#include "eval_core.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <stdexcept>
#include <string>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <variant>

namespace algoos::eval {
namespace {
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

constexpr std::int64_t kMaxTimeMs = 3'600'000;
constexpr std::int64_t kMaxMemBytes = 8LL << 30;
constexpr std::int64_t kMaxOutBytes = 256LL << 20;
constexpr std::int64_t kOutputSlackBytes = 4096;
constexpr std::size_t kMaxAnswerBytes = 64ULL << 20;
constexpr off_t kStderrTailBytes = 4096;

void check(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

void validate_limits(const Limits& limits) {
    check(limits.cpu_ms > 0 && limits.cpu_ms <= kMaxTimeMs && limits.wall_ms == limits.cpu_ms,
          "invalid local CPU/wall limits");
    check(limits.mem_bytes > 0 && limits.mem_bytes <= kMaxMemBytes, "invalid local memory limit");
    check(limits.out_bytes >= 0 && limits.out_bytes <= kMaxOutBytes, "invalid local output limit");
}

std::string read_bounded(const fs::path& path, std::size_t maximum) {
    std::ifstream input(path, std::ios::binary);
    check(input.good(), "cannot open: " + path.string());
    std::array<char, 8192> chunk{};
    std::string bytes;
    while (input) {
        input.read(chunk.data(), static_cast<std::streamsize>(chunk.size()));
        const auto count = static_cast<std::size_t>(input.gcount());
        check(count <= maximum - bytes.size(), "file exceeds read cap: " + path.string());
        bytes.append(chunk.data(), count);
    }
    check(!input.bad(), "cannot read: " + path.string());
    return bytes;
}

// common：统一换行、去掉行尾空格/制表符及末尾空行；不解码输出字节。
std::string normalize_common(std::string_view bytes) {
    std::string result;
    result.reserve(bytes.size());
    std::size_t start = 0;
    const auto append_line = [&](std::size_t end) {
        while (end > start && (bytes[end - 1] == ' ' || bytes[end - 1] == '\t')) --end;
        result.append(bytes.substr(start, end - start));
    };
    for (std::size_t index = 0; index < bytes.size(); ++index) {
        const char value = bytes[index];
        if (value != '\r' && value != '\n') continue;
        append_line(index);
        result.push_back('\n');
        if (value == '\r' && index + 1 < bytes.size() && bytes[index + 1] == '\n') ++index;
        start = index + 1;
    }
    append_line(bytes.size());
    while (!result.empty() && result.back() == '\n') result.pop_back();
    return result;
}

struct Descriptor {
    explicit Descriptor(int fd) : fd(fd) {}
    Descriptor(const Descriptor&) = delete;
    Descriptor& operator=(const Descriptor&) = delete;
    ~Descriptor() { close_now(); }
    void close_now() {
        if (fd >= 0) close(fd);
        fd = -1;
    }
    int fd;
};

std::string read_stderr_tail(const fs::path& path) {
    Descriptor file(open(path.c_str(), O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC));
    struct stat metadata{};
    if (file.fd < 0 || fstat(file.fd, &metadata) != 0 || !S_ISREG(metadata.st_mode) || metadata.st_size <= 0)
        return {};
    const auto count = std::min(metadata.st_size, kStderrTailBytes);
    std::string tail(static_cast<std::size_t>(count), '\0');
    ssize_t got;
    do { got = pread(file.fd, tail.data(), tail.size(), metadata.st_size - count); }
    while (got < 0 && errno == EINTR);
    tail.resize(got > 0 ? static_cast<std::size_t>(got) : 0);
    return tail;
}

struct ChildError {
    int stage;
    int number;
};

struct Measurement {
    std::int64_t cpu_ms;
    std::int64_t wall_ms;
    std::int64_t mem_bytes;
    std::optional<int> exit_code;
    int signal;
    bool wall_timeout;
};

struct StartupFailure {
    std::string detail;
};

std::int64_t elapsed_ms(Clock::time_point start) {
    return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - start).count();
}

bool terminate_and_reap(pid_t pid, int* status, rusage* usage) {
    const bool group_killed = kill(-pid, SIGKILL) == 0;
    const bool child_killed = kill(pid, SIGKILL) == 0;
    if (!group_killed && !child_killed && errno != ESRCH)
        throw std::runtime_error(std::string("kill: ") + std::strerror(errno));
    pid_t waited;
    do { waited = wait4(pid, status, 0, usage); }
    while (waited < 0 && errno == EINTR);
    if (waited != pid) throw std::runtime_error(std::string("wait4 after kill: ") + std::strerror(errno));
    return group_killed || child_killed;
}

std::variant<Measurement, StartupFailure> run(const LocalCase& test, const fs::path& directory,
                                              std::int64_t file_cap) {
    const auto binary = fs::absolute(test.executable).string();
    const auto input = fs::absolute(test.input).string();
    const auto output = (directory / "stdout.out").string();
    const auto error = (directory / "stderr.err").string();
    const auto workdir = directory.string();
    const auto cpu_seconds = static_cast<rlim_t>((test.limits.cpu_ms + 999) / 1000);
    const auto memory_bytes = static_cast<rlim_t>(test.limits.mem_bytes);
    const auto file_bytes = static_cast<rlim_t>(file_cap);
    char* const args[] = {const_cast<char*>(binary.c_str()), nullptr};

    int descriptors[2];
    if (pipe2(descriptors, O_CLOEXEC | O_NONBLOCK) != 0)
        return StartupFailure{std::string("pipe2: ") + std::strerror(errno)};
    Descriptor reader(descriptors[0]), writer(descriptors[1]);
    const auto started = Clock::now();
    const pid_t pid = fork();
    if (pid < 0) return StartupFailure{std::string("fork: ") + std::strerror(errno)};
    if (pid == 0) {
        close(reader.fd);
        const auto fail = [&](int stage) {
            const ChildError failure{stage, errno};
            ssize_t count;
            do { count = write(writer.fd, &failure, sizeof(failure)); }
            while (count < 0 && errno == EINTR);
            _exit(125);
        };
        const auto redirect = [&](const char* path, int flags, int target, int stage) {
            const int fd = open(path, flags, 0600);
            if (fd < 0) fail(stage);
            if (fd != target) {
                if (dup2(fd, target) < 0) fail(stage);
                close(fd);
            }
        };
        const auto set_limit = [&](int resource, rlim_t soft, rlim_t hard, int stage) {
            const rlimit limit{soft, hard};
            if (setrlimit(resource, &limit) != 0) fail(stage);
        };
        if (setpgid(0, 0) != 0 || chdir(workdir.c_str()) != 0) fail(0);
        redirect(input.c_str(), O_RDONLY, STDIN_FILENO, 1);
        redirect(output.c_str(), O_WRONLY | O_CREAT | O_EXCL, STDOUT_FILENO, 2);
        redirect(error.c_str(), O_WRONLY | O_CREAT | O_EXCL, STDERR_FILENO, 3);
        set_limit(RLIMIT_CORE, 0, 0, 4);
        set_limit(RLIMIT_CPU, cpu_seconds, cpu_seconds + 1, 5);
        set_limit(RLIMIT_AS, memory_bytes, memory_bytes, 6);
        set_limit(RLIMIT_FSIZE, file_bytes, file_bytes, 7);
        execv(binary.c_str(), args);
        fail(8);
    }
    writer.close_now();

    int status = 0;
    rusage usage{};
    bool deadline_kill = false;
    for (;;) {
        const auto waited = wait4(pid, &status, WNOHANG, &usage);
        if (waited == pid) break;
        if (waited < 0) {
            const int saved = errno;
            if (saved == EINTR) continue;
            if (saved != ECHILD) terminate_and_reap(pid, nullptr, nullptr);
            throw std::runtime_error(std::string("wait4: ") + std::strerror(saved));
        }
        if (elapsed_ms(started) >= test.limits.wall_ms) {
            deadline_kill = terminate_and_reap(pid, &status, &usage);
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    const auto wall_ms = elapsed_ms(started);

    ChildError failure{};
    ssize_t count;
    do { count = read(reader.fd, &failure, sizeof(failure)); }
    while (count < 0 && errno == EINTR);
    if (count == static_cast<ssize_t>(sizeof(failure))) {
        constexpr std::string_view stages[] = {
            "setpgid/chdir", "stdin", "stdout", "stderr", "RLIMIT_CORE", "RLIMIT_CPU",
            "RLIMIT_AS", "RLIMIT_FSIZE", "execv",
        };
        const auto stage = failure.stage >= 0 && failure.stage < 9 ? stages[failure.stage] : "unknown";
        return StartupFailure{std::string(stage) + ": " + std::strerror(failure.number)};
    }
    check(count == 0, "invalid child setup status");
    check(WIFEXITED(status) || WIFSIGNALED(status), "child has no exit status");
    const auto code = WIFEXITED(status) ? std::optional{WEXITSTATUS(status)} : std::nullopt;
    const int signal = WIFSIGNALED(status) ? WTERMSIG(status) : 0;
    // 只在时限终止和实际回收状态一致时判墙钟超时，不能仅凭 SIGKILL 推断。
    const bool wall_timeout = deadline_kill && signal == SIGKILL;

    // Linux wait4 返回的 ru_maxrss 单位是 KiB；v1 使用字节。
    const auto cpu_ms = (static_cast<std::int64_t>(usage.ru_utime.tv_sec) + usage.ru_stime.tv_sec) * 1000
        + (usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) / 1000;
    return Measurement{cpu_ms, wall_ms, static_cast<std::int64_t>(usage.ru_maxrss) * 1024,
                       code, signal, wall_timeout};
}
}  // namespace

std::optional<Decision> classify_process(const ProcessEvidence& evidence, const Limits& limits) {
    if (evidence.confirmed_oom) return Decision{"MLE", "OOM_KILL"};
    if (evidence.wall_timeout) return Decision{"TLE", "WALL_TIMEOUT"};
    // SIGKILL 单独不代表 CPU 超限；还要有实际 CPU 时间超过配置的证据。
    if (evidence.signal == SIGXCPU || (evidence.signal == SIGKILL && evidence.cpu_ms > limits.cpu_ms))
        return Decision{"TLE", "CPU_TIMEOUT"};
    switch (evidence.signal) {
        case 0:
            break;
        case SIGXFSZ:
            return Decision{"WA", "OUTPUT_LIMIT"};
        case SIGABRT:
        case SIGBUS:
        case SIGFPE:
        case SIGILL:
        case SIGSEGV:
            return Decision{"RE", "CRASH_SIGNAL"};
        default:
            throw std::runtime_error("unclassified signal " + std::to_string(evidence.signal));
    }
    check(evidence.exit_code.has_value(), "child has no normal exit status");
    // 成功 exec 会关闭错误管道；此处的 125 也属于程序自己的退出码。
    if (*evidence.exit_code != 0) return Decision{"RE", "NONZERO_EXIT"};
    if (evidence.comparable_memory_peak_bytes && *evidence.comparable_memory_peak_bytes > limits.mem_bytes)
        return Decision{"MLE", "MEM_PEAK_OVER"};
    if (evidence.cpu_ms > limits.cpu_ms) return Decision{"TLE", "CPU_TIMEOUT"};
    return std::nullopt;
}

Result evaluate(const LocalCase& test) {
    validate_limits(test.limits);
    for (const auto& file : {test.executable, test.input, test.answer})
        check(fs::is_regular_file(file), "expected a local file: " + file.string());
    const auto expected = normalize_common(read_bounded(test.answer, kMaxAnswerBytes));
    const auto limit = static_cast<std::size_t>(test.limits.out_bytes);
    check(expected.size() == limit, "out_bytes differs from normalized answer length");

    const auto directory = fs::absolute(test.output_dir);
    check(fs::create_directory(directory), "output directory already exists: " + directory.string());
    const auto file_cap = std::min(kMaxOutBytes, test.limits.out_bytes + kOutputSlackBytes);
    const auto execution = run(test, directory, file_cap);
    if (const auto* failed = std::get_if<StartupFailure>(&execution))
        return {.verdict = "SE", .reason = "SYSTEM_ERROR", .detail = failed->detail};

    const auto& measured = std::get<Measurement>(execution);
    Result result{.verdict = {}, .reason = {}, .cpu_ms = measured.cpu_ms,
                  .wall_ms = measured.wall_ms, .mem_bytes = measured.mem_bytes,
                  .exit_code = measured.exit_code, .signal = measured.signal,
                  .wall_timeout = measured.wall_timeout,
                  .stderr_tail = read_stderr_tail(directory / "stderr.err")};
    const ProcessEvidence evidence{measured.cpu_ms, measured.exit_code, measured.signal, measured.wall_timeout};
    if (const auto decision = classify_process(evidence, test.limits)) {
        result.verdict = decision->verdict;
        result.reason = decision->reason;
        return result;
    }

    check(measured.mem_bytes <= test.limits.mem_bytes, "RSS above limit without comparable memory evidence");
    const auto actual = normalize_common(read_bounded(directory / "stdout.out", static_cast<std::size_t>(file_cap)));
    result.out_bytes = static_cast<std::int64_t>(actual.size());
    if (actual.size() > limit) {
        result.verdict = "WA";
        result.reason = "OUTPUT_LIMIT";
        return result;
    }
    const bool equal = actual == expected;
    result.verdict = equal ? "AC" : "WA";
    result.reason = equal ? "NONE" : "ANSWER_MISMATCH";
    return result;
}

}  // namespace algoos::eval
