// eval_server.cpp —— 按 CoreUML.md 实现的 C++ 常驻评测服务端（最小可用版）。
//
// 组件对应：
//   EvalServer      监听 127.0.0.1，接受连接
//   RequestQueue    有界队列 + 背压
//   ThreadPool      N 个 worker
//   JudgeWorker     处理单个 EvalRequest
//   JudgeCore       fork / exec / wait4 + ResourceLimiter
//   Comparator      输出比对
//   Verdict         AC / WA / TLE / MLE / RE / CE / SE
//
// 边界：
//   - 编译由 Python CompileScheduler 通过 g++ 子进程完成，本服务端只评测已编译的 bin。
//   - 每个连接一次请求、一次响应；4 字节大端长度前缀 + JSON（CoreUML 第 7 节）。
//   - 时间/空间/崩溃原始数据由 wait4/rusage 取得；提交级最终结果由 Python 聚合。
//
// 构建：
//   make -C judge/native
// 运行：
//   judge/native/eval_server --port 9000 --workers 4

#include <json-c/json.h>

#include <algorithm>
#include <cerrno>
#include <charconv>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstring>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <optional>
#include <poll.h>
#include <string>
#include <string_view>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <thread>
#include <arpa/inet.h>
#include <condition_variable>
#include <fcntl.h>
#include <netinet/in.h>
#include <unistd.h>
#include <vector>

namespace fs = std::filesystem;

