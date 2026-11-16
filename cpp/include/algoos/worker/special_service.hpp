#pragma once

#include <cstdint>
#include <string>

#include "algoos/protocol/messages.hpp"
#include "algoos/sandbox/types.hpp"

namespace algoos::worker {

struct SpecialPaths {
  std::string input;
  std::string output;
  std::string answer;
  std::string checker;
};

struct SpecialOutcome {
  std::string verdict;  ///< AC | WA | SE
  std::string reason;
  std::string detail;
  std::string stderr_tail;
  int64_t cpu_ms{0};
  int64_t wall_ms{0};
  bool short_circuited{false};
};

/// Runs an external checker under the same sandbox.  The checker contract is
/// exit 0 = AC, exit 1 = WA, any other status = SE.
class SpecialService {
 public:
  struct Config {
    std::string work_root{"/tmp/algoos-special"};
    sandbox::SandboxConfig sandbox;
  };

  explicit SpecialService(Config config);

  [[nodiscard]] SpecialOutcome judge(const protocol::SpecialTask& task,
                                     const SpecialPaths& paths);

 private:
  Config config_;
};

}  // namespace algoos::worker
