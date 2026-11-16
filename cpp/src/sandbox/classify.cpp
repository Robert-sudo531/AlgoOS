#include "algoos/sandbox/classify.hpp"

#include <csignal>
#include <map>
#include <stdexcept>

namespace algoos::sandbox {

std::string_view name(Verdict verdict) {
  switch (verdict) {
    case Verdict::AC: return "AC";
    case Verdict::WA: return "WA";
    case Verdict::TLE: return "TLE";
    case Verdict::MLE: return "MLE";
    case Verdict::RE: return "RE";
    case Verdict::OLE: return "OLE";
    case Verdict::CE: return "CE";
    case Verdict::SE: return "SE";
    case Verdict::CANCELLED: return "CANCELLED";
    case Verdict::NONE: return "NONE";
  }
  return "NONE";
}

std::string_view name(Reason reason) {
  switch (reason) {
    case Reason::NONE: return "NONE";
    case Reason::ANSWER_MISMATCH: return "ANSWER_MISMATCH";
    case Reason::OUTPUT_LIMIT: return "OUTPUT_LIMIT";
    case Reason::OLE_TRUNCATED: return "OLE_TRUNCATED";
    case Reason::WALL_TIMEOUT: return "WALL_TIMEOUT";
    case Reason::CPU_TIMEOUT: return "CPU_TIMEOUT";
    case Reason::OOM_KILL: return "OOM_KILL";
    case Reason::MEM_PEAK_OVER: return "MEM_PEAK_OVER";
    case Reason::CRASH_SIGNAL: return "CRASH_SIGNAL";
    case Reason::NONZERO_EXIT: return "NONZERO_EXIT";
    case Reason::SHORT_CIRCUITED: return "SHORT_CIRCUITED";
    case Reason::USER_CANCELLED: return "USER_CANCELLED";
    case Reason::COMPILE_ERROR: return "COMPILE_ERROR";
    case Reason::COMPILE_TIMEOUT: return "COMPILE_TIMEOUT";
    case Reason::COMPILE_LIMIT: return "COMPILE_LIMIT";
    case Reason::SYSTEM_ERROR: return "SYSTEM_ERROR";
    case Reason::SPECIAL_WA: return "SPECIAL_WA";
    case Reason::NO_NODE: return "NO_NODE";
  }
  return "SYSTEM_ERROR";
}

bool parse_verdict(std::string_view text, Verdict& out) {
  static const std::map<std::string_view, Verdict> table = {
      {"AC", Verdict::AC},         {"WA", Verdict::WA},       {"TLE", Verdict::TLE},
      {"MLE", Verdict::MLE},       {"RE", Verdict::RE},       {"OLE", Verdict::OLE},
      {"CE", Verdict::CE},         {"SE", Verdict::SE},       {"CANCELLED", Verdict::CANCELLED},
  };
  auto it = table.find(text);
  if (it == table.end()) return false;
  out = it->second;
  return true;
}

bool parse_reason(std::string_view text, Reason& out) {
  static const std::map<std::string_view, Reason> table = {
      {"NONE", Reason::NONE},
      {"ANSWER_MISMATCH", Reason::ANSWER_MISMATCH},
      {"OUTPUT_LIMIT", Reason::OUTPUT_LIMIT},
      {"OLE_TRUNCATED", Reason::OLE_TRUNCATED},
      {"WALL_TIMEOUT", Reason::WALL_TIMEOUT},
      {"CPU_TIMEOUT", Reason::CPU_TIMEOUT},
      {"OOM_KILL", Reason::OOM_KILL},
      {"MEM_PEAK_OVER", Reason::MEM_PEAK_OVER},
      {"CRASH_SIGNAL", Reason::CRASH_SIGNAL},
      {"NONZERO_EXIT", Reason::NONZERO_EXIT},
      {"SHORT_CIRCUITED", Reason::SHORT_CIRCUITED},
      {"USER_CANCELLED", Reason::USER_CANCELLED},
      {"COMPILE_ERROR", Reason::COMPILE_ERROR},
      {"COMPILE_TIMEOUT", Reason::COMPILE_TIMEOUT},
      {"COMPILE_LIMIT", Reason::COMPILE_LIMIT},
      {"SYSTEM_ERROR", Reason::SYSTEM_ERROR},
      {"SPECIAL_WA", Reason::SPECIAL_WA},
      {"NO_NODE", Reason::NO_NODE},
  };
  auto it = table.find(text);
  if (it == table.end()) return false;
  out = it->second;
  return true;
}

namespace {

bool crash_signal(int signal) {
  switch (signal) {
    case SIGABRT:
    case SIGBUS:
    case SIGFPE:
    case SIGILL:
    case SIGSEGV:
    case SIGSYS:
    case SIGTRAP:
      return true;
    default:
      return false;
  }
}

int64_t effective_memory_peak(const Evidence& evidence) {
  if (evidence.cgroup_memory_peak) return *evidence.cgroup_memory_peak;
  return evidence.ru_maxrss_bytes;
}

}  // namespace

std::optional<Decision> classify(const Evidence& evidence, const Limits& limits) {
  // Priority follows policy_v1.json result.classification_priority.
  if (evidence.setup_failed) return Decision{Verdict::SE, Reason::SYSTEM_ERROR};
  if (evidence.oom_kill > 0) return Decision{Verdict::MLE, Reason::OOM_KILL};
  if (evidence.wall_dog_fired) return Decision{Verdict::TLE, Reason::WALL_TIMEOUT};
  if (evidence.wifsignaled && evidence.term_sig == SIGXCPU) {
    return Decision{Verdict::TLE, Reason::CPU_TIMEOUT};
  }
  // A bare SIGKILL is not proof of a CPU limit; require measured CPU evidence.
  if (evidence.wifsignaled && evidence.term_sig == SIGKILL && evidence.cpu_ms > limits.cpu_ms) {
    return Decision{Verdict::TLE, Reason::CPU_TIMEOUT};
  }
  if (evidence.wifsignaled && evidence.term_sig == SIGXFSZ) {
    return Decision{Verdict::OLE, Reason::OLE_TRUNCATED};
  }
  if (evidence.wifsignaled && crash_signal(evidence.term_sig)) {
    return Decision{Verdict::RE, Reason::CRASH_SIGNAL};
  }
  if (evidence.wifsignaled) return Decision{Verdict::RE, Reason::CRASH_SIGNAL};
  if (evidence.wifexited && evidence.exit_code != 0) {
    return Decision{Verdict::RE, Reason::NONZERO_EXIT};
  }
  if (effective_memory_peak(evidence) > limits.mem_bytes) {
    return Decision{Verdict::MLE, Reason::MEM_PEAK_OVER};
  }
  if (evidence.cpu_ms > limits.cpu_ms) return Decision{Verdict::TLE, Reason::CPU_TIMEOUT};
  if (evidence.out_size > limits.out_bytes) return Decision{Verdict::OLE, Reason::OLE_TRUNCATED};
  return std::nullopt;
}

Decision compare_decision(const Evidence& evidence, const Limits& limits, bool matches) {
  if (auto decision = classify(evidence, limits)) return *decision;
  return matches ? Decision{Verdict::AC, Reason::NONE}
                 : Decision{Verdict::WA, Reason::ANSWER_MISMATCH};
}

}  // namespace algoos::sandbox
