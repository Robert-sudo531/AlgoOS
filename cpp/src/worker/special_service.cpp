#include "algoos/worker/special_service.hpp"

#include <chrono>
#include <filesystem>
#include <random>

#include "algoos/sandbox/classify.hpp"
#include "algoos/sandbox/sandbox.hpp"

namespace algoos::worker {
namespace {
namespace fs = std::filesystem;

std::string unique_name() {
  static std::random_device device;
  static std::mt19937_64 generator(device());
  return std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + "-" +
         std::to_string(generator());
}

std::string tail(std::string value, std::size_t maximum) {
  if (value.size() <= maximum) return value;
  return value.substr(value.size() - maximum);
}
}  // namespace

SpecialService::SpecialService(Config config) : config_(std::move(config)) {}

SpecialOutcome SpecialService::judge(const protocol::SpecialTask& task,
                                     const SpecialPaths& paths) {
  SpecialOutcome outcome;
  const auto work = fs::path(config_.work_root) / ("special-" + unique_name());
  fs::create_directories(work);

  sandbox::RunRequest request;
  request.executable = paths.checker;
  request.input = "/dev/null";
  request.work_dir = work.string();
  request.limits = task.limits;
  request.config = config_.sandbox;
  request.arguments = {paths.input, paths.output, paths.answer};

  const auto run = sandbox::run(request);
  outcome.cpu_ms = run.evidence.cpu_ms;
  outcome.wall_ms = run.evidence.wall_ms;
  outcome.stderr_tail = run.stderr_tail;
  fs::remove_all(work);

  if (run.evidence.setup_failed) {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = run.setup_error;
    return outcome;
  }
  if (run.evidence.wifsignaled) {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = "checker terminated by signal " + std::to_string(run.evidence.term_sig);
    return outcome;
  }
  if (run.evidence.wall_dog_fired || run.evidence.cpu_ms > task.limits.cpu_ms ||
      (run.evidence.cgroup_memory_peak &&
       *run.evidence.cgroup_memory_peak > task.limits.mem_bytes)) {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = "checker exceeded its resource budget";
    return outcome;
  }
  if (!run.evidence.wifexited) {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = "checker has no exit status";
    return outcome;
  }
  if (run.evidence.exit_code == 0) {
    outcome.verdict = "AC";
    outcome.reason = "NONE";
  } else if (run.evidence.exit_code == 1) {
    outcome.verdict = "WA";
    outcome.reason = "SPECIAL_WA";
    outcome.detail = tail(run.stderr_tail, 512);
  } else {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = "checker exited with code " + std::to_string(run.evidence.exit_code);
  }
  return outcome;
}

}  // namespace algoos::worker
