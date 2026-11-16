#include "algoos/worker/eval_service.hpp"

#include <chrono>
#include <filesystem>
#include <fstream>
#include <random>
#include <stdexcept>

#include "algoos/runner/comparator.hpp"
#include "algoos/sandbox/classify.hpp"
#include "algoos/sandbox/sandbox.hpp"

namespace algoos::worker {
namespace {
namespace fs = std::filesystem;
using Decision = sandbox::Decision;
using Verdict = sandbox::Verdict;
using Reason = sandbox::Reason;

constexpr int64_t kAnswerCap = 64LL << 20;

std::string unique_name() {
  static std::random_device device;
  static std::mt19937_64 generator(device());
  return std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + "-" +
         std::to_string(generator());
}

}  // namespace

EvalService::EvalService(Config config, CancelRegistry& registry)
    : config_(std::move(config)), registry_(registry) {}

RawOutcome EvalService::run_raw(const protocol::EvalTask& task, const EvalPaths& paths) {
  RawOutcome outcome;
  outcome.memory_source = "rss_peak";
  if (registry_.cancelled(task.file_id, task.judge_id, task.sequence)) {
    outcome.cancelled = true;
    outcome.verdict = "CANCELLED";
    outcome.reason = registry_.reason(task.file_id, task.judge_id, task.sequence);
    return outcome;
  }
  const auto work = fs::path(config_.work_root) / ("raw-" + unique_name());
  fs::create_directories(work);
  sandbox::RunRequest request;
  request.executable = paths.binary;
  request.input = paths.input;
  request.work_dir = work.string();
  request.limits = task.limits;
  request.config = config_.sandbox;
  request.cancelled = [&] {
    return registry_.cancelled(task.file_id, task.judge_id, task.sequence);
  };
  request.on_start = [&](int64_t host_pid) {
    registry_.attach(task.file_id, task.judge_id, task.sequence, host_pid, host_pid);
  };
  const auto run = sandbox::run(request);
  registry_.detach(task.file_id, task.judge_id, task.sequence);
  outcome.evidence = run.evidence;
  outcome.stderr_tail = run.stderr_tail;
  outcome.work_dir = work.string();
  outcome.out_path = (work / "stdout.out").string();
  if (registry_.cancelled(task.file_id, task.judge_id, task.sequence)) {
    outcome.cancelled = true;
    outcome.verdict = "CANCELLED";
    outcome.reason = registry_.reason(task.file_id, task.judge_id, task.sequence);
    return outcome;
  }
  if (run.evidence.setup_failed) {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = run.setup_error;
    return outcome;
  }
  try {
    outcome.out_bytes = static_cast<int64_t>(fs::file_size(outcome.out_path));
  } catch (const std::exception&) {
    outcome.out_bytes = 0;
  }
  if (run.evidence.cgroup_memory_peak) outcome.memory_source = "cgroup_peak";
  if (auto decision = sandbox::classify(run.evidence, task.limits)) {
    outcome.verdict = std::string(sandbox::name(decision->verdict));
    outcome.reason = std::string(sandbox::name(decision->reason));
    if (config_.merge_ole && decision->verdict == Verdict::OLE) {
      outcome.verdict = "WA";
      outcome.reason = "OUTPUT_LIMIT";
    }
    return outcome;
  }
  outcome.comparable = true;
  return outcome;
}

EvalOutcome EvalService::judge(const protocol::EvalTask& task, const EvalPaths& paths) {
  EvalOutcome outcome;
  const auto& limits = task.limits;
  outcome.memory_source = "rss_peak";

  if (registry_.cancelled(task.file_id, task.judge_id, task.sequence)) {
    outcome.verdict = "CANCELLED";
    outcome.reason = registry_.reason(task.file_id, task.judge_id, task.sequence);
    outcome.short_circuited = outcome.reason == "SHORT_CIRCUITED";
    return outcome;
  }

  // The answer defines the semantic output limit.  A mismatch means the
  // frozen evaluation plan and the test bundle disagree: report SE, never
  // silently compare against a different limit.
  bool answer_truncated = false;
  std::string read_error;
  const auto answer_bytes =
      runner::read_bounded(paths.answer, kAnswerCap, answer_truncated, read_error);
  if (!answer_bytes) {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = read_error;
    return outcome;
  }
  if (answer_truncated) {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = "standard answer exceeds the local read cap";
    return outcome;
  }
  const auto expected = runner::normalize_common(*answer_bytes);
  if (static_cast<int64_t>(expected.size()) != limits.out_bytes) {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = "plan out_bytes does not match the normalized answer";
    return outcome;
  }

  const auto work = fs::path(config_.work_root) / ("case-" + unique_name());
  fs::create_directories(work);

  sandbox::RunRequest request;
  request.executable = paths.binary;
  request.input = paths.input;
  request.work_dir = work.string();
  request.limits = limits;
  request.config = config_.sandbox;
  request.cancelled = [&] {
    return registry_.cancelled(task.file_id, task.judge_id, task.sequence);
  };
  request.on_start = [&](int64_t host_pid) {
    registry_.attach(task.file_id, task.judge_id, task.sequence, host_pid, host_pid);
  };

  const auto run = sandbox::run(request);
  registry_.detach(task.file_id, task.judge_id, task.sequence);
  outcome.evidence = run.evidence;
  outcome.stderr_tail = run.stderr_tail;

  if (registry_.cancelled(task.file_id, task.judge_id, task.sequence)) {
    outcome.verdict = "CANCELLED";
    outcome.reason = registry_.reason(task.file_id, task.judge_id, task.sequence);
    outcome.short_circuited = outcome.reason == "SHORT_CIRCUITED";
    fs::remove_all(work);
    return outcome;
  }
  if (run.evidence.setup_failed) {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = run.setup_error;
    fs::remove_all(work);
    return outcome;
  }

  bool truncated = false;
  const int64_t fsize_cap =
      std::min<int64_t>(256LL << 20, limits.out_bytes + config_.sandbox.fsize_slack_bytes);
  read_error.clear();
  const auto actual_bytes =
      runner::read_bounded((work / "stdout.out").string(), fsize_cap, truncated, read_error);
  if (!actual_bytes) {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = read_error;
    fs::remove_all(work);
    return outcome;
  }
  const auto actual = runner::normalize_common(*actual_bytes);
  outcome.out_bytes = static_cast<int64_t>(actual.size());
  if (truncated) outcome.out_bytes = std::max<int64_t>(outcome.out_bytes, fsize_cap + 1);
  if (run.evidence.cgroup_memory_peak) outcome.memory_source = "cgroup_peak";

  // The semantic limit is the normalized answer length. The file cap and
  // its SIGXFSZ evidence still apply independently to the raw stream.
  outcome.evidence.out_size = outcome.out_bytes;
  if (auto decision = sandbox::classify(outcome.evidence, limits)) {
    outcome.verdict = std::string(sandbox::name(decision->verdict));
    outcome.reason = std::string(sandbox::name(decision->reason));
    if (config_.merge_ole && decision->verdict == Verdict::OLE) {
      outcome.verdict = "WA";
      outcome.reason = "OUTPUT_LIMIT";
    }
  } else {
    const auto compare = runner::compare_common(actual, expected, limits.out_bytes);
    if (compare.over_limit) {
      outcome.verdict = config_.merge_ole ? "WA" : "OLE";
      outcome.reason = config_.merge_ole ? "OUTPUT_LIMIT" : "OLE_TRUNCATED";
      outcome.detail = compare.detail;
      outcome.out_bytes = compare.actual_bytes;
    } else if (compare.matches) {
      outcome.verdict = "AC";
      outcome.reason = "NONE";
    } else {
      outcome.verdict = "WA";
      outcome.reason = "ANSWER_MISMATCH";
      outcome.detail = compare.detail;
    }
  }
  fs::remove_all(work);
  return outcome;
}

}  // namespace algoos::worker
