#pragma once

#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <vector>

namespace algoos::sandbox {

/// Per-test resource budget.  Timings are milliseconds and sizes are bytes.
struct Limits {
  int64_t cpu_ms{0};
  int64_t wall_ms{0};
  int64_t mem_bytes{0};
  int64_t out_bytes{0};
};

/// Runtime sandbox switches.  A deployment may disable cgroup or seccomp for
/// an unprivileged development host; the effective mode is reported back to
/// the control plane so synthetic evidence is never presented as production
/// isolation.
struct SandboxConfig {
  bool use_cgroup{true};
  bool use_seccomp{true};
  bool use_namespaces{true};
  bool readonly_root{true};
  std::string cgroup_root{"/sys/fs/cgroup"};
  std::string cgroup_parent{"algoos"};
  int64_t tmpfs_bytes{256LL << 20};
  int64_t fsize_slack_bytes{4096};
};

struct RunRequest {
  std::string executable;
  std::string input;    ///< malformed/absent input is a setup error
  std::string work_dir; ///< parent creates it; child gets a tmpfs mount here
  Limits limits;
  SandboxConfig config;
  /// Polled by the watchdog between wait4 calls; returning true kills the run
  /// and marks it cancelled in the evidence.
  std::function<bool()> cancelled;
  /// Invoked with the host pid immediately after clone, before cgroup attach.
  std::function<void(int64_t)> on_start;
  /// Optional argv passed to the executable (checker mode).  Empty means no
  /// extra arguments; stdin is still redirected to ``input``.
  std::vector<std::string> arguments;
};

/// Raw measurements collected from wait4 and cgroup v2.  This is evidence, not
/// a verdict; the single ``classify`` function maps it to verdict + reason.
struct Evidence {
  bool started{false};
  bool setup_failed{false};
  bool wifexited{false};
  int exit_code{0};
  bool wifsignaled{false};
  int term_sig{0};
  int64_t oom_kill{0};
  std::optional<int64_t> cgroup_memory_peak;
  int64_t ru_maxrss_bytes{0};
  bool wall_dog_fired{false};
  bool cancelled{false};
  int64_t cpu_ms{0};
  int64_t wall_ms{0};
  int64_t out_size{0};
};

struct RunResult {
  Evidence evidence;
  std::string stderr_tail;
  std::string setup_error;
  std::string sandbox_mode;  ///< "full" | "partial" | "none"
  std::vector<std::string> warnings;
};

/// The sandbox layers actually applied, e.g. {"cgroup","ns","seccomp","rlimit"}.
[[nodiscard]] std::vector<std::string> layers(const RunResult& result);

}  // namespace algoos::sandbox
