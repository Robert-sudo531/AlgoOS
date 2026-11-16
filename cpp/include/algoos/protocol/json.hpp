#pragma once

/// Strict JSON helpers shared by the AlgoOS native workers.
/// Messages are parsed from untrusted queue payloads, so parsing rejects
/// duplicate keys, invalid UTF-8, trailing data and non-64-bit integers.

#include <json-c/json.h>

#include <cstdint>
#include <functional>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace algoos::protocol {

class Json {
 public:
  Json() : value_(nullptr, json_object_put) {}
  explicit Json(json_object* value) : value_(value, json_object_put) {}

  static Json parse(std::string_view wire);
  static Json object();
  static Json array();

  [[nodiscard]] json_object* get() const { return value_.get(); }
  [[nodiscard]] json_object* release() { return value_.release(); }
  [[nodiscard]] explicit operator bool() const { return value_ != nullptr; }

  void add(const std::string& key, Json value);
  void add(const std::string& key, json_object* value);
  void add_string(const std::string& key, std::string_view value);
  void add_int(const std::string& key, int64_t value);
  void add_bool(const std::string& key, bool value);
  void add_null(const std::string& key);
  [[nodiscard]] std::string dump() const;

 private:
  std::unique_ptr<json_object, decltype(&json_object_put)> value_;
};

using ObjectPtr = json_object*;

[[nodiscard]] bool has(json_object* object, const char* key);
[[nodiscard]] json_object* field(json_object* object, const char* key);
[[nodiscard]] std::string string_field(json_object* object, const char* key, bool allow_empty = false);
[[nodiscard]] int64_t int_field(json_object* object, const char* key, int64_t minimum = 0,
                                bool nullable = false);
[[nodiscard]] bool bool_field(json_object* object, const char* key);
[[nodiscard]] json_object* object_field(json_object* object, const char* key);
[[nodiscard]] json_object* array_field(json_object* object, const char* key);

}  // namespace algoos::protocol
