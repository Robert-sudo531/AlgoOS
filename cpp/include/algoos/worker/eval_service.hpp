#pragma once

#include <cstdint>
#include <optional>
#include <string>

#include "algoos/protocol/messages.hpp"
#include "algoos/sandbox/types.hpp"
#include "algoos/worker/cancel_registry.hpp"

namespace algoos::worker {

struct EvalPaths {
  std::string binary;
  std::string input;
  std::string answer;
};

struct RawOutcome {
  bool comparable{false};
  bool cancelled{false};
  std::string verdict;
  std::string reason;
  std::string detail;
  std::string stderr_tail;
  std::string memory_source;
  std::string out_path;
  std::string work_dir;
  int64_t out_bytes{0};
  sandbox::Evidence evidence;
};

struct EvalOutcome {
  std::string verdict;
  std::string reason;
  std::string detail;
  std::string stderr_tail;
  std::string memory_source;
  int64_t out_bytes{0};
  sandbox::Evidence evidence;
  bool short_circuited{false};
};

/// Evaluation service: one case in, one verdict out.  The only place where
/// resource evidence becomes a verdict is ``sandbox::classify``.
class EvalService {
 public:
  struct Config {
    std::string work_root{"/tmp/algoos-eval"};
    sandbox::SandboxConfig sandbox;
    bool merge_ole{false};
  };

  EvalService(Config config, CancelRegistry& registry);

  [[nodiscard]] EvalOutcome judge(const protocol::EvalTask& task, const EvalPaths& paths);

  /// Run a program without comparing, for special judging.  The caller owns
  /// ``work_dir`` and must remove it after reading ``out_path``.
  [[nodiscard]] RawOutcome run_raw(const protocol::EvalTask& task, const EvalPaths& paths);

 private:
  Config config_;
  CancelRegistry& registry_;
};

}  // namespace algoos::worker
