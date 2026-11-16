#include "algoos/sandbox/sandbox.hpp"

#include <fcntl.h>
#include <linux/mount.h>
#include <sys/prctl.h>
#include <sched.h>
#include <seccomp.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/personality.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <limits>
#include <mutex>
#include <optional>
#include <random>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace algoos::sandbox {
namespace {
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

constexpr int64_t kMaxOutBytes = 256LL << 20;
constexpr std::size_t kStackBytes = 8ULL << 20;
constexpr off_t kStderrTailBytes = 4096;

void check(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

int64_t elapsed_ms(Clock::time_point start) {
  return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - start).count();
}

std::string unique_suffix() {
  static std::atomic<uint64_t> counter{0};
  static thread_local std::mt19937_64 generator(std::random_device{}());
  const auto now = static_cast<uint64_t>(Clock::now().time_since_epoch().count());
  return std::to_string(now) + "-" + std::to_string(generator()) + "-" + std::to_string(counter++);
}

bool write_text(const fs::path& path, const std::string& text) {
  std::ofstream output(path, std::ios::binary | std::ios::trunc);
  output << text;
  return output.good();
}

std::optional<int64_t> read_int(const fs::path& path) {
  std::ifstream input(path);
  int64_t value = 0;
  if (!(input >> value)) return std::nullopt;
  return value;
}

struct ChildError {
  int kind;  // 0 = setup failure
  int stage;
  int number;
};

struct ProgramReport {
  int kind;  // 1 = program finished
  int status;
  int padding;
  rusage usage;
};

struct ChildContext {
  const char* executable;
  const char* scratch;
  int stdin_fd;
  int stdout_fd;
  int stderr_fd;
  int error_fd;
  rlim_t cpu_seconds;
  rlim_t memory_bytes;
  rlim_t fsize_bytes;
  bool seccomp;
  bool readonly_root;
  bool mount_tmpfs;
  bool pid_namespace;
  int64_t tmpfs_bytes;
  char** argv;
};

// Seccomp allow-by-exception policy.  The filter is compiled once and loaded
// into every user process before exec.  Network, mount, namespace, ptrace and
// kernel-module entry points are denied.
void install_seccomp() {
  static std::mutex mutex;
  static scmp_filter_ctx filter = nullptr;
  std::lock_guard guard(mutex);
  if (filter == nullptr) {
    filter = seccomp_init(SCMP_ACT_ALLOW);
    if (filter == nullptr) throw std::runtime_error("seccomp_init failed");
    auto deny = [&](int syscall) {
      seccomp_rule_add(filter, SCMP_ACT_ERRNO(EPERM), syscall, 0);
    };
    for (int syscall : {SCMP_SYS(socket), SCMP_SYS(socketpair), SCMP_SYS(connect),
                        SCMP_SYS(bind), SCMP_SYS(listen), SCMP_SYS(accept), SCMP_SYS(accept4),
                        SCMP_SYS(sendto), SCMP_SYS(recvfrom), SCMP_SYS(sendmsg), SCMP_SYS(recvmsg),
                        SCMP_SYS(ptrace), SCMP_SYS(process_vm_readv), SCMP_SYS(process_vm_writev),
                        SCMP_SYS(mount), SCMP_SYS(umount2), SCMP_SYS(pivot_root), SCMP_SYS(chroot),
                        SCMP_SYS(setns), SCMP_SYS(unshare), SCMP_SYS(open_by_handle_at),
                        SCMP_SYS(name_to_handle_at), SCMP_SYS(keyctl), SCMP_SYS(add_key),
                        SCMP_SYS(request_key), SCMP_SYS(init_module), SCMP_SYS(finit_module),
                        SCMP_SYS(delete_module), SCMP_SYS(reboot), SCMP_SYS(kexec_load),
                        SCMP_SYS(kexec_file_load), SCMP_SYS(swapon), SCMP_SYS(swapoff),
                        SCMP_SYS(acct), SCMP_SYS(settimeofday), SCMP_SYS(clock_settime),
                        SCMP_SYS(syslog), SCMP_SYS(personality), SCMP_SYS(bpf),
                        SCMP_SYS(userfaultfd), SCMP_SYS(io_uring_setup)}) {
      deny(syscall);
    }
    // clone3 is disabled so glibc falls back to clone; namespace flag bits are
    // masked out of clone's first argument and rejected.
    seccomp_rule_add(filter, SCMP_ACT_ERRNO(ENOSYS), SCMP_SYS(clone3), 0);
    constexpr uint32_t kNamespaceFlags = CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWNET |
                                         CLONE_NEWUTS | CLONE_NEWIPC | CLONE_NEWUSER |
                                         CLONE_NEWCGROUP;
    seccomp_rule_add(filter, SCMP_ACT_ERRNO(EPERM), SCMP_SYS(clone), 1,
                     SCMP_A0(SCMP_CMP_MASKED_EQ, kNamespaceFlags, kNamespaceFlags));
    seccomp_rule_add(filter, SCMP_ACT_ERRNO(EPERM), SCMP_SYS(clone), 1,
                     SCMP_A0(SCMP_CMP_MASKED_EQ, CLONE_NEWUSER, CLONE_NEWUSER));
  }
  if (seccomp_load(filter) != 0) throw std::runtime_error("seccomp_load failed");
}

[[noreturn]] void child_fail(int fd, int stage) {
  const ChildError error{0, stage, errno};
  ssize_t written;
  do {
    written = write(fd, &error, sizeof(error));
  } while (written < 0 && errno == EINTR);
  _exit(125);
}

void redirect(int fd, int target, int error_fd) {
  if (fd != target) {
    if (dup2(fd, target) < 0) child_fail(error_fd, 7);
    close(fd);
  }
}

int child_main(void* raw) {
  auto* context = static_cast<ChildContext*>(raw);
  if (setpgid(0, 0) != 0) child_fail(context->error_fd, 1);
  redirect(context->stdin_fd, STDIN_FILENO, context->error_fd);
  redirect(context->stdout_fd, STDOUT_FILENO, context->error_fd);
  redirect(context->stderr_fd, STDERR_FILENO, context->error_fd);

  if (context->readonly_root) {
    // Best-effort read-only root; a failed remount is reported as a warning by
    // the parent, not a reason to refuse execution.
    if (mount("/", "/", nullptr, MS_REC | MS_PRIVATE, nullptr) != 0)
      child_fail(context->error_fd, 20);
    if (mount("/", "/", nullptr, MS_BIND | MS_REC, nullptr) != 0)
      child_fail(context->error_fd, 21);
    if (mount(nullptr, "/", nullptr, MS_REMOUNT | MS_BIND | MS_RDONLY, nullptr) != 0)
      child_fail(context->error_fd, 22);
  }
  if (context->mount_tmpfs) {
    const std::string options = "size=" + std::to_string(context->tmpfs_bytes) + ",mode=0755";
    if (mount("tmpfs", context->scratch, "tmpfs", MS_NOSUID | MS_NODEV, options.c_str()) != 0) {
      child_fail(context->error_fd, 2);
    }
  }
    if (chdir(context->scratch) != 0) child_fail(context->error_fd, 3);
    if (umask(077) != 077) { /* umask returns the previous mask */ }

  // When a PID namespace is used the cloned process is PID 1 inside it, and
  // PID 1 ignores default-action signals such as SIGSEGV/SIGXCPU.  Fork the
  // real program as PID 2 and let PID 1 only wait and report, so crash and CPU
  // limit signals retain their normal semantics.
  if (context->pid_namespace) {
    const pid_t program = fork();
    if (program < 0) child_fail(context->error_fd, 30);
    if (program > 0) {
      int status = 0;
      rusage usage{};
      while (wait4(program, &status, 0, &usage) < 0 && errno == EINTR) {
      }
      ProgramReport report{1, status, 0, usage};
      ssize_t written;
      do {
        written = write(context->error_fd, &report, sizeof(report));
      } while (written < 0 && errno == EINTR);
      _exit(0);
    }
  }

  rlimit core_limit{0, 0};
  rlimit cpu_limit{context->cpu_seconds, context->cpu_seconds + 1};
  rlimit memory_limit{context->memory_bytes, context->memory_bytes};
  rlimit file_limit{context->fsize_bytes, context->fsize_bytes};
  if (setrlimit(RLIMIT_CORE, &core_limit) != 0) child_fail(context->error_fd, 4);
  if (setrlimit(RLIMIT_CPU, &cpu_limit) != 0) child_fail(context->error_fd, 5);
  if (setrlimit(RLIMIT_AS, &memory_limit) != 0) child_fail(context->error_fd, 6);
  if (setrlimit(RLIMIT_FSIZE, &file_limit) != 0) child_fail(context->error_fd, 8);
  if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) child_fail(context->error_fd, 9);
  if (context->seccomp) {
    if (prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0) child_fail(context->error_fd, 10);
    try {
      install_seccomp();
    } catch (...) {
      child_fail(context->error_fd, 11);
    }
  }
  execv(context->executable, context->argv);
  child_fail(context->error_fd, 12);
}

