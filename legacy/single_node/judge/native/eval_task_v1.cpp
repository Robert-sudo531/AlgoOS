// eval_local 与 eval-worker 共用的 v1 任务和结果处理。
#include "eval_task_v1.hpp"
#include "eval_core.hpp"

#include <array>
#include <ctime>
#include <fstream>
#include <string>

namespace algoos::worker {
namespace {
namespace proto = algoos::protocol;

std::string read_message(std::istream& stream) {
    constexpr std::size_t cap = 1ULL << 20;
    std::string result;
    std::array<char, 4096> chunk{};
    while (stream) {
        stream.read(chunk.data(), static_cast<std::streamsize>(chunk.size()));
        const auto count = static_cast<std::size_t>(stream.gcount());
        proto::require(count <= cap - result.size(), "JSON input exceeds 1 MiB");
        result.append(chunk.data(), count);
    }
    proto::require(!stream.bad(), "cannot read JSON input");
    return result;
}

std::string utc_now() {
    const auto now = std::time(nullptr);
    std::tm calendar{};
    char timestamp[21];
    proto::require(gmtime_r(&now, &calendar) != nullptr &&
                       std::strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &calendar) != 0,
                   "cannot format UTC time");
    return timestamp;
}

std::string stderr_text(const std::string& tail) {
    if (tail.empty()) return {};
    proto::Json token(json_object_new_string_len(tail.data(), static_cast<int>(tail.size())), json_object_put);
    proto::require(token != nullptr, "cannot allocate stderr summary");
    try {
        (void)proto::ascii_json(json_object_to_json_string_ext(token.get(), JSON_C_TO_STRING_PLAIN));
        return tail;
    } catch (const std::runtime_error&) {
        // 文件截尾可能切断 UTF-8，原始字节保留在 stderr.err。
        constexpr char hex[] = "0123456789abcdef";
        std::string text;
        for (unsigned char byte : tail) {
            if (byte < 0x80) text += static_cast<char>(byte);
            else {
                text += "\\x";
                text += hex[byte >> 4];
                text += hex[byte & 15];
            }
        }
        return text;
    }
}

proto::Json result_message(json_object* task, const char* node, const algoos::eval::Result& result) {
    proto::Json message(json_object_new_object(), json_object_put);
    proto::require(message != nullptr, "cannot allocate result");
    const auto put = [&](const char* key, json_object* value) { json_object_object_add(message.get(), key, value); };
    const auto put_text = [&](const char* key, std::string_view value) {
        put(key, json_object_new_string_len(value.data(), static_cast<int>(value.size())));
    };
    const auto put_number = [&](const char* key, const auto& value) {
        put(key, value ? json_object_new_int64(*value) : nullptr);
    };
    for (const auto* key : {"file_id", "correlation_id", "problem_id", "test_id", "sequence", "attempt", "judge_id"})
        put(key, json_object_get(proto::field(task, key)));
    put("ver", json_object_new_int(1));
    put_text("type", "eval_result");
    put_text("status", "FINAL");
    put_text("stage", "eval");
    put_text("msg_id", "result:" + proto::string(task, "msg_id"));
    put_text("emitted_at", utc_now());
    put_text("node_id", node);
    put_text("source", "runner");
    put_text("verdict", result.verdict);
    put_text("reason", result.reason);
    put_number("cpu_ms", result.cpu_ms);
    put_number("wall_ms", result.wall_ms);
    put_number("mem_bytes", result.mem_bytes);
    if (result.mem_bytes) put_text("memory_source", "rss_peak");
    else put("memory_source", nullptr);
    put_number("out_bytes", result.out_bytes);
    put_number("exit_code", result.exit_code);
    put_number("signal", result.signal);
    put("wall_timeout", json_object_new_boolean(result.wall_timeout));
    put("short_circuited", json_object_new_boolean(false));
    put_text("stderr_tail", stderr_text(result.stderr_tail));
    put_text("detail", result.detail);
    return message;
}
}  // namespace

protocol::Json read_json_file(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    proto::require(input.good(), "cannot open JSON file: " + path.string());
    return proto::parse(read_message(input));
}

protocol::Json load_policy(const std::filesystem::path& path) {
    auto policy = read_json_file(path);
    proto::require(proto::integer(policy.get(), "ver", 1) == 1, "unsupported policy version");
    proto::require(proto::string(policy.get(), "policy_id") == "distributed_core_v1", "unsupported policy");
    for (const auto* key : {"transport", "modes", "result"})
        proto::require(json_object_is_type(proto::field(policy.get(), key), json_type_object),
                       std::string("invalid policy field: ") + key);
    return policy;
}

void check_node_id(const std::string& node_id, json_object* policy) {
    const auto pattern = proto::string(proto::field(policy, "transport"), "node_id_pattern");
    proto::require(std::regex_match(node_id, std::regex(pattern)), "invalid node_id");
}

std::string evaluate_task(std::istream& input, json_object* policy, const std::string& node_id,
                          const LocalFiles& files) {
    auto task = proto::parse(read_message(input));
    proto::normalize(task.get(), policy);
    proto::require(proto::string(task.get(), "type") == "eval_task", "expected eval_task");
    proto::require(proto::string(task.get(), "target_node_id") == node_id, "target node mismatch");
    proto::require(proto::string(task.get(), "compare_mode") == "common", "expected common compare mode");
    auto* limits = proto::field(task.get(), "limits");
    const eval::LocalCase test{
        files.executable, files.input, files.answer, files.output_dir,
        {proto::integer(limits, "cpu_ms", 1), proto::integer(limits, "wall_ms", 1),
         proto::integer(limits, "mem_bytes", 1), proto::integer(limits, "out_bytes")},
    };
    auto result = result_message(task.get(), node_id.c_str(), eval::evaluate(test));
    proto::normalize(result.get(), policy);
    return json_object_to_json_string_ext(result.get(), JSON_C_TO_STRING_PLAIN);
}

}  // namespace algoos::worker
