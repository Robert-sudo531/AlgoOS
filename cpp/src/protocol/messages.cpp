#include "algoos/protocol/messages.hpp"

#include <chrono>
#include <ctime>
#include <stdexcept>
#include <string>

#include "algoos/protocol/digest.hpp"

namespace algoos::protocol {
namespace {

using algoos::sandbox::Limits;

void require(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

Limits parse_limits(json_object* limits) {
  Limits result;
  result.cpu_ms = int_field(limits, "cpu_ms", 1);
  result.wall_ms = int_field(limits, "wall_ms", 1);
  result.mem_bytes = int_field(limits, "mem_bytes", 1);
  result.out_bytes = int_field(limits, "out_bytes", 0);
  require(result.wall_ms >= result.cpu_ms, "wall_ms must not be below cpu_ms");
  return result;
}

std::optional<std::string> optional_string(json_object* object, const char* key) {
  if (!has(object, key)) return std::nullopt;
  json_object* value = field(object, key);
  if (value == nullptr) return std::nullopt;
  require(json_object_is_type(value, json_type_string), std::string("expected string: ") + key);
  return std::string(json_object_get_string(value), json_object_get_string_len(value));
}

std::string utc_now() {
  const auto now = std::time(nullptr);
  std::tm calendar{};
  char buffer[21];
  require(gmtime_r(&now, &calendar) != nullptr &&
              std::strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &calendar) != 0,
          "cannot format UTC time");
  return buffer;
}

}  // namespace

EvalTask parse_eval_task(json_object* task) {
  require(string_field(task, "type") == "eval_task", "expected eval_task");
  EvalTask result;
  result.msg_id = string_field(task, "msg_id");
  result.file_id = string_field(task, "file_id");
  result.problem_id = string_field(task, "problem_id");
  result.test_id = string_field(task, "test_id");
  result.judge_id = string_field(task, "judge_id");
  result.target_node_id = string_field(task, "target_node_id");
  result.bin_hash = string_field(task, "bin_hash");
  result.test_bundle_hash = string_field(task, "test_bundle_hash");
  result.judge_mode = string_field(task, "judge_mode");
  result.compare_mode = string_field(task, "compare_mode");
  result.evaluation_mode = string_field(task, "evaluation_mode");
  result.sequence = int_field(task, "sequence", 1);
  result.attempt = int_field(task, "attempt", 1);
  result.enqueue_seq = int_field(task, "enqueue_seq", 1);
  result.limits = parse_limits(object_field(task, "limits"));
  require(valid_hash(result.bin_hash), "invalid bin_hash");
  require(valid_hash(result.test_bundle_hash), "invalid test_bundle_hash");
  require(result.judge_id == result.file_id + ":" + result.test_id + ":" +
                                  std::to_string(result.attempt),
          "judge_id mismatch");
  require(result.judge_mode == "normal" || result.judge_mode == "special",
          "unsupported judge_mode");
  return result;
}

SpecialTask parse_special_task(json_object* task) {
  require(string_field(task, "type") == "special_task", "expected special_task");
  SpecialTask result;
  result.msg_id = string_field(task, "msg_id");
  result.file_id = string_field(task, "file_id");
  result.problem_id = string_field(task, "problem_id");
  result.test_id = string_field(task, "test_id");
  result.judge_id = string_field(task, "judge_id");
  result.out_hash = string_field(task, "out_hash");
  result.ans_hash = string_field(task, "ans_hash");
  result.checker_hash = string_field(task, "checker_hash");
  result.fallback_topic = string_field(task, "fallback_topic");
  result.sequence = int_field(task, "sequence", 1);
  result.attempt = int_field(task, "attempt", 1);
  result.limits = parse_limits(object_field(task, "limits"));
  auto* reply = object_field(task, "reply_to");
  result.target_eval_node = string_field(reply, "eval_node_id");
  require(valid_hash(result.out_hash) && valid_hash(result.ans_hash) &&
              valid_hash(result.checker_hash),
          "invalid special task hash");
  require(result.judge_id == result.file_id + ":" + result.test_id + ":" +
                                  std::to_string(result.attempt),
          "judge_id mismatch");
  return result;
}

CompileSpec parse_compile_spec(json_object* spec) {
  CompileSpec result;
  result.source_hash = string_field(spec, "source_hash");
  result.compiler_profile = string_field(spec, "compiler_profile");
  result.compiler = string_field(spec, "compiler");
  result.source = string_field(spec, "source");
  result.output_dir = string_field(spec, "output_dir");
  result.platform = string_field(spec, "platform", true);
  result.limits = parse_limits(object_field(spec, "limits"));
  auto* flags = array_field(spec, "flags");
  for (std::size_t index = 0; index < json_object_array_length(flags); ++index) {
    auto* entry = json_object_array_get_idx(flags, index);
    require(json_object_is_type(entry, json_type_string), "compiler flag must be a string");
    result.flags.emplace_back(json_object_get_string(entry));
  }
  require(valid_hash(result.source_hash), "invalid source_hash");
  return result;
}

Json make_eval_result(const EvalTask& task, const std::string& node_id, std::string_view verdict,
                      std::string_view reason, const sandbox::Evidence& evidence,
                      int64_t out_bytes, std::string_view memory_source,
                      std::string_view stderr_tail, std::string_view detail,
                      bool short_circuited) {
  Json message = Json::object();
  message.add("ver", json_object_new_int(1));
  message.add_string("type", "eval_result");
  message.add_string("msg_id", "result:" + task.msg_id);
  message.add_string("file_id", task.file_id);
  message.add_string("correlation_id", task.file_id);
  message.add_string("problem_id", task.problem_id);
  message.add_string("test_id", task.test_id);
  message.add_string("judge_id", task.judge_id);
  message.add("sequence", json_object_new_int64(task.sequence));
  message.add("attempt", json_object_new_int64(task.attempt));
  message.add("enqueue_seq", json_object_new_int64(task.enqueue_seq));
  message.add_string("emitted_at", utc_now());
  message.add_string("stage", "eval");
  message.add_string("status", "FINAL");
  message.add_string("node_id", node_id);
  message.add_string("source", "runner");
  message.add_string("verdict", verdict);
  message.add_string("reason", reason);
  message.add("wall_timeout", json_object_new_boolean(evidence.wall_dog_fired));
  message.add("short_circuited", json_object_new_boolean(short_circuited));
  if (evidence.started && evidence.cpu_ms >= 0) {
    message.add("cpu_ms", json_object_new_int64(evidence.cpu_ms));
    message.add("wall_ms", json_object_new_int64(evidence.wall_ms));
  } else {
    message.add_null("cpu_ms");
    message.add_null("wall_ms");
  }
  message.add("out_bytes", json_object_new_int64(out_bytes));
  if (evidence.wifsignaled) {
    message.add_null("exit_code");
    message.add("signal", json_object_new_int64(evidence.term_sig));
  } else if (evidence.wifexited) {
    message.add("exit_code", json_object_new_int64(evidence.exit_code));
    message.add("signal", json_object_new_int(0));
  } else {
    message.add_null("exit_code");
    message.add_null("signal");
  }
  if (memory_source.empty()) {
    message.add_null("mem_bytes");
    message.add_null("memory_source");
  } else {
    const int64_t peak = evidence.cgroup_memory_peak.value_or(evidence.ru_maxrss_bytes);
    message.add("mem_bytes", json_object_new_int64(peak));
    message.add_string("memory_source", memory_source);
  }
  message.add_string("stderr_tail", stderr_tail);
  message.add_string("detail", detail);
  return message;
}

Json make_special_result(const SpecialTask& task, std::string_view verdict,
                         std::string_view reason, std::string_view detail,
                         std::string_view stderr_tail, int64_t cpu_ms, int64_t wall_ms,
                         std::optional<int64_t> mem_bytes, std::optional<int64_t> memory_source,
                         bool short_circuited) {
  (void)memory_source;
  Json message = Json::object();
  message.add("ver", json_object_new_int(1));
  message.add_string("type", "special_result");
  message.add_string("msg_id", "special:" + task.msg_id);
  message.add_string("file_id", task.file_id);
  message.add_string("correlation_id", task.file_id);
  message.add_string("problem_id", task.problem_id);
  message.add_string("test_id", task.test_id);
  message.add_string("judge_id", task.judge_id);
  message.add("sequence", json_object_new_int64(task.sequence));
  message.add("attempt", json_object_new_int64(task.attempt));
  message.add_string("emitted_at", utc_now());
  message.add_string("target_eval_node", task.target_eval_node);
  message.add_string("session_id", "node-session");
  message.add_string("source", "special");
  message.add_string("verdict", verdict);
  message.add_string("reason", reason);
  message.add("short_circuited", json_object_new_boolean(short_circuited));
  message.add("cpu_ms", json_object_new_int64(cpu_ms));
  message.add("wall_ms", json_object_new_int64(wall_ms));
  if (mem_bytes) {
    message.add("mem_bytes", json_object_new_int64(*mem_bytes));
    message.add_string("memory_source", "rss_peak");
  } else {
    message.add_null("mem_bytes");
    message.add_null("memory_source");
  }
  message.add_string("detail", detail);
  message.add_string("stderr_tail", stderr_tail);
  return message;
}

}  // namespace algoos::protocol