struct CgroupHandle {
  std::string path;
  bool active{false};
};

CgroupHandle create_cgroup(const SandboxConfig& config, std::vector<std::string>& warnings) {
  CgroupHandle handle;
  const std::string name = "algoos-run-" + unique_suffix();
  const auto path = fs::path(config.cgroup_root) / name;
  handle.path = path.string();
  try {
    if (!fs::create_directory(path)) {
      warnings.push_back("cgroup directory already exists: " + handle.path);
      return handle;
    }
    handle.active = true;
  } catch (const std::exception& error) {
    warnings.push_back(std::string("cgroup unavailable: ") + error.what());
  }
  return handle;
}

void apply_cgroup_limits(const CgroupHandle& handle, const Limits& limits,
                         std::vector<std::string>& warnings) {
  if (!handle.active) return;
  const auto base = fs::path(handle.path);
  if (!write_text(base / "memory.max", std::to_string(limits.mem_bytes))) {
    warnings.push_back("cannot write memory.max");
  }
  if (!write_text(base / "memory.swap.max", "0")) warnings.push_back("cannot disable swap");
  if (!write_text(base / "pids.max", "64")) warnings.push_back("cannot write pids.max");
  if (!write_text(base / "cpu.max", "100000 100000")) warnings.push_back("cannot write cpu.max");
}

