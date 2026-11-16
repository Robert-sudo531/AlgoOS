#include "algoos/protocol/json.hpp"

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <map>
#include <set>

namespace algoos::protocol {
namespace {

void check(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

// json-c's strict tokener accepts escaped unicode but rejects raw multi-byte
// UTF-8 in old builds; escape it first, and keep the strict grammar.
std::string ascii_escape(std::string_view wire) {
  std::string result;
  result.reserve(wire.size() + wire.size() / 8);
  bool in_string = false;
  bool escaped = false;
  auto append_unit = [&](uint32_t unit) {
    constexpr char hex[] = "0123456789abcdef";
    result += "\\u";
    for (int shift : {12, 8, 4, 0}) result += hex[(unit >> shift) & 15];
  };
  for (std::size_t i = 0; i < wire.size();) {
    auto lead = static_cast<unsigned char>(wire[i++]);
    if (lead < 0x80) {
      result += static_cast<char>(lead);
      if (lead == '"' && !escaped) in_string = !in_string;
      escaped = in_string && lead == '\\' && !escaped;
      continue;
    }
    check(in_string && !escaped, "non-ASCII outside a JSON string");
    int remaining;
    uint32_t code;
    uint32_t minimum;
    if (lead >= 0xc2 && lead <= 0xdf) {
      remaining = 1;
      code = lead & 0x1f;
      minimum = 0x80;
    } else if (lead >= 0xe0 && lead <= 0xef) {
      remaining = 2;
      code = lead & 0x0f;
      minimum = 0x800;
    } else if (lead >= 0xf0 && lead <= 0xf4) {
      remaining = 3;
      code = lead & 0x07;
      minimum = 0x10000;
    } else {
      throw std::runtime_error("invalid UTF-8 leading byte");
    }
    check(i + static_cast<std::size_t>(remaining) <= wire.size(), "incomplete UTF-8 sequence");
    while (remaining-- > 0) {
      auto byte = static_cast<unsigned char>(wire[i++]);
      check(byte >= 0x80 && byte <= 0xbf, "invalid UTF-8 continuation byte");
      code = (code << 6) | (byte & 0x3f);
    }
    check(code >= minimum && code <= 0x10ffff && (code < 0xd800 || code > 0xdfff),
          "invalid UTF-8 code point");
    if (code <= 0xffff) {
      append_unit(code);
    } else {
      code -= 0x10000;
      append_unit(0xd800 + (code >> 10));
      append_unit(0xdc00 + (code & 0x3ff));
    }
  }
  return result;
}

// Scan tokens to reject duplicate object keys and integers outside int64.
void check_tokens(std::string_view wire) {
  std::vector<std::set<std::string>> keys;
  for (std::size_t i = 0; i < wire.size();) {
    const char c = wire[i];
    if (c == '{') {
      keys.emplace_back();
      ++i;
    } else if (c == '}') {
      check(!keys.empty(), "unbalanced JSON object");
      keys.pop_back();
      ++i;
    } else if (c == '"') {
      std::size_t start = i++;
      while (i < wire.size() && wire[i] != '"') {
        if (wire[i] != '\\') {
          ++i;
          continue;
        }
        check(i + 1 < wire.size(), "truncated JSON escape");
        if (wire[i + 1] != 'u') {
          i += 2;
          continue;
        }
        check(i + 6 <= wire.size(), "truncated unicode escape");
        unsigned unit = std::stoul(std::string(wire.substr(i + 2, 4)), nullptr, 16);
        i += 6;
        if (unit >= 0xd800 && unit <= 0xdbff) {
          check(i + 6 <= wire.size() && wire.substr(i, 2) == "\\u", "unpaired surrogate");
          unsigned low = std::stoul(std::string(wire.substr(i + 2, 4)), nullptr, 16);
          check(low >= 0xdc00 && low <= 0xdfff, "unpaired surrogate");
          i += 6;
        } else {
          check(unit < 0xdc00 || unit > 0xdfff, "unpaired surrogate");
        }
      }
      check(i < wire.size(), "unterminated JSON string");
      ++i;
      std::size_t next = wire.find_first_not_of(" \t\r\n", i);
      if (next != std::string_view::npos && wire[next] == ':') {
        auto key_json = Json::parse(std::string(wire.substr(start, i - start)));
        check(json_object_is_type(key_json.get(), json_type_string), "invalid JSON object key");
        std::string decoded(json_object_get_string(key_json.get()),
                            static_cast<std::size_t>(json_object_get_string_len(key_json.get())));
        check(!keys.empty(), "object key outside object");
        check(keys.back().insert(decoded).second, "duplicate JSON key: " + decoded);
      }
    } else if (c == '-' || (c >= '0' && c <= '9')) {
      std::size_t end = wire.find_first_of(" \t\r\n,]}", i);
      if (end == std::string_view::npos) end = wire.size();
      auto token = std::string(wire.substr(i, end - i));
      if (token.find_first_of(".eE") == std::string::npos) {
        int64_t parsed = 0;
        auto [last, error] = std::from_chars(token.data(), token.data() + token.size(), parsed);
        check(error == std::errc{} && last == token.data() + token.size(),
              "integer outside signed 64-bit range");
      }
      i = end;
    } else if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')) {
      std::size_t end = wire.find_first_of(" \t\r\n,]}", i);
      if (end == std::string_view::npos) end = wire.size();
      auto token = std::string(wire.substr(i, end - i));
      check(token == "true" || token == "false" || token == "null",
            "invalid JSON literal: " + token);
      i = end;
    } else {
      ++i;
    }
  }
  check(keys.empty(), "unbalanced JSON object");
}

}  // namespace

Json Json::parse(std::string_view wire) {
  check(wire.find('\0') == std::string_view::npos, "NUL in JSON payload");
  auto escaped = ascii_escape(wire);
  auto tokener = std::unique_ptr<json_tokener, decltype(&json_tokener_free)>(
      json_tokener_new_ex(64), json_tokener_free);
  check(tokener != nullptr, "cannot allocate JSON parser");
  json_tokener_set_flags(tokener.get(), JSON_TOKENER_STRICT | JSON_TOKENER_VALIDATE_UTF8);
  Json result(json_tokener_parse_ex(tokener.get(), escaped.c_str(),
                                   static_cast<int>(escaped.size() + 1)));
  check(json_tokener_get_error(tokener.get()) == json_tokener_success, "invalid JSON payload");
  auto end = json_tokener_get_parse_end(tokener.get());
  check(escaped.find_first_not_of(" \t\r\n", end) == std::string::npos, "trailing JSON data");
  check_tokens(wire);
  return result;
}

Json Json::object() { return Json(json_object_new_object()); }

Json Json::array() { return Json(json_object_new_array()); }

void Json::add(const std::string& key, Json value) {
  check(value_ != nullptr, "cannot add to null JSON");
  json_object_object_add(value_.get(), key.c_str(), value.release());
}

void Json::add(const std::string& key, json_object* value) {
  check(value_ != nullptr, "cannot add null JSON");
  json_object_object_add(value_.get(), key.c_str(), value);
}

void Json::add_string(const std::string& key, std::string_view value) {
  add(key, json_object_new_string_len(value.data(), static_cast<int>(value.size())));
}

void Json::add_int(const std::string& key, int64_t value) { add(key, json_object_new_int64(value)); }

void Json::add_bool(const std::string& key, bool value) { add(key, json_object_new_boolean(value)); }

void Json::add_null(const std::string& key) { add(key, nullptr); }

std::string Json::dump() const { return json_object_to_json_string_ext(value_.get(), JSON_C_TO_STRING_PLAIN); }

bool has(json_object* object, const char* key) {
  json_object* unused = nullptr;
  return object != nullptr && json_object_is_type(object, json_type_object) &&
         json_object_object_get_ex(object, key, &unused);
}

json_object* field(json_object* object, const char* key) {
  json_object* value = nullptr;
  check(object != nullptr && json_object_is_type(object, json_type_object) &&
            json_object_object_get_ex(object, key, &value),
        std::string("missing field: ") + key);
  return value;
}

std::string string_field(json_object* object, const char* key, bool allow_empty) {
  json_object* value = field(object, key);
  check(json_object_is_type(value, json_type_string), std::string("expected string: ") + key);
  std::string result(json_object_get_string(value), json_object_get_string_len(value));
  check(allow_empty || !result.empty(), std::string("empty string: ") + key);
  return result;
}

int64_t int_field(json_object* object, const char* key, int64_t minimum, bool nullable) {
  json_object* value = field(object, key);
  if (nullable && value == nullptr) return std::numeric_limits<int64_t>::min();
  check(json_object_is_type(value, json_type_int), std::string("expected integer: ") + key);
  auto result = json_object_get_int64(value);
  check(result >= minimum, std::string("integer below minimum: ") + key);
  return result;
}

bool bool_field(json_object* object, const char* key) {
  json_object* value = field(object, key);
  check(json_object_is_type(value, json_type_boolean), std::string("expected boolean: ") + key);
  return json_object_get_boolean(value);
}

json_object* object_field(json_object* object, const char* key) {
  json_object* value = field(object, key);
  check(json_object_is_type(value, json_type_object), std::string("expected object: ") + key);
  return value;
}

json_object* array_field(json_object* object, const char* key) {
  json_object* value = field(object, key);
  check(json_object_is_type(value, json_type_array), std::string("expected array: ") + key);
  return value;
}

}  // namespace algoos::protocol
