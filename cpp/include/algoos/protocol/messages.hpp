#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "algoos/protocol/json.hpp"
#include "algoos/sandbox/types.hpp"

namespace algoos::protocol {

/// Evaluation task fields consumed by the native runner.  The Python control
/// plane already validated the full v1 message; the worker re-checks the fields
/// that affect execution because queue payloads are untrusted.
struct EvalTask {
  std::string msg_id;
  std::string file_id;
  std::string problem_id;
  std::string test_id;
  std::string judge_id;
  std::string target_node_id;
  std::string bin_hash;
  std::string test_bundle_hash;
  std::string judge_mode;
  std::string compare_mode;
  std::string evaluation_mode;
  int64_t sequence{0};
  int64_t attempt{1};
  int64_t enqueue_seq{0};
  sandbox::Limits limits;
};

struct SpecialTask {
  std::string msg_id;
  std::string file_id;
  std::string problem_id;
  std::string test_id;
  std::string judge_id;
  std::string target_eval_node;
  std::string out_hash;
  std::string ans_hash;
  std::string checker_hash;
  std::string fallback_topic;
  int64_t sequence{0};
  int64_t attempt{1};
  sandbox::Limits limits;
};

struct CompileSpec {
  std::string source_hash;
  std::string compiler_profile;
  std::string compiler;
  std::string source;
  std::string output_dir;
  std::string platform;
  std::vector<std::string> flags;
  sandbox::Limits limits;
};

[[nodiscard]] EvalTask parse_eval_task(json_object* task);
[[nodiscard]] SpecialTask parse_special_task(json_object* task);
[[nodiscard]] CompileSpec parse_compile_spec(json_object* spec);

/// Serialize an eval_result for ``eval-topic``.  ``workdir`` is not part of the
/// wire message; paths stay on the node.
Json make_eval_result(const EvalTask& task, const std::string& node_id,
                      std::string_view verdict, std::string_view reason,
                      const sandbox::Evidence& evidence, int64_t out_bytes,
                      std::string_view memory_source, std::string_view stderr_tail,
                      std::string_view detail, bool short_circuited);

/// Serialize a special_result for ``special-result-topic``.
Json make_special_result(const SpecialTask& task, std::string_view verdict,
                         std::string_view reason, std::string_view detail,
                         std::string_view stderr_tail, int64_t cpu_ms, int64_t wall_ms,
                         std::optional<int64_t> mem_bytes, std::optional<int64_t> memory_source,
                         bool short_circuited);

}  // namespace algoos::protocol