namespace {

// ---- 空间上限：先划定，再读取 -------------------------------------------------
constexpr std::size_t kMaxMessageBytes = 1ULL << 20;      // 1 MiB
constexpr std::size_t kMaxAnswerBytes = 64ULL << 20;      // 64 MiB
constexpr std::size_t kMaxStderrTailBytes = 4ULL << 10;   // 4 KiB

constexpr std::int64_t kMaxCpuMs = 3'600'000;             // 1 小时
constexpr std::int64_t kMaxMemBytes = 8LL << 30;          // 8 GiB
constexpr std::int64_t kMaxOutBytes = 256LL << 20;        // 256 MiB
constexpr std::int64_t kOutputFsizeSlackBytes = 4LL << 10; // 4 KiB
constexpr std::int64_t kMaxWallMs = 3'600'000;

constexpr int kDefaultPort = 9000;
constexpr int kDefaultWorkers = 4;
constexpr std::size_t kDefaultQueueCapacity = 64;
constexpr int kSocketTimeoutSeconds = 5;

volatile std::sig_atomic_t g_stop = 0;

void handle_signal(int) { g_stop = 1; }

// ---- 基础类型 -----------------------------------------------------------------

enum class Verdict { AC, WA, TLE, MLE, RE, CE, SE };

std::string verdict_name(Verdict verdict) {
  switch (verdict) {
    case Verdict::AC:
      return "AC";
    case Verdict::WA:
      return "WA";
    case Verdict::TLE:
      return "TLE";
    case Verdict::MLE:
      return "MLE";
    case Verdict::RE:
      return "RE";
    case Verdict::CE:
      return "CE";
    case Verdict::SE:
      return "SE";
  }
  return "SE";
}

struct Limits {
  std::int64_t cpu_ms = 1000;
  std::int64_t mem_bytes = 256LL << 20;
  std::int64_t out_bytes = 64LL << 20;
  std::int64_t wall_ms = 0;  // 0 表示跟随 cpu_ms
};

struct EvalRequest {
  int ver = 1;
  std::string file_id;
  std::string problem_id;
  std::string test_id;
  std::string bin_path;
  std::string input_path;
  std::string answer_path;
  std::string result_dir;
  std::string compare_mode = "common";
  Limits limits;
};

struct JudgeResult {
  std::string file_id;
  std::string problem_id;
  std::string test_id;
  Verdict verdict = Verdict::SE;
  std::int64_t cpu_ms = 0;
  std::int64_t mem_kb = 0;
  std::int64_t out_bytes = 0;
  int exit_code = -1;
  int signal = 0;
  bool wall_timeout = false;
  std::string stderr_tail;
  std::string error;
};

struct RunStat {
  int exit_code = -1;
  int term_signal = 0;
  bool timed_out = false;
  std::int64_t cpu_ms = 0;
  std::int64_t mem_kb = 0;
  std::string setup_error;
};

// ---- 小工具 -------------------------------------------------------------------

bool read_file_bounded(const fs::path& path, std::size_t max_bytes,
                       std::string& out, bool& truncated, std::string& error) {
  truncated = false;
  out.clear();

  std::ifstream in(path, std::ios::binary);
  if (!in) {
    error = "无法打开文件: " + path.string();
    return false;
  }

  std::vector<char> buffer(64 * 1024);
  while (in) {
    in.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
    const std::streamsize got = in.gcount();
    if (got <= 0) {
      break;
    }
    const auto chunk = static_cast<std::size_t>(got);
    if (out.size() + chunk > max_bytes) {
      out.append(buffer.data(), max_bytes - out.size());
      truncated = true;
      break;
    }
    out.append(buffer.data(), chunk);
  }

  if (in.bad()) {
    error = "读取文件失败: " + path.string();
    return false;
  }
  return true;
}

std::string read_tail(const fs::path& path, std::size_t max_bytes) {
  std::ifstream in(path, std::ios::binary | std::ios::ate);
  if (!in) {
    return {};
  }
  const std::streamoff end = in.tellg();
  if (end <= 0) {
    return {};
  }
  const auto size = static_cast<std::size_t>(end);
  const auto start = size > max_bytes ? size - max_bytes : 0;
  in.seekg(static_cast<std::streamoff>(start));
  std::string out(size - start, '\0');
  in.read(out.data(), static_cast<std::streamsize>(out.size()));
  out.resize(static_cast<std::size_t>(in.gcount()));
  return out;
}

// 与 judge/checker.py 一致：只统一换行符，其余逐字节比较。
std::string normalize_newlines(std::string_view text) {
  std::string out;
  out.reserve(text.size());
  for (std::size_t i = 0; i < text.size(); ++i) {
    if (text[i] == '\r') {
      if (i + 1 < text.size() && text[i + 1] == '\n') {
        ++i;
      }
      out.push_back('\n');
    } else {
      out.push_back(text[i]);
    }
  }
  return out;
}

// trim_common：统一换行后，去掉每行末尾空格/制表符和末尾空行。
std::string trim_common(std::string_view text) {
  const std::string normalized = normalize_newlines(text);
  std::vector<std::string> lines;
  std::size_t start = 0;
  while (true) {
    const std::size_t pos = normalized.find('\n', start);
    if (pos == std::string::npos) {
      lines.push_back(normalized.substr(start));
      break;
    }
    lines.push_back(normalized.substr(start, pos - start));
    start = pos + 1;
  }
  for (std::string& line : lines) {
    while (!line.empty() &&
           (line.back() == ' ' || line.back() == '\t' || line.back() == '\r')) {
      line.pop_back();
    }
  }
  while (!lines.empty() && lines.back().empty()) {
    lines.pop_back();
  }
  std::string out;
  for (std::size_t i = 0; i < lines.size(); ++i) {
    if (i != 0) {
      out.push_back('\n');
    }
    out += lines[i];
  }
  return out;
}

// ---- JSON 读写 ----------------------------------------------------------------

bool json_get_string(json_object* object, const char* key, std::string& out,
                     bool required, std::string& error) {
  json_object* value = nullptr;
  if (!json_object_object_get_ex(object, key, &value)) {
    if (required) {
      error = std::string("缺少字段: ") + key;
      return false;
    }
    return true;
  }
  if (!json_object_is_type(value, json_type_string)) {
    error = std::string("字段类型错误: ") + key;
    return false;
  }
  const char* text = json_object_get_string(value);
  out = text != nullptr ? text : "";
  return true;
}

bool json_get_int64(json_object* object, const char* key, std::int64_t& out,
                    bool required, std::string& error) {
  json_object* value = nullptr;
  if (!json_object_object_get_ex(object, key, &value)) {
    if (required) {
      error = std::string("缺少字段: ") + key;
      return false;
    }
    return true;
  }
  if (!json_object_is_type(value, json_type_int)) {
    error = std::string("字段类型错误: ") + key;
    return false;
  }
  out = json_object_get_int64(value);
  return true;
}

bool parse_limits(json_object* root, Limits& limits, std::string& error) {
  json_object* object = nullptr;
  if (json_object_object_get_ex(root, "limits", &object)) {
    if (!json_object_is_type(object, json_type_object)) {
      error = "limits 必须是对象";
      return false;
    }
    if (!json_get_int64(object, "cpu_ms", limits.cpu_ms, false, error) ||
        !json_get_int64(object, "mem_bytes", limits.mem_bytes, false, error) ||
        !json_get_int64(object, "out_bytes", limits.out_bytes, false, error) ||
        !json_get_int64(object, "wall_ms", limits.wall_ms, false, error)) {
      return false;
    }
  }
  if (limits.wall_ms == 0) {
    limits.wall_ms = limits.cpu_ms;
  }
  if (limits.cpu_ms < 1 || limits.cpu_ms > kMaxCpuMs) {
    error = "limits.cpu_ms 超出范围";
    return false;
  }
  if (limits.mem_bytes < 1 || limits.mem_bytes > kMaxMemBytes) {
    error = "limits.mem_bytes 超出范围";
    return false;
  }
  if (limits.out_bytes < 0 || limits.out_bytes > kMaxOutBytes) {
    error = "limits.out_bytes 超出范围";
    return false;
  }
  if (limits.wall_ms < 1 || limits.wall_ms > kMaxWallMs) {
    error = "limits.wall_ms 超出范围";
    return false;
  }
  return true;
}

bool parse_request(const std::string& payload, EvalRequest& request,
                   std::string& error) {
  json_object* root = json_tokener_parse(payload.c_str());
  if (root == nullptr) {
    error = "JSON 解析失败";
    return false;
  }
  if (!json_object_is_type(root, json_type_object)) {
    error = "请求必须是 JSON 对象";
    json_object_put(root);
    return false;
  }

  std::int64_t protocol_version = 1;
  bool ok = true;
  ok = ok && json_get_int64(root, "ver", protocol_version, false, error);
  if (!ok) {
    json_object_put(root);
    return false;
  }
  if (protocol_version != 1) {
    error = "不支持的协议版本";
    json_object_put(root);
    return false;
  }
  request.ver = 1;

  ok = ok && json_get_string(root, "file_id", request.file_id, true, error);
  ok = ok && json_get_string(root, "problem_id", request.problem_id, false, error);
  ok = ok && json_get_string(root, "test_id", request.test_id, true, error);
  ok = ok && json_get_string(root, "bin_path", request.bin_path, true, error);
  ok = ok && json_get_string(root, "input_path", request.input_path, true, error);
  ok = ok && json_get_string(root, "answer_path", request.answer_path, true, error);
  ok = ok && json_get_string(root, "result_dir", request.result_dir, true, error);
  ok = ok && json_get_string(root, "compare_mode", request.compare_mode, false, error);
  ok = ok && parse_limits(root, request.limits, error);
  json_object_put(root);

  if (!ok) {
    return false;
  }
  if (request.file_id.empty() || request.test_id.empty()) {
    error = "file_id/test_id 不能为空";
    return false;
  }
  if (request.compare_mode != "common" && request.compare_mode != "special") {
    error = "compare_mode 只支持 common 或 special";
    return false;
  }
  return true;
}

std::string result_to_json(const JudgeResult& result) {
  json_object* root = json_object_new_object();
  json_object_object_add(root, "ver", json_object_new_int(1));
  json_object_object_add(root, "file_id",
                         json_object_new_string(result.file_id.c_str()));
  json_object_object_add(root, "problem_id",
                         json_object_new_string(result.problem_id.c_str()));
  json_object_object_add(root, "test_id",
                         json_object_new_string(result.test_id.c_str()));
  json_object_object_add(root, "verdict",
                         json_object_new_string(verdict_name(result.verdict).c_str()));
  json_object_object_add(root, "cpu_ms", json_object_new_int64(result.cpu_ms));
  json_object_object_add(root, "mem_kb", json_object_new_int64(result.mem_kb));
  json_object_object_add(root, "out_bytes",
                         json_object_new_int64(result.out_bytes));
  json_object_object_add(root, "exit_code", json_object_new_int(result.exit_code));
  json_object_object_add(root, "signal", json_object_new_int(result.signal));
  json_object_object_add(root, "wall_timeout",
                         json_object_new_boolean(result.wall_timeout));
  json_object_object_add(root, "stderr_tail",
                         json_object_new_string(result.stderr_tail.c_str()));
  json_object_object_add(root, "error", json_object_new_string(result.error.c_str()));

  const char* text =
      json_object_to_json_string_ext(root, JSON_C_TO_STRING_PLAIN);
  std::string out = text != nullptr ? text : "{}";
  json_object_put(root);
  return out;
}

// ---- ResourceLimiter + JudgeCore ---------------------------------------------

struct ChildError {
  int stage;
  int err;
};

std::string stage_message(int stage, int err) {
  const char* name = "未知阶段";
  switch (stage) {
    case 1:
      name = "setrlimit(RLIMIT_CORE)";
      break;
    case 2:
      name = "重定向 stdin";
      break;
    case 3:
      name = "重定向 stdout";
      break;
    case 4:
      name = "重定向 stderr";
      break;
    case 5:
      name = "setrlimit(RLIMIT_CPU)";
      break;
    case 6:
      name = "setrlimit(RLIMIT_AS)";
      break;
    case 7:
      name = "setrlimit(RLIMIT_FSIZE)";
      break;
    case 8:
      name = "execv";
      break;
    default:
      break;
  }
  return std::string(name) + " 失败: " + std::strerror(err);
}

RunStat run_process(const std::vector<std::string>& args,
                    const std::string& input, const fs::path& out_path,
                    const fs::path& err_path, const Limits& limits,
                    std::int64_t fsize_cap) {
  RunStat stat;

  int error_pipe[2] = {-1, -1};
  if (pipe(error_pipe) != 0) {
    stat.setup_error = std::string("pipe 失败: ") + std::strerror(errno);
    return stat;
  }
  for (const int fd : error_pipe) {
    (void)fcntl(fd, F_SETFD, FD_CLOEXEC);
  }

  const std::string out = out_path.string();
  const std::string err = err_path.string();
  std::vector<char*> argv;
  argv.reserve(args.size() + 1);
  for (const std::string& arg : args) {
    argv.push_back(const_cast<char*>(arg.c_str()));
  }
  argv.push_back(nullptr);

  const pid_t pid = fork();
  if (pid < 0) {
    stat.setup_error = std::string("fork 失败: ") + std::strerror(errno);
    close(error_pipe[0]);
    close(error_pipe[1]);
    return stat;
  }

  if (pid == 0) {
    close(error_pipe[0]);
    const auto fail = [&](int stage, int error_number) {
      const ChildError child_error{stage, error_number};
      const ssize_t ignored =
          write(error_pipe[1], &child_error, sizeof(child_error));
      (void)ignored;
      _exit(125);
    };

    (void)setpgid(0, 0);

    rlimit core_limit{0, 0};
    if (setrlimit(RLIMIT_CORE, &core_limit) != 0) {
      fail(1, errno);
    }

    const int in_fd = open(input.c_str(), O_RDONLY);
    if (in_fd < 0 || dup2(in_fd, STDIN_FILENO) < 0) {
      fail(2, errno);
    }
    close(in_fd);

    const int out_fd = open(out.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (out_fd < 0 || dup2(out_fd, STDOUT_FILENO) < 0) {
      fail(3, errno);
    }
    close(out_fd);

    const int err_fd = open(err.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (err_fd < 0 || dup2(err_fd, STDERR_FILENO) < 0) {
      fail(4, errno);
    }
    close(err_fd);

    std::int64_t cpu_seconds = (limits.cpu_ms + 999) / 1000;
    if (cpu_seconds < 1) {
      cpu_seconds = 1;
    }
    const rlimit cpu_limit{static_cast<rlim_t>(cpu_seconds),
                           static_cast<rlim_t>(cpu_seconds + 1)};
    if (setrlimit(RLIMIT_CPU, &cpu_limit) != 0) {
      fail(5, errno);
    }

    const rlimit memory_limit{static_cast<rlim_t>(limits.mem_bytes),
                              static_cast<rlim_t>(limits.mem_bytes)};
    if (setrlimit(RLIMIT_AS, &memory_limit) != 0) {
      fail(6, errno);
    }

    const rlimit file_limit{static_cast<rlim_t>(fsize_cap),
                            static_cast<rlim_t>(fsize_cap)};
    if (setrlimit(RLIMIT_FSIZE, &file_limit) != 0) {
      fail(7, errno);
    }

    execv(args.front().c_str(), argv.data());
    fail(8, errno);
  }

  close(error_pipe[1]);

  int status = 0;
  struct rusage usage {};
  const auto start = std::chrono::steady_clock::now();
  bool finished = false;

  while (!finished) {
    const pid_t waited = wait4(pid, &status, WNOHANG, &usage);
    if (waited == pid) {
      finished = true;
      break;
    }
    if (waited < 0) {
      if (errno == EINTR) {
        continue;
      }
      stat.setup_error = std::string("wait4 失败: ") + std::strerror(errno);
      kill(-pid, SIGKILL);
      kill(pid, SIGKILL);
      while (wait4(pid, &status, 0, &usage) < 0 && errno == EINTR) {
      }
      finished = true;
      break;
    }

    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                             std::chrono::steady_clock::now() - start)
                             .count();
    if (elapsed >= limits.wall_ms) {
      stat.timed_out = true;
      kill(-pid, SIGKILL);
      kill(pid, SIGKILL);
      while (wait4(pid, &status, 0, &usage) < 0 && errno == EINTR) {
      }
      finished = true;
      break;
    }

    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }

  if (WIFEXITED(status)) {
    stat.exit_code = WEXITSTATUS(status);
  } else if (WIFSIGNALED(status)) {
    stat.term_signal = WTERMSIG(status);
    stat.exit_code = -1;
  }

  stat.cpu_ms = static_cast<std::int64_t>(usage.ru_utime.tv_sec) * 1000 +
                static_cast<std::int64_t>(usage.ru_utime.tv_usec) / 1000 +
                static_cast<std::int64_t>(usage.ru_stime.tv_sec) * 1000 +
                static_cast<std::int64_t>(usage.ru_stime.tv_usec) / 1000;
  stat.mem_kb = static_cast<std::int64_t>(usage.ru_maxrss);

  ChildError child_error{};
  const ssize_t got = read(error_pipe[0], &child_error, sizeof(child_error));
  if (got == static_cast<ssize_t>(sizeof(child_error))) {
    stat.setup_error = stage_message(child_error.stage, child_error.err);
  }
  close(error_pipe[0]);

  return stat;
}

// ---- JudgeWorker --------------------------------------------------------------

JudgeResult judge_request(const EvalRequest& request) {
  JudgeResult result;
  result.file_id = request.file_id;
  result.problem_id = request.problem_id;
  result.test_id = request.test_id;

  if (request.compare_mode == "special") {
    result.verdict = Verdict::SE;
    result.error = "compare_mode=special 尚未实现：本期不提供 checker";
    return result;
  }

  std::string expected;
  bool expected_truncated = false;
  std::string read_error;
  if (!read_file_bounded(request.answer_path, kMaxAnswerBytes, expected,
                         expected_truncated, read_error)) {
    result.verdict = Verdict::SE;
    result.error = "读取答案失败: " + read_error;
    return result;
  }
  if (expected_truncated) {
    result.verdict = Verdict::SE;
    result.error = "答案超过读取上限";
    return result;
  }
  const std::string expected_trimmed = trim_common(expected);

  const fs::path result_dir(request.result_dir);
  const fs::path out_path = result_dir / (request.test_id + ".out");
  const fs::path err_path = result_dir / (request.test_id + ".err");
  const std::int64_t fsize_cap = std::min<std::int64_t>(
      kMaxOutBytes, request.limits.out_bytes + kOutputFsizeSlackBytes);

  const RunStat stat = run_process(
      std::vector<std::string>{request.bin_path}, request.input_path, out_path,
      err_path, request.limits, fsize_cap);
  result.exit_code = stat.exit_code;
  result.signal = stat.term_signal;
  result.cpu_ms = stat.cpu_ms;
  result.mem_kb = stat.mem_kb;
  result.wall_timeout = stat.timed_out;
  result.stderr_tail = read_tail(err_path, kMaxStderrTailBytes);

  if (!stat.setup_error.empty()) {
    result.verdict = Verdict::SE;
    result.error = stat.setup_error;
    return result;
  }
  if (stat.timed_out) {
    result.verdict = Verdict::TLE;
    return result;
  }
  // RLIMIT_CPU 的硬限制会用 SIGKILL 结束进程，也按 TLE 处理。
  if (stat.term_signal == SIGKILL && stat.cpu_ms > request.limits.cpu_ms) {
    result.verdict = Verdict::TLE;
    return result;
  }
  if (stat.term_signal != 0) {
    if (stat.term_signal == SIGXCPU) {
      result.verdict = Verdict::TLE;
    } else if (stat.term_signal == SIGXFSZ) {
      // 输出超限统一归入 WA。
      result.verdict = Verdict::WA;
    } else {
      result.verdict = Verdict::RE;
    }
    return result;
  }
  if (stat.exit_code == 125) {
    result.verdict = Verdict::SE;
    result.error = "子进程资源限制设置失败";
    return result;
  }
  if (stat.exit_code != 0) {
    result.verdict = Verdict::RE;
    return result;
  }
  if (stat.cpu_ms > request.limits.cpu_ms) {
    result.verdict = Verdict::TLE;
    return result;
  }
  if (stat.mem_kb * 1024 > request.limits.mem_bytes) {
    result.verdict = Verdict::MLE;
    return result;
  }

  std::error_code ec;
  const std::uintmax_t raw_size = fs::file_size(out_path, ec);
  if (ec) {
    result.verdict = Verdict::SE;
    result.error = "读取输出文件失败";
    return result;
  }
  if (raw_size > static_cast<std::uintmax_t>(fsize_cap)) {
    result.out_bytes = static_cast<std::int64_t>(raw_size);
    result.verdict = Verdict::WA;
    return result;
  }

  std::string actual;
  bool actual_truncated = false;
  if (!read_file_bounded(out_path, static_cast<std::size_t>(fsize_cap), actual,
                         actual_truncated, read_error)) {
    result.verdict = Verdict::SE;
    result.error = "读取输出失败: " + read_error;
    return result;
  }
  if (actual_truncated) {
    result.out_bytes = static_cast<std::int64_t>(raw_size);
    result.verdict = Verdict::WA;
    return result;
  }

  const std::string actual_trimmed = trim_common(actual);
  result.out_bytes = static_cast<std::int64_t>(actual_trimmed.size());
  if (result.out_bytes > request.limits.out_bytes) {
    result.verdict = Verdict::WA;
    return result;
  }
  result.verdict = actual_trimmed == expected_trimmed ? Verdict::AC : Verdict::WA;
  return result;
}

// ---- RequestQueue + ThreadPool -----------------------------------------------

struct Job {
  int fd = -1;
  EvalRequest request;
};

template <typename T>
class BoundedQueue {
 public:
  explicit BoundedQueue(std::size_t capacity) : capacity_(capacity) {}

  bool push(T item) {
    std::unique_lock<std::mutex> lock(mutex_);
    not_full_.wait(lock, [&] { return closed_ || queue_.size() < capacity_; });
    if (closed_) {
      return false;
    }
    queue_.push_back(std::move(item));
    not_empty_.notify_one();
    return true;
  }

  std::optional<T> pop() {
    std::unique_lock<std::mutex> lock(mutex_);
    not_empty_.wait(lock, [&] { return closed_ || !queue_.empty(); });
    if (queue_.empty()) {
      return std::nullopt;
    }
    T item = std::move(queue_.front());
    queue_.pop_front();
    not_full_.notify_one();
    return item;
  }

  void close() {
    std::lock_guard<std::mutex> lock(mutex_);
    closed_ = true;
    not_empty_.notify_all();
    not_full_.notify_all();
  }

 private:
  std::size_t capacity_;
  bool closed_ = false;
  std::deque<T> queue_;
  std::mutex mutex_;
  std::condition_variable not_empty_;
  std::condition_variable not_full_;
};

// ---- EvalServer ---------------------------------------------------------------

bool send_all(int fd, const void* data, std::size_t size) {
  const auto* bytes = static_cast<const char*>(data);
  std::size_t sent = 0;
  while (sent < size) {
    const ssize_t n = send(fd, bytes + sent, size - sent, MSG_NOSIGNAL);
    if (n < 0) {
      if (errno == EINTR) {
        continue;
      }
      return false;
    }
    if (n == 0) {
      return false;
    }
    sent += static_cast<std::size_t>(n);
  }
  return true;
}

bool recv_all(int fd, void* data, std::size_t size) {
  auto* bytes = static_cast<char*>(data);
  std::size_t received = 0;
  while (received < size) {
    const ssize_t n = recv(fd, bytes + received, size - received, 0);
    if (n < 0) {
      if (errno == EINTR) {
        continue;
      }
      return false;
    }
    if (n == 0) {
      return false;
    }
    received += static_cast<std::size_t>(n);
  }
  return true;
}

bool send_message(int fd, const std::string& payload) {
  if (payload.size() > kMaxMessageBytes) {
    return false;
  }
  const std::uint32_t length = htonl(static_cast<std::uint32_t>(payload.size()));
  return send_all(fd, &length, sizeof(length)) &&
         send_all(fd, payload.data(), payload.size());
}

bool recv_message(int fd, std::string& payload) {
  std::uint32_t length = 0;
  if (!recv_all(fd, &length, sizeof(length))) {
    return false;
  }
  const std::uint32_t size = ntohl(length);
  if (size == 0 || size > kMaxMessageBytes) {
    return false;
  }
  payload.resize(size);
  return recv_all(fd, payload.data(), payload.size());
}

class EvalServer {
 public:
  EvalServer(int port, int workers, std::size_t queue_capacity)
      : port_(port),
        workers_(workers),
        queue_(queue_capacity),
        threads_() {}

  ~EvalServer() { stop_workers(); }

  int run() {
    const int listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd < 0) {
      std::cerr << "socket 失败: " << std::strerror(errno) << "\n";
      return 3;
    }

    int reuse = 1;
    (void)setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(static_cast<std::uint16_t>(port_));
    if (bind(listen_fd, reinterpret_cast<sockaddr*>(&address),
             sizeof(address)) != 0) {
      std::cerr << "bind 失败: " << std::strerror(errno) << "\n";
      close(listen_fd);
      return 3;
    }
    if (listen(listen_fd, 64) != 0) {
      std::cerr << "listen 失败: " << std::strerror(errno) << "\n";
      close(listen_fd);
      return 3;
    }

    std::cout << "[eval_server] listening 127.0.0.1:" << port_
              << " workers=" << workers_ << std::endl;

    for (int i = 0; i < workers_; ++i) {
      threads_.emplace_back([this] { worker_loop(); });
    }

    pollfd descriptor{listen_fd, POLLIN, 0};
    while (g_stop == 0) {
      const int ready = poll(&descriptor, 1, 200);
      if (ready < 0) {
        if (errno == EINTR) {
          continue;
        }
        std::cerr << "poll 失败: " << std::strerror(errno) << "\n";
        break;
      }
      if (ready == 0) {
        continue;
      }
      if ((descriptor.revents & POLLIN) == 0) {
        continue;
      }

      sockaddr_in client{};
      socklen_t client_length = sizeof(client);
      const int fd = accept(listen_fd, reinterpret_cast<sockaddr*>(&client),
                            &client_length);
      if (fd < 0) {
        if (errno == EINTR) {
          continue;
        }
        std::cerr << "accept 失败: " << std::strerror(errno) << "\n";
        continue;
      }
      handle_connection(fd);
    }

    close(listen_fd);
    queue_.close();
    for (std::thread& thread : threads_) {
      if (thread.joinable()) {
        thread.join();
      }
    }
    std::cout << "[eval_server] stopped" << std::endl;
    return 0;
  }

 private:
  void stop_workers() {
    queue_.close();
    for (std::thread& thread : threads_) {
      if (thread.joinable()) {
        thread.join();
      }
    }
  }

  void handle_connection(int fd) {
    timeval timeout{};
    timeout.tv_sec = kSocketTimeoutSeconds;
    (void)setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    (void)setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

    std::string payload;
    if (!recv_message(fd, payload)) {
      close(fd);
      return;
    }

    EvalRequest request;
    std::string error;
    if (!parse_request(payload, request, error)) {
      JudgeResult result;
      result.verdict = Verdict::SE;
      result.error = error;
      const std::string response = result_to_json(result);
      (void)send_message(fd, response);
      close(fd);
      return;
    }

    if (!queue_.push(Job{fd, std::move(request)})) {
      close(fd);
    }
  }

  void worker_loop() {
    while (std::optional<Job> job = queue_.pop()) {
      const JudgeResult result = judge_request(job->request);
      const std::string response = result_to_json(result);
      (void)send_message(job->fd, response);
      close(job->fd);
    }
  }

  int port_;
  int workers_;
  BoundedQueue<Job> queue_;
  std::vector<std::thread> threads_;
};

// ---- 命令行 -------------------------------------------------------------------

struct ServerOptions {
  int port = kDefaultPort;
  int workers = kDefaultWorkers;
  std::size_t queue_capacity = kDefaultQueueCapacity;
};

void usage(const char* program) {
  std::cerr << "用法: " << program
            << " [--port 9000] [--workers 4] [--queue 64]\n";
}

bool parse_int(std::string_view text, int& value) {
  if (text.empty()) {
    return false;
  }
  const auto* begin = text.data();
  const auto* end = text.data() + text.size();
  const auto [ptr, ec] = std::from_chars(begin, end, value);
  return ec == std::errc{} && ptr == end;
}

bool parse_size(std::string_view text, std::size_t& value) {
  if (text.empty()) {
    return false;
  }
  const auto* begin = text.data();
  const auto* end = text.data() + text.size();
  const auto [ptr, ec] = std::from_chars(begin, end, value);
  return ec == std::errc{} && ptr == end;
}

bool parse_args(int argc, char** argv, ServerOptions& options, bool& help,
                std::string& error) {
  help = false;
  for (int i = 1; i < argc; ++i) {
    const std::string_view arg = argv[i];
    auto take = [&](std::string& target) -> bool {
      if (i + 1 >= argc) {
        error = "缺少参数值: " + std::string(arg);
        return false;
      }
      target = argv[++i];
      return true;
    };

    if (arg == "--help" || arg == "-h") {
      help = true;
      return true;
    }
    if (arg == "--port") {
      std::string value;
      if (!take(value) || !parse_int(value, options.port) || options.port < 1 ||
          options.port > 65535) {
        error = "--port 必须是 1—65535 的整数";
        return false;
      }
    } else if (arg == "--workers") {
      std::string value;
      if (!take(value) || !parse_int(value, options.workers) ||
          options.workers < 1 || options.workers > 64) {
        error = "--workers 必须是 1—64 的整数";
        return false;
      }
    } else if (arg == "--queue") {
      std::string value;
      if (!take(value) || !parse_size(value, options.queue_capacity) ||
          options.queue_capacity < 1 || options.queue_capacity > 65536) {
        error = "--queue 必须是 1—65536 的整数";
        return false;
      }
    } else {
      error = "未知参数: " + std::string(arg);
      return false;
    }
  }
  return true;
}

}  // namespace

int main(int argc, char** argv) {
  ServerOptions options;
  bool help = false;
  std::string error;

  if (!parse_args(argc, argv, options, help, error)) {
    std::cerr << "参数错误: " << error << "\n";
    usage(argv[0]);
    return 2;
  }
  if (help) {
    usage(argv[0]);
    return 0;
  }

  std::signal(SIGINT, handle_signal);
  std::signal(SIGTERM, handle_signal);
  std::signal(SIGPIPE, SIG_IGN);

  EvalServer server(options.port, options.workers, options.queue_capacity);
  return server.run();
}