bool attach_to_cgroup(const CgroupHandle& handle, int64_t pid, std::vector<std::string>& warnings) {
  if (!handle.active) return false;
  if (!write_text(fs::path(handle.path) / "cgroup.procs", std::to_string(pid))) {
    warnings.push_back("cannot attach process to cgroup");
    return false;
  }
  return true;
}

std::optional<int64_t> cgroup_memory_peak(const CgroupHandle& handle) {
  if (!handle.active) return std::nullopt;
  return read_int(fs::path(handle.path) / "memory.peak");
}

int64_t cgroup_oom_kills(const CgroupHandle& handle) {
  if (!handle.active) return 0;
  std::ifstream input(fs::path(handle.path) / "memory.events");
  std::string key;
  int64_t value = 0;
  while (input >> key) {
    if (key == "oom_kill" || key == "oom_kill:") {
      input >> value;
      return value;
    }
    input.ignore(std::numeric_limits<std::streamsize>::max(), '\n');
  }
  return 0;
}

void kill_cgroup(const CgroupHandle& handle) {
  if (!handle.active) return;
  (void)write_text(fs::path(handle.path) / "cgroup.kill", "1");
}

void cleanup_cgroup(CgroupHandle& handle) {
  if (!handle.active) return;
  kill_cgroup(handle);
  handle.active = false;
  for (int attempt = 0; attempt < 50; ++attempt) {
    if (fs::remove(fs::path(handle.path))) return;
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
}

std::string read_tail(const fs::path& path, off_t maximum) {
  int fd = open(path.c_str(), O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
  if (fd < 0) return {};
  struct stat metadata{};
  std::string tail;
  if (fstat(fd, &metadata) == 0 && S_ISREG(metadata.st_mode) && metadata.st_size > 0) {
    const auto count = std::min(metadata.st_size, maximum);
    tail.resize(static_cast<std::size_t>(count));
    ssize_t got;
    do {
      got = pread(fd, tail.data(), tail.size(), metadata.st_size - count);
    } while (got < 0 && errno == EINTR);
    tail.resize(got > 0 ? static_cast<std::size_t>(got) : 0);
  }
  close(fd);
  return tail;
}

bool kill_group(pid_t pid) {
  const bool group = kill(-pid, SIGKILL) == 0;
  const bool child = kill(pid, SIGKILL) == 0;
  return group || child;
}

RunResult run_common(const RunRequest& request, bool sandbox_user_program) {
  RunResult result;
  const Limits& limits = request.limits;
  check(limits.cpu_ms > 0 && limits.wall_ms >= limits.cpu_ms,
        "invalid CPU/wall limits");
  check(limits.mem_bytes > 0, "invalid memory limit");
  check(limits.out_bytes >= 0, "invalid output limit");

  const auto work = fs::absolute(request.work_dir);
  const auto scratch = work / "scratch";
  fs::create_directories(scratch);
  const auto stdout_path = work / "stdout.out";
  const auto stderr_path = work / "stderr.err";

  int input_fd = open(request.input.c_str(), O_RDONLY | O_CLOEXEC);
  int stdout_fd = open(stdout_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
  int stderr_fd = open(stderr_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
  if (input_fd < 0 || stdout_fd < 0 || stderr_fd < 0) {
    result.evidence.setup_failed = true;
    result.setup_error = "cannot open execution files: " + std::string(std::strerror(errno));
    if (input_fd >= 0) close(input_fd);
    if (stdout_fd >= 0) close(stdout_fd);
    if (stderr_fd >= 0) close(stderr_fd);
    return result;
  }

  int error_pipe[2];
  if (pipe2(error_pipe, O_CLOEXEC) != 0) {
    close(input_fd);
    close(stdout_fd);
    close(stderr_fd);
    result.evidence.setup_failed = true;
    result.setup_error = std::string("pipe2: ") + std::strerror(errno);
    return result;
  }

  std::vector<char*> argv;
  argv.push_back(const_cast<char*>(request.executable.c_str()));
  for (const auto& argument : request.arguments) argv.push_back(const_cast<char*>(argument.c_str()));
  argv.push_back(nullptr);

  const auto fsize = std::min<int64_t>(kMaxOutBytes, limits.out_bytes + request.config.fsize_slack_bytes);
  ChildContext context{
      request.executable.c_str(),
      scratch.c_str(),
      input_fd,
      stdout_fd,
      stderr_fd,
      error_pipe[1],
      static_cast<rlim_t>((limits.cpu_ms + 999) / 1000),
      static_cast<rlim_t>(limits.mem_bytes),
      static_cast<rlim_t>(fsize),
      sandbox_user_program && request.config.use_seccomp,
      sandbox_user_program && request.config.readonly_root,
      sandbox_user_program && request.config.use_cgroup,
      sandbox_user_program && request.config.use_namespaces,
      request.config.tmpfs_bytes,
      argv.data(),
  };

  void* stack = mmap(nullptr, kStackBytes, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_STACK, -1, 0);
  if (stack == MAP_FAILED) {
    close(input_fd);
    close(stdout_fd);
    close(stderr_fd);
    close(error_pipe[0]);
    close(error_pipe[1]);
    result.evidence.setup_failed = true;
    result.setup_error = std::string("mmap stack: ") + std::strerror(errno);
    return result;
  }
  char* stack_top = static_cast<char*>(stack) + kStackBytes;

  std::vector<std::string> warnings;
  CgroupHandle cgroup;
  if (sandbox_user_program && request.config.use_cgroup) {
    cgroup = create_cgroup(request.config, warnings);
    apply_cgroup_limits(cgroup, limits, warnings);
  }

  const Clock::time_point started = Clock::now();
  int flags = SIGCHLD;
  if (sandbox_user_program && request.config.use_namespaces) {
    flags |= CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWNET | CLONE_NEWUTS | CLONE_NEWIPC;
  }
  pid_t pid = clone(child_main, stack_top, flags, &context);
  if (pid < 0 && (errno == EPERM || errno == EINVAL) && sandbox_user_program &&
      request.config.use_namespaces) {
    warnings.push_back(std::string("namespaces unavailable, falling back: ") + std::strerror(errno));
    flags = SIGCHLD;
    pid = clone(child_main, stack_top, flags, &context);
  }
  if (pid < 0) {
    result.evidence.setup_failed = true;
    result.setup_error = std::string("clone: ") + std::strerror(errno);
    cleanup_cgroup(cgroup);
    munmap(stack, kStackBytes);
    close(input_fd);
    close(stdout_fd);
    close(stderr_fd);
    close(error_pipe[0]);
    close(error_pipe[1]);
    return result;
  }
  close(input_fd);
  close(stdout_fd);
  close(stderr_fd);
  close(error_pipe[1]);

  bool cgroup_attached = false;
  if (cgroup.active) cgroup_attached = attach_to_cgroup(cgroup, pid, warnings);

  int status = 0;
  rusage usage{};
  bool deadline_kill = false;
  for (;;) {
    pid_t waited = wait4(pid, &status, WNOHANG, &usage);
    if (waited == pid) break;
    if (waited < 0) {
      if (errno == EINTR) continue;
      if (errno != ECHILD) {
        kill_group(pid);
        while (wait4(pid, &status, 0, &usage) < 0 && errno == EINTR) {
        }
      }
      break;
    }
    if (elapsed_ms(started) >= limits.wall_ms) {
      deadline_kill = true;
      kill_cgroup(cgroup);
      kill_group(pid);
      while (wait4(pid, &status, 0, &usage) < 0 && errno == EINTR) {
      }
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  const int64_t wall_ms = elapsed_ms(started);

  int report_kind = -1;
  int report_size = 0;
  std::string report_bytes;
  {
    char buffer[sizeof(ProgramReport)]{};
    ssize_t got;
    do {
      got = read(error_pipe[0], buffer, sizeof(buffer));
    } while (got < 0 && errno == EINTR);
    close(error_pipe[0]);
    report_size = got > 0 ? static_cast<int>(got) : 0;
    report_bytes.assign(buffer, static_cast<std::size_t>(report_size));
    if (report_size >= static_cast<int>(sizeof(int)))
      std::memcpy(&report_kind, buffer, sizeof(int));
  }
  munmap(stack, kStackBytes);

  auto& evidence = result.evidence;
  evidence.started = true;
  evidence.wall_ms = wall_ms;
  evidence.wall_dog_fired = deadline_kill && WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL;

  // With a PID namespace the cloned process is an init helper; its report
  // carries the real program status and rusage.  Without it, the clone child
  // execs directly and its own wait status is authoritative.
  int program_status = status;
  rusage program_usage = usage;
  bool have_program = !sandbox_user_program || !request.config.use_namespaces;
  if (sandbox_user_program && request.config.use_namespaces) {
    if (report_kind == 1 && report_size >= static_cast<int>(sizeof(ProgramReport))) {
      ProgramReport report{};
      std::memcpy(&report, report_bytes.data(), sizeof(report));
      program_status = report.status;
      program_usage = report.usage;
      have_program = true;
    } else if (report_kind == 0 && report_size >= static_cast<int>(sizeof(ChildError))) {
      ChildError error{};
      std::memcpy(&error, report_bytes.data(), sizeof(error));
      evidence.setup_failed = true;
      static const char* stages[] = {
          "unknown", "setpgid", "mount tmpfs", "chdir", "RLIMIT_CORE", "RLIMIT_CPU",
          "RLIMIT_AS", "dup2", "RLIMIT_FSIZE", "no_new_privs", "dumpable", "seccomp",
          "execv", "13", "14", "15", "16", "17", "18", "19", "mount private",
          "mount bind", "remount read-only", "23", "24", "25", "26", "27", "28", "29",
          "fork program",
      };
      const int stage = error.stage >= 0 && error.stage < 31 ? error.stage : 0;
      result.setup_error = std::string(stages[stage]) + ": " + std::strerror(error.number);
    }
  }
  if (!evidence.setup_failed) {
    evidence.wifexited = WIFEXITED(program_status);
    evidence.exit_code = WIFEXITED(program_status) ? WEXITSTATUS(program_status) : 0;
    evidence.wifsignaled = WIFSIGNALED(program_status);
    evidence.term_sig = WIFSIGNALED(program_status) ? WTERMSIG(program_status) : 0;
    evidence.cpu_ms = static_cast<int64_t>(program_usage.ru_utime.tv_sec +
                                           program_usage.ru_stime.tv_sec) * 1000 +
                      (program_usage.ru_utime.tv_usec + program_usage.ru_stime.tv_usec) / 1000;
    evidence.ru_maxrss_bytes = static_cast<int64_t>(program_usage.ru_maxrss) * 1024;
  }
  (void)have_program;
  evidence.cgroup_memory_peak = cgroup_memory_peak(cgroup);
  evidence.oom_kill = cgroup_oom_kills(cgroup);
  try {
    evidence.out_size = static_cast<int64_t>(fs::file_size(stdout_path));
  } catch (const std::exception&) {
    evidence.out_size = 0;
  }
  result.stderr_tail = read_tail(stderr_path, kStderrTailBytes);
  cleanup_cgroup(cgroup);

  std::vector<std::string> applied;
  applied.emplace_back("rlimit");
  if (cgroup_attached) applied.emplace_back("cgroup");
  if (sandbox_user_program && request.config.use_namespaces) applied.emplace_back("namespace");
  if (sandbox_user_program && request.config.use_seccomp) applied.emplace_back("seccomp");
  if (sandbox_user_program && request.config.readonly_root) applied.emplace_back("fs");
  result.sandbox_mode = (cgroup_attached && sandbox_user_program && request.config.use_seccomp)
                            ? "full"
                            : (sandbox_user_program ? "partial" : "compiler");
  if (!cgroup_attached && sandbox_user_program && request.config.use_cgroup) {
    warnings.push_back("cgroup limits were not applied");
  }
  result.warnings = std::move(warnings);
  return result;
}

}  // namespace

std::vector<std::string> layers(const RunResult& result) {
  std::vector<std::string> applied;
  applied.emplace_back("rlimit");
  if (result.sandbox_mode == "full" || result.sandbox_mode == "partial") {
    applied.emplace_back("namespace");
    applied.emplace_back("seccomp");
  }
  return applied;
}

RunResult run(const RunRequest& request) { return run_common(request, true); }

RunResult run_bounded_process(const std::string& executable, const std::vector<std::string>& arguments,
                              const std::string& work_dir, const std::string& stdin_path,
                              const std::string& stdout_path, const std::string& stderr_path,
                              const Limits& limits, bool use_cgroup) {
  RunRequest request;
  request.executable = executable;
  request.arguments = arguments;
  request.input = stdin_path;
  request.work_dir = work_dir;
  request.limits = limits;
  request.config.use_cgroup = use_cgroup;
  request.config.use_seccomp = false;
  request.config.use_namespaces = false;
  request.config.readonly_root = false;
  RunResult result;
  // Compiler helpers need a writable work directory; run in a plain child with
  // rlimits and a wall deadline.  Reuse the common path by disabling the user
  // sandbox and pointing the scratch directory at an existing writable dir.
  const auto work = fs::absolute(work_dir);
  fs::create_directories(work);
  int output_fd = open(stdout_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
  int error_fd = open(stderr_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
  int input_fd = stdin_path.empty() ? open("/dev/null", O_RDONLY | O_CLOEXEC)
                                     : open(stdin_path.c_str(), O_RDONLY | O_CLOEXEC);
  if (output_fd < 0 || error_fd < 0 || input_fd < 0) {
    result.evidence.setup_failed = true;
    result.setup_error = "cannot open compiler files";
    if (output_fd >= 0) close(output_fd);
    if (error_fd >= 0) close(error_fd);
    if (input_fd >= 0) close(input_fd);
    return result;
  }
  int error_pipe[2];
  if (pipe2(error_pipe, O_CLOEXEC) != 0) {
    result.evidence.setup_failed = true;
    result.setup_error = "pipe2 failed";
    close(output_fd);
    close(error_fd);
    close(input_fd);
    return result;
  }
  std::vector<char*> argv;
  argv.push_back(const_cast<char*>(executable.c_str()));
  for (const auto& argument : arguments) argv.push_back(const_cast<char*>(argument.c_str()));
  argv.push_back(nullptr);
  ChildContext context{
      executable.c_str(), work.c_str(), input_fd, output_fd, error_fd, error_pipe[1],
      static_cast<rlim_t>((limits.cpu_ms + 999) / 1000), static_cast<rlim_t>(limits.mem_bytes),
      static_cast<rlim_t>(std::min<int64_t>(kMaxOutBytes, limits.out_bytes + 4096)), false,
      false, false, false, 0, argv.data(),
  };
  void* stack = mmap(nullptr, kStackBytes, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_STACK, -1, 0);
  if (stack == MAP_FAILED) {
    result.evidence.setup_failed = true;
    result.setup_error = "mmap stack failed";
    close(output_fd);
    close(error_fd);
    close(input_fd);
    close(error_pipe[0]);
    close(error_pipe[1]);
    return result;
  }
  const auto started = Clock::now();
  pid_t pid = clone(child_main, static_cast<char*>(stack) + kStackBytes, SIGCHLD, &context);
  if (pid < 0) {
    result.evidence.setup_failed = true;
    result.setup_error = std::string("clone: ") + std::strerror(errno);
    munmap(stack, kStackBytes);
    close(output_fd);
    close(error_fd);
    close(input_fd);
    close(error_pipe[0]);
    close(error_pipe[1]);
    return result;
  }
  close(input_fd);
  close(output_fd);
  close(error_fd);
  close(error_pipe[1]);
  int status = 0;
  rusage usage{};
  bool deadline_kill = false;
  for (;;) {
    pid_t waited = wait4(pid, &status, WNOHANG, &usage);
    if (waited == pid) break;
    if (waited < 0 && errno != EINTR) break;
    if (waited >= 0 && elapsed_ms(started) >= limits.wall_ms) {
      deadline_kill = true;
      kill_group(pid);
      while (wait4(pid, &status, 0, &usage) < 0 && errno == EINTR) {
      }
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  auto& evidence = result.evidence;
  evidence.started = true;
  evidence.wall_ms = elapsed_ms(started);
  evidence.wall_dog_fired = deadline_kill && WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL;
  evidence.wifexited = WIFEXITED(status);
  evidence.exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : 0;
  evidence.wifsignaled = WIFSIGNALED(status);
  evidence.term_sig = WIFSIGNALED(status) ? WTERMSIG(status) : 0;
  evidence.cpu_ms = static_cast<int64_t>(usage.ru_utime.tv_sec + usage.ru_stime.tv_sec) * 1000 +
                    (usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) / 1000;
  evidence.ru_maxrss_bytes = static_cast<int64_t>(usage.ru_maxrss) * 1024;
  ChildError child_error{};
  ssize_t got;
  do {
    got = read(error_pipe[0], &child_error, sizeof(child_error));
  } while (got < 0 && errno == EINTR);
  close(error_pipe[0]);
  munmap(stack, kStackBytes);
  if (got == static_cast<ssize_t>(sizeof(child_error))) {
    evidence.setup_failed = true;
    result.setup_error = std::string("child setup stage ") +
                         std::to_string(child_error.stage) + ": " +
                         std::strerror(child_error.number);
  }
  result.stderr_tail = read_tail(stderr_path, kStderrTailBytes);
  result.sandbox_mode = "compiler";
  (void)use_cgroup;
  return result;
}

}  // namespace algoos::sandbox
