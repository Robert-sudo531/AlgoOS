#pragma once

#include <string>

#include "algoos/sandbox/types.hpp"

namespace algoos::sandbox {

/// Execute ``request.executable`` under the available isolation layers and
/// return raw evidence.  Never throws for user-program failures; configuration
/// or system failures are reported through ``setup_failed``/``setup_error``.
[[nodiscard]] RunResult run(const RunRequest& request);

/// Run a compiler-like helper with rlimits and a wall deadline but without the
/// user-program namespace/seccomp sandbox.
[[nodiscard]] RunResult run_bounded_process(const std::string& executable,
                                            const std::vector<std::string>& arguments,
                                            const std::string& work_dir,
                                            const std::string& stdin_path,
                                            const std::string& stdout_path,
                                            const std::string& stderr_path, const Limits& limits,
                                            bool use_cgroup);

}  // namespace algoos::sandbox
