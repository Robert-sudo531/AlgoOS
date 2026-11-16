#include "algoos/runner/comparator.hpp"

#include <algorithm>
#include <array>
#include <fstream>
#include <string>
#include <vector>

namespace algoos::runner {

std::string normalize_common(std::string_view bytes) {
  std::string result;
  result.reserve(bytes.size());
  std::size_t start = 0;
  auto append_line = [&](std::size_t end) {
    while (end > start && (bytes[end - 1] == ' ' || bytes[end - 1] == '\t')) --end;
    result.append(bytes.substr(start, end - start));
  };
  for (std::size_t index = 0; index < bytes.size(); ++index) {
    const char value = bytes[index];
    if (value != '\r' && value != '\n') continue;
    append_line(index);
    result.push_back('\n');
    if (value == '\r' && index + 1 < bytes.size() && bytes[index + 1] == '\n') ++index;
    start = index + 1;
  }
  append_line(bytes.size());
  while (!result.empty() && result.back() == '\n') result.pop_back();
  return result;
}

std::optional<std::string> read_bounded(const std::string& path, int64_t cap, bool& truncated,
                                        std::string& error) {
  truncated = false;
  std::ifstream input(path, std::ios::binary);
  if (!input.good()) {
    error = "cannot open: " + path;
    return std::nullopt;
  }
  if (cap <= 0) {
    error = "invalid read cap";
    return std::nullopt;
  }
  std::string bytes(static_cast<std::size_t>(cap), '\0');
  input.read(bytes.data(), cap);
  bytes.resize(static_cast<std::size_t>(input.gcount()));
  char extra = '\0';
  if (input.get(extra)) truncated = true;
  if (input.bad()) {
    error = "cannot read: " + path;
    return std::nullopt;
  }
  return bytes;
}

std::optional<int64_t> first_difference_line(std::string_view actual, std::string_view expected,
                                             int limit) {
  std::size_t a = 0;
  std::size_t b = 0;
  int64_t line = 1;
  while (a < actual.size() && b < expected.size() && line <= limit) {
    const auto end_a = actual.find('\n', a);
    const auto end_b = expected.find('\n', b);
    const std::string_view line_a =
        actual.substr(a, end_a == std::string_view::npos ? actual.size() - a : end_a - a);
    const std::string_view line_b =
        expected.substr(b, end_b == std::string_view::npos ? expected.size() - b : end_b - b);
    if (line_a != line_b) return line;
    if (end_a == std::string_view::npos || end_b == std::string_view::npos) return std::nullopt;
    a = end_a + 1;
    b = end_b + 1;
    ++line;
  }
  if (a < actual.size() || b < expected.size()) return line;
  return std::nullopt;
}

CompareResult compare_common(std::string_view actual, std::string_view expected,
                             int64_t out_limit) {
  CompareResult result;
  result.actual_bytes = static_cast<int64_t>(actual.size());
  if (result.actual_bytes > out_limit) {
    result.over_limit = true;
    result.detail = "output exceeded limit " + std::to_string(out_limit);
    return result;
  }
  result.matches = actual == expected;
  if (!result.matches) {
    result.hint_line = first_difference_line(actual, expected);
    result.detail = result.hint_line ? "first difference at line " + std::to_string(*result.hint_line)
                                     : "output differs from the standard answer";
  }
  return result;
}

}  // namespace algoos::runner
