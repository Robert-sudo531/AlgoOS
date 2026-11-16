#pragma once

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>

namespace algoos::eval {

struct Limits {
    std::int64_t cpu_ms;
    std::int64_t wall_ms;
    std::int64_t mem_bytes;
    std::int64_t out_bytes;
};

// 文件已经在执行节点准备好；本接口不需要网络或协议对象。
struct LocalCase {
    std::filesystem::path executable;
    std::filesystem::path input;
    std::filesystem::path answer;
    std::filesystem::path output_dir;  // 必须是尚不存在的测试点目录
    Limits limits;
};

struct Result {
    std::string_view verdict;
    std::string_view reason;
    std::optional<std::int64_t> cpu_ms{};
    std::optional<std::int64_t> wall_ms{};
    std::optional<std::int64_t> mem_bytes{};
    std::optional<std::int64_t> out_bytes{};
    std::optional<int> exit_code{};
    std::optional<int> signal{};
    bool wall_timeout = false;
    std::string stderr_tail{};
    std::string detail{};
};

struct ProcessEvidence {
    std::int64_t cpu_ms;
    std::optional<int> exit_code;
    int signal;
    bool wall_timeout;
    // 仅由可信执行层提供。普通 wait4 RSS 与 RLIMIT_AS 的口径不同。
    std::optional<std::int64_t> comparable_memory_peak_bytes{};
    bool confirmed_oom = false;
};

struct Decision {
    std::string_view verdict;
    std::string_view reason;
};

// 启动错误由调用方优先处理；nullopt 表示继续做输出检查与比较。
std::optional<Decision> classify_process(const ProcessEvidence& evidence, const Limits& limits);

// 本地执行使用 RLIMIT_AS 和 wait4 RSS，尚不接收专属 cgroup 的 OOM 证据。
// 尚未接入的执行情况报入口错误，保留已生成的输出。
Result evaluate(const LocalCase& test);

}  // namespace algoos::eval
