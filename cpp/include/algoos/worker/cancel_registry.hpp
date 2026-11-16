#pragma once

#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <string>

namespace algoos::worker {

/// Tracks submission-level and case-level cancellation.  The same registry is
/// consulted before spawning a run and inside the sandbox watchdog, so a cancel
/// arriving while a case is in flight kills the process group immediately.
class CancelRegistry {
 public:
  enum class Kind { none, submission, case_level };

  /// Record a cancellation.  ``boundary`` limits short-circuit cancellation to
  /// sequences greater than the failing case (ordered short circuit); when it
  /// is nullopt the whole submission is cancelled.
  void cancel_submission(const std::string& file_id, std::optional<int64_t> boundary,
                         std::string reason);
  void cancel_case(const std::string& file_id, const std::string& judge_id, std::string reason);

  /// Returns true when this (file, judge_id, sequence) must not run.
  [[nodiscard]] bool cancelled(const std::string& file_id, const std::string& judge_id,
                               int64_t sequence) const;

  /// Reason attached to the active cancellation (SHORT_CIRCUITED/USER_CANCELLED).
  [[nodiscard]] std::string reason(const std::string& file_id, const std::string& judge_id,
                                   int64_t sequence) const;

  /// Register an in-flight process.  Returns false when the case was cancelled
  /// while it was being prepared; the caller must not start it.
  bool attach(const std::string& file_id, const std::string& judge_id, int64_t sequence,
              int64_t pid, int64_t host_pid);
  void detach(const std::string& file_id, const std::string& judge_id, int64_t sequence);

  void clear_submission(const std::string& file_id);
  [[nodiscard]] std::size_t active_count() const;

 private:
  struct SubmissionState {
    bool all{false};
    std::optional<int64_t> boundary;
    std::set<std::string> cases;
    std::string reason{"SHORT_CIRCUITED"};
  };

  [[nodiscard]] bool cancelled_locked(const SubmissionState& state, const std::string& judge_id,
                                      int64_t sequence) const;

  mutable std::mutex mutex_;
  std::map<std::string, SubmissionState> submissions_;
  // key: file_id + "\x1f" + judge_id + "\x1f" + sequence
  std::map<std::string, int64_t> active_host_pids_;
};

}  // namespace algoos::worker
