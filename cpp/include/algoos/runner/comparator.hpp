#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace algoos::runner {

/// common comparison: normalize CRLF/CR to LF, strip trailing spaces and
/// tabs on each line, drop trailing blank lines, then require byte equality.
[[nodiscard]] std::string normalize_common(std::string_view bytes);

struct CompareResult {
  bool matches{false};
  int64_t actual_bytes{0};
  bool over_limit{false};
  std::optional<int64_t> hint_line;
  std::string detail;
};

/// Read at most ``cap`` bytes.  ``truncated`` is set when the file is larger,
/// which lets the caller report OLE without loading the whole file.
[[nodiscard]] std::optional<std::string> read_bounded(const std::string& path, int64_t cap,
                                                      bool& truncated, std::string& error);

[[nodiscard]] CompareResult compare_common(std::string_view actual, std::string_view expected,
                                           int64_t out_limit);

/// Best-effort first differing line, used for a compact ``detail`` string.
[[nodiscard]] std::optional<int64_t> first_difference_line(std::string_view actual,
                                                           std::string_view expected, int limit = 16);

}  // namespace algoos::runner
