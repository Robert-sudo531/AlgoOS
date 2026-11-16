#pragma once

#include <optional>
#include <string>
#include <string_view>

#include "algoos/sandbox/types.hpp"

namespace algoos::sandbox {

enum class Verdict { AC, WA, TLE, MLE, RE, OLE, CE, SE, CANCELLED, NONE };
enum class Reason {
  NONE,
  ANSWER_MISMATCH,
  OUTPUT_LIMIT,
  OLE_TRUNCATED,
  WALL_TIMEOUT,
  CPU_TIMEOUT,
  OOM_KILL,
  MEM_PEAK_OVER,
  CRASH_SIGNAL,
  NONZERO_EXIT,
  SHORT_CIRCUITED,
  USER_CANCELLED,
  COMPILE_ERROR,
  COMPILE_TIMEOUT,
  COMPILE_LIMIT,
  SYSTEM_ERROR,
  SPECIAL_WA,
  NO_NODE
};

struct Decision {
  Verdict verdict{Verdict::NONE};
  Reason reason{Reason::NONE};
};

[[nodiscard]] std::string_view name(Verdict verdict);
[[nodiscard]] std::string_view name(Reason reason);
[[nodiscard]] bool parse_verdict(std::string_view text, Verdict& out);
[[nodiscard]] bool parse_reason(std::string_view text, Reason& out);

/// Single verdict table shared by every runner path.  Returns ``nullopt`` only
/// when the process survived every resource check and output comparison is
/// still required.
[[nodiscard]] std::optional<Decision> classify(const Evidence& evidence, const Limits& limits);

/// Map a result from a previously classified process to the verdict/reason
/// pair after output comparison (AC/WA).
[[nodiscard]] Decision compare_decision(const Evidence& evidence, const Limits& limits,
                                        bool matches);

}  // namespace algoos::sandbox
