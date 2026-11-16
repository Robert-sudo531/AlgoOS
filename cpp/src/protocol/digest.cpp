#include "algoos/protocol/digest.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <regex>
#include <string>
#include <utility>
#include <vector>

namespace algoos::protocol {
namespace {

constexpr std::array<uint32_t, 64> kRoundConstants = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

uint32_t rotr(uint32_t value, uint32_t count) {
  return (value >> count) | (value << (32 - count));
}

std::string hex(const std::array<uint8_t, 32>& bytes) {
  static const char digits[] = "0123456789abcdef";
  std::string result;
  result.reserve(64);
  for (uint8_t byte : bytes) {
    result.push_back(digits[byte >> 4]);
    result.push_back(digits[byte & 15]);
  }
  return result;
}

}  // namespace

std::string sha256_hex(std::string_view bytes) {
  std::array<uint32_t, 8> state = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                   0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
  std::vector<uint8_t> block;
  block.reserve(((bytes.size() + 72) / 64) * 64);
  block.assign(bytes.begin(), bytes.end());
  block.push_back(0x80);
  while (block.size() % 64 != 56) block.push_back(0x00);
  const uint64_t length = static_cast<uint64_t>(bytes.size()) * 8ULL;
  for (int shift = 56; shift >= 0; shift -= 8)
    block.push_back(static_cast<uint8_t>((length >> shift) & 0xff));

  for (std::size_t offset = 0; offset < block.size(); offset += 64) {
    std::array<uint32_t, 64> words{};
    for (std::size_t index = 0; index < 16; ++index) {
      const auto base = offset + index * 4;
      words[index] = (static_cast<uint32_t>(block[base]) << 24) |
                     (static_cast<uint32_t>(block[base + 1]) << 16) |
                     (static_cast<uint32_t>(block[base + 2]) << 8) |
                     static_cast<uint32_t>(block[base + 3]);
    }
    for (std::size_t index = 16; index < 64; ++index) {
      const uint32_t s0 = rotr(words[index - 15], 7) ^ rotr(words[index - 15], 18) ^
                          (words[index - 15] >> 3);
      const uint32_t s1 = rotr(words[index - 2], 17) ^ rotr(words[index - 2], 19) ^
                          (words[index - 2] >> 10);
      words[index] = words[index - 16] + s0 + words[index - 7] + s1;
    }
    std::array<uint32_t, 8> work = state;
    for (std::size_t index = 0; index < 64; ++index) {
      const uint32_t s1 = rotr(work[4], 6) ^ rotr(work[4], 11) ^ rotr(work[4], 25);
      const uint32_t choice = (work[4] & work[5]) ^ (~work[4] & work[6]);
      const uint32_t temp1 = work[7] + s1 + choice + kRoundConstants[index] + words[index];
      const uint32_t s0 = rotr(work[0], 2) ^ rotr(work[0], 13) ^ rotr(work[0], 22);
      const uint32_t majority = (work[0] & work[1]) ^ (work[0] & work[2]) ^ (work[1] & work[2]);
      const uint32_t temp2 = s0 + majority;
      work[7] = work[6];
      work[6] = work[5];
      work[5] = work[4];
      work[4] = work[3] + temp1;
      work[3] = work[2];
      work[2] = work[1];
      work[1] = work[0];
      work[0] = temp1 + temp2;
    }
    for (std::size_t index = 0; index < 8; ++index) state[index] += work[index];
  }

  std::array<uint8_t, 32> output{};
  for (std::size_t index = 0; index < 8; ++index) {
    output[index * 4] = static_cast<uint8_t>((state[index] >> 24) & 0xff);
    output[index * 4 + 1] = static_cast<uint8_t>((state[index] >> 16) & 0xff);
    output[index * 4 + 2] = static_cast<uint8_t>((state[index] >> 8) & 0xff);
    output[index * 4 + 3] = static_cast<uint8_t>(state[index] & 0xff);
  }
  return hex(output);
}

std::string canonical_json(const std::vector<std::pair<std::string, std::string>>& fields) {
  std::vector<std::pair<std::string, std::string>> sorted = fields;
  std::sort(sorted.begin(), sorted.end());
  std::string result = "{";
  bool first = true;
  for (const auto& [key, value] : sorted) {
    if (!first) result.push_back(',');
    first = false;
    result.push_back('"');
    result += key;
    result += "\":\"";
    result += value;
    result.push_back('"');
  }
  result.push_back('}');
  return result;
}

std::string hash_field(std::string_view bytes) { return "sha256:" + sha256_hex(bytes); }

bool valid_hash(std::string_view value) {
  static const std::regex pattern("sha256:[0-9a-f]{64}");
  return std::regex_match(std::string(value), pattern);
}

}  // namespace algoos::protocol
