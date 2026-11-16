#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace algoos::protocol {

/// Lowercase hex SHA-256 of the given bytes.
[[nodiscard]] std::string sha256_hex(std::string_view bytes);

/// Canonical JSON (sorted keys, no whitespace) used for build-cache keys.
[[nodiscard]] std::string canonical_json(const std::vector<std::pair<std::string, std::string>>& fields);

/// ``sha256:<hex>`` wire form.
[[nodiscard]] std::string hash_field(std::string_view bytes);

[[nodiscard]] bool valid_hash(std::string_view value);

}  // namespace algoos::protocol
