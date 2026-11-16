#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "algoos/protocol/messages.hpp"
#include "algoos/sandbox/types.hpp"

namespace algoos::worker {

struct CompileOutcome {
  std::string status{"FINAL"};  ///< COMPILED | FINAL
  std::string verdict;          ///< empty on success, CE/SE otherwise
  std::string reason{"NONE"};
  std::string detail;
  std::string stderr_tail;
  std::string binary;
  std::string build_key;
  std::string compiler_version;
  std::string compiler_profile;
  int64_t cpu_ms{0};
  int64_t wall_ms{0};
};

/// Native compile worker.  Runs a frozen compiler profile under rlimits and a
/// wall deadline, verifies the produced binary by content hash at the agent
/// layer, and derives a build-cache key that is distinct from the bin hash.
class CompileService {
 public:
  struct Config {
    std::string compiler{"g++"};
    std::string work_root{"/tmp/algoos-compile"};
    std::string platform{"linux-x86_64"};
  };

  explicit CompileService(Config config);

  [[nodiscard]] CompileOutcome compile(const protocol::CompileSpec& spec);
  [[nodiscard]] const std::string& compiler_version() const { return compiler_version_; }

 private:
  Config config_;
  std::string compiler_version_;
};

}  // namespace algoos::worker
