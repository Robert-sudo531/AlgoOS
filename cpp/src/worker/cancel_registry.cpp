#include "algoos/worker/cancel_registry.hpp"

#include <signal.h>
#include <unistd.h>

namespace algoos::worker {
namespace {

std::string active_key(const std::string& file_id, const std::string& judge_id, int64_t sequence) {
  return file_id + "\x1f" + judge_id + "\x1f" + std::to_string(sequence);
}

void kill_group(int64_t host_pid) {
  if (host_pid <= 0) return;
  const auto pid = static_cast<pid_t>(host_pid);
  (void)kill(-pid, SIGKILL);
  (void)kill(pid, SIGKILL);
}

}  // namespace

void CancelRegistry::cancel_submission(const std::string& file_id,
                                       std::optional<int64_t> boundary, std::string reason) {
  std::lock_guard guard(mutex_);
  auto& state = submissions_[file_id];
  state.boundary = boundary;
  state.all = boundary.has_value() == false;
  state.reason = std::move(reason);
  for (auto it = active_host_pids_.begin(); it != active_host_pids_.end();) {
    if (it->first.rfind(file_id + "\x1f", 0) == 0) {
      kill_group(it->second);
      it = active_host_pids_.erase(it);
    } else {
      ++it;
    }
  }
}

void CancelRegistry::cancel_case(const std::string& file_id, const std::string& judge_id,
                                 std::string reason) {
  std::lock_guard guard(mutex_);
  auto& state = submissions_[file_id];
  state.cases.insert(judge_id);
  state.reason = std::move(reason);
  for (auto it = active_host_pids_.begin(); it != active_host_pids_.end();) {
    const std::string prefix = file_id + "\x1f" + judge_id + "\x1f";
    if (it->first.rfind(prefix, 0) == 0) {
      kill_group(it->second);
      it = active_host_pids_.erase(it);
    } else {
      ++it;
    }
  }
}

bool CancelRegistry::cancelled_locked(const SubmissionState& state, const std::string& judge_id,
                                      int64_t sequence) const {
  if (state.all) return true;
  if (state.boundary && sequence > *state.boundary) return true;
  return state.cases.find(judge_id) != state.cases.end();
}

bool CancelRegistry::cancelled(const std::string& file_id, const std::string& judge_id,
                               int64_t sequence) const {
  std::lock_guard guard(mutex_);
  auto it = submissions_.find(file_id);
  if (it == submissions_.end()) return false;
  return cancelled_locked(it->second, judge_id, sequence);
}

std::string CancelRegistry::reason(const std::string& file_id, const std::string& judge_id,
                                   int64_t sequence) const {
  std::lock_guard guard(mutex_);
  auto it = submissions_.find(file_id);
  if (it == submissions_.end()) return "USER_CANCELLED";
  if (!cancelled_locked(it->second, judge_id, sequence)) return "USER_CANCELLED";
  return it->second.reason;
}

bool CancelRegistry::attach(const std::string& file_id, const std::string& judge_id,
                            int64_t sequence, int64_t pid, int64_t host_pid) {
  std::lock_guard guard(mutex_);
  auto it = submissions_.find(file_id);
  if (it != submissions_.end() && cancelled_locked(it->second, judge_id, sequence)) {
    kill_group(host_pid);
    return false;
  }
  active_host_pids_[active_key(file_id, judge_id, sequence)] = host_pid;
  (void)pid;
  return true;
}

void CancelRegistry::detach(const std::string& file_id, const std::string& judge_id,
                            int64_t sequence) {
  std::lock_guard guard(mutex_);
  active_host_pids_.erase(active_key(file_id, judge_id, sequence));
}

void CancelRegistry::clear_submission(const std::string& file_id) {
  std::lock_guard guard(mutex_);
  submissions_.erase(file_id);
}

std::size_t CancelRegistry::active_count() const {
  std::lock_guard guard(mutex_);
  return active_host_pids_.size();
}

}  // namespace algoos::worker
