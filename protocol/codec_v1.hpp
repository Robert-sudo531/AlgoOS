#pragma once

// v1 消息契约。这里只检查消息，不启动 Worker、不改变 N1 执行代码。
#include <json-c/json.h>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <memory>
#include <regex>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace algoos::protocol {
using Json = std::unique_ptr<json_object, decltype(&json_object_put)>;

inline void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

inline json_object* field(json_object* object, const char* key) {
    json_object* value = nullptr;
    require(json_object_is_type(object, json_type_object) &&
            json_object_object_get_ex(object, key, &value), std::string("missing field: ") + key);
    return value; // JSON null 在 json-c 中是 nullptr，仍与缺字段区分。
}

inline std::string string(json_object* object, const char* key, bool empty = false) {
    auto* value = field(object, key);
    require(json_object_is_type(value, json_type_string), std::string("expected string: ") + key);
    std::string result(json_object_get_string(value), json_object_get_string_len(value));
    require(empty || !result.empty(), std::string("empty string: ") + key);
    return result;
}

inline int64_t integer(json_object* object, const char* key, int64_t minimum = 0,
                       bool nullable = false) {
    auto* value = field(object, key);
    if (nullable && value == nullptr) return 0;
    require(json_object_is_type(value, json_type_int), std::string("expected integer: ") + key);
    auto result = json_object_get_int64(value);
    require(result >= minimum && json_object_get_uint64(value) <= uint64_t(INT64_MAX),
            std::string("integer out of range: ") + key);
    return result;
}

inline bool contains(json_object* array, const std::string& value) {
    for (size_t i = 0; i < json_object_array_length(array); ++i) {
        auto* entry = json_object_array_get_idx(array, i);
        if (json_object_is_type(entry, json_type_string) && value == json_object_get_string(entry))
            return true;
    }
    return false;
}

inline std::string one_of(json_object* object, const char* key, json_object* choices) {
    auto value = string(object, key);
    json_object* unused = nullptr;
    bool accepted = json_object_is_type(choices, json_type_array)
        ? contains(choices, value) : (value.find('\0') == std::string::npos &&
                                     json_object_object_get_ex(choices, value.c_str(), &unused));
    require(accepted, std::string("unsupported value: ") + key);
    return value;
}

inline void hash_field(json_object* object, const char* key) {
    static const std::regex pattern("sha256:[0-9a-f]{64}");
    require(std::regex_match(string(object, key), pattern), std::string("invalid hash: ") + key);
}

inline void check_tree(json_object* value, int depth = 0) {
    require(depth < 64, "JSON nesting too deep");
    if (json_object_is_type(value, json_type_object)) {
        json_object_object_foreach(value, key, item) {
            (void) key;
            check_tree(item, depth + 1);
        }
    } else if (json_object_is_type(value, json_type_array)) {
        for (size_t i = 0; i < json_object_array_length(value); ++i)
            check_tree(json_object_array_get_idx(value, i), depth + 1);
    } else if (json_object_is_type(value, json_type_double)) {
        require(std::isfinite(json_object_get_double(value)), "non-finite JSON number");
    }
}

// json-c 对重复键及超大整数较宽容；额外检查，避免与 Python 的解释不同。
// 先由严格 JSON 解析器检查语法，此处只扫描数值和字符串 token。
inline void check_tokens(const std::string& wire) {
    std::vector<std::set<std::string>> object_keys;
    for (size_t i = 0; i < wire.size();) {
        char c = wire[i];
        if (c == '{') { object_keys.emplace_back(); ++i; }
        else if (c == '}') { object_keys.pop_back(); ++i; }
        else if (c == '"') {
            size_t start = i++;
            while (i < wire.size() && wire[i] != '"') {
                if (wire[i] != '\\') { ++i; continue; }
                if (wire[i + 1] != 'u') { i += 2; continue; }
                unsigned unit = std::stoul(wire.substr(i + 2, 4), nullptr, 16);
                i += 6;
                if (unit >= 0xd800 && unit <= 0xdbff) {
                    require(wire.substr(i, 2) == "\\u", "unpaired Unicode surrogate");
                    unsigned low = std::stoul(wire.substr(i + 2, 4), nullptr, 16);
                    require(low >= 0xdc00 && low <= 0xdfff, "unpaired Unicode surrogate");
                    i += 6;
                } else require(unit < 0xdc00 || unit > 0xdfff, "unpaired Unicode surrogate");
            }
            ++i;
            size_t next = wire.find_first_not_of(" \t\r\n", i);
            if (next != std::string::npos && wire[next] == ':') {
                Json key(json_tokener_parse(wire.substr(start, i - start).c_str()), json_object_put);
                std::string decoded(json_object_get_string(key.get()), json_object_get_string_len(key.get()));
                require(decoded.find('\0') == std::string::npos, "NUL in object key");
                require(!object_keys.empty() && object_keys.back().insert(decoded).second,
                        "duplicate object key");
            }
        } else if (c == '-' || (c >= '0' && c <= '9')) {
            size_t end = wire.find_first_of(" \t\r\n,]}", i);
            if (end == std::string::npos) end = wire.size();
            auto token = wire.substr(i, end - i);
            if (token.find_first_of(".eE") == std::string::npos) {
                int64_t value;
                auto [last, error] = std::from_chars(token.data(), token.data() + token.size(), value);
                require(error == std::errc{} && last == token.data() + token.size(),
                        "integer outside signed 64-bit range");
            }
            i = end;
        } else ++i;
    }
}

inline std::string ascii_json(const std::string& wire) {
    std::string result;
    bool in_string = false, escaped = false;
    auto append_unit = [&](uint32_t unit) {
        constexpr char hex[] = "0123456789abcdef";
        result += "\\u";
        for (int shift : {12, 8, 4, 0}) result += hex[(unit >> shift) & 15];
    };
    for (size_t i = 0; i < wire.size();) {
        auto lead = static_cast<unsigned char>(wire[i++]);
        if (lead < 0x80) {
            result += static_cast<char>(lead);
            if (lead == '"' && !escaped) in_string = !in_string;
            escaped = in_string && lead == '\\' && !escaped;
            continue;
        }
        require(in_string && !escaped, "non-ASCII outside JSON string/after escape");
        int remaining;
        uint32_t code, minimum;
        if (lead >= 0xc2 && lead <= 0xdf) { remaining = 1; code = lead & 0x1f; minimum = 0x80; }
        else if (lead >= 0xe0 && lead <= 0xef) { remaining = 2; code = lead & 0x0f; minimum = 0x800; }
        else if (lead >= 0xf0 && lead <= 0xf4) { remaining = 3; code = lead & 0x07; minimum = 0x10000; }
        else throw std::runtime_error("invalid UTF-8 leading byte");
        require(i + remaining <= wire.size(), "incomplete UTF-8 sequence");
        while (remaining--) {
            auto byte = static_cast<unsigned char>(wire[i++]);
            require(byte >= 0x80 && byte <= 0xbf, "invalid UTF-8 continuation byte");
            code = (code << 6) | (byte & 0x3f);
        }
        require(code >= minimum && code <= 0x10ffff && (code < 0xd800 || code > 0xdfff),
                "invalid UTF-8 code point");
        if (code <= 0xffff) append_unit(code);
        else {
            code -= 0x10000;
            append_unit(0xd800 + (code >> 10));
            append_unit(0xdc00 + (code & 0x3ff));
        }
    }
    return result;
}

inline Json parse(const std::string& wire) {
    require(wire.find('\0') == std::string::npos && wire.size() < size_t(INT32_MAX),
            "invalid JSON payload");
    // 本机 json-c 0.18 的 STRICT 模式拒绝未转义中文；先验证 UTF-8，再等值转义。
    // 仍使用严格 JSON 语法检查，不通过放宽语法来接受中文。
    auto parser_input = ascii_json(wire);
    require(parser_input.size() < size_t(INT32_MAX), "JSON payload too large");
    auto tokener = std::unique_ptr<json_tokener, decltype(&json_tokener_free)>(
        json_tokener_new_ex(64), json_tokener_free);
    require(tokener != nullptr, "cannot allocate JSON parser");
    json_tokener_set_flags(tokener.get(), JSON_TOKENER_STRICT);
    // 末尾 NUL 只作为解析器结束标记，不属于消息载荷。
    Json result(json_tokener_parse_ex(tokener.get(), parser_input.c_str(), int(parser_input.size() + 1)), json_object_put);
    auto error = json_tokener_get_error(tokener.get());
    require(error == json_tokener_success,
            std::string("invalid JSON syntax/UTF-8: ") + json_tokener_error_desc(error));
    auto end = json_tokener_get_parse_end(tokener.get());
    require(end >= parser_input.size() || parser_input.find_first_not_of(" \t\r\n", end) == std::string::npos,
            "trailing JSON payload");
    check_tokens(wire);
    check_tree(result.get());
    return result;
}

inline void normalize(json_object* message, json_object* policy) {
    require(integer(message, "ver", 1) == integer(policy, "ver", 1), "unsupported version");
    auto kind = string(message, "type");
    require(kind == "compile_task" || kind == "eval_task" || kind == "eval_result", "unsupported type");
    for (auto key : {"msg_id", "problem_id", "file_id", "correlation_id", "emitted_at"}) string(message, key);
    auto file_id = string(message, "file_id");
    require(file_id.find(':') == std::string::npos, "colon in file_id");
    require(string(message, "correlation_id") == file_id, "correlation_id mismatch");
    auto timestamp = string(message, "emitted_at");
    static const std::regex date_pattern(
        "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?Z");
    require(std::regex_match(timestamp, date_pattern), "expected UTC timestamp");
    int year = std::stoi(timestamp.substr(0, 4));
    auto date = std::chrono::year_month_day(std::chrono::year(year),
        std::chrono::month(std::stoi(timestamp.substr(5, 2))),
        std::chrono::day(std::stoi(timestamp.substr(8, 2))));
    require(year >= 1 && date.ok() && std::stoi(timestamp.substr(11, 2)) < 24 &&
            std::stoi(timestamp.substr(14, 2)) < 60 && std::stoi(timestamp.substr(17, 2)) < 60,
            "invalid timestamp");
    auto attempt = integer(message, "attempt", 1);
    auto node = [&](const char* key) {
        auto pattern = string(field(policy, "transport"), "node_id_pattern");
        require(std::regex_match(string(message, key), std::regex(pattern)), "invalid node id");
    };
    auto identity = [&] {
        auto test_id = string(message, "test_id");
        require(test_id.find(':') == std::string::npos, "colon in test_id");
        integer(message, "sequence", 1);
        require(string(message, "judge_id") == file_id + ":" + test_id + ":" + std::to_string(attempt),
                "judge_id mismatch");
    };
    auto profile = [&] {
        auto* profiles = field(policy, "compiler_profiles");
        auto name = one_of(message, "compiler_profile", profiles);
        return field(profiles, name.c_str());
    };
    if (kind == "compile_task" || kind == "eval_task") {
        integer(message, "enqueue_seq", 1);
        hash_field(message, "test_bundle_hash");
        auto* limits = field(message, "limits");
        for (auto key : {"cpu_ms", "wall_ms", "mem_bytes", "out_bytes"})
            integer(limits, key, std::string(key) == "out_bytes" ? 0 : 1);
        if (kind == "compile_task") {
            hash_field(message, "source_hash");
            auto* frozen = field(profile(), "limits");
            json_object_object_foreach(frozen, key, value) {
                require(integer(limits, key) == json_object_get_int64(value), "compiler limits mismatch");
            }
        } else {
            identity(); node("target_node_id"); hash_field(message, "bin_hash");
            json_object_object_foreach(field(policy, "modes"), key, choices) { one_of(message, key, choices); }
            require(integer(limits, "cpu_ms") == integer(limits, "wall_ms"), "wall/CPU limits mismatch");
        }
        return;
    }
    auto* rules = field(policy, "result");
    auto* sources = field(rules, "sources_by_stage");
    auto stage = one_of(message, "stage", sources);
    node("node_id");
    require(string(message, "source") == string(sources, stage.c_str()), "source/stage mismatch");
    json_object* unused = nullptr;
    if (!json_object_object_get_ex(message, "status", &unused))
        json_object_object_add(message, "status", json_object_new_string(string(rules, "default_status").c_str()));
    auto status = one_of(message, "status", field(rules, "statuses"));
    for (auto key : {"cpu_ms", "wall_ms", "mem_bytes", "out_bytes", "exit_code", "signal"})
        integer(message, key, 0, true);
    auto* memory_source = field(message, "memory_source");
    require(memory_source == nullptr || contains(field(rules, "memory_sources"), string(message, "memory_source")),
            "unsupported memory source");
    require((field(message, "mem_bytes") == nullptr) == (memory_source == nullptr), "memory evidence mismatch");
    for (auto key : {"wall_timeout", "short_circuited"})
        require(json_object_is_type(field(message, key), json_type_boolean), "expected boolean");
    require(!json_object_get_boolean(field(message, "short_circuited")), "short circuit unsupported");
    for (auto key : {"stderr_tail", "detail"}) string(message, key, true);
    if (field(message, "signal") && integer(message, "signal") != 0)
        require(field(message, "exit_code") == nullptr, "signal requires null exit_code");
    if (stage == "compile") {
        for (auto key : {"test_id", "sequence", "judge_id", "exit_code", "signal"})
            require(field(message, key) == nullptr, "compile identity/exit must be null");
        require(!json_object_get_boolean(field(message, "wall_timeout")), "compile did not run user program");
    } else { identity(); require(status == "FINAL", "eval result must be FINAL"); }
    auto* verdict = field(message, "verdict");
    auto reason = string(message, "reason");
    if (status == "COMPILED") {
        require(stage == "compile" && verdict == nullptr && reason == "NONE", "compiled status mismatch");
        auto* required = field(rules, "compile_success_required_fields");
        for (size_t i = 0; i < json_object_array_length(required); ++i)
            field(message, json_object_get_string(json_object_array_get_idx(required, i)));
        hash_field(message, "bin_hash"); hash_field(message, "test_bundle_hash");
        if (json_object_object_get_ex(message, "source_hash", &unused)) hash_field(message, "source_hash");
        profile(); integer(message, "enqueue_seq", 1);
    } else {
        auto text = one_of(message, "verdict", field(field(rules, "final_verdicts_by_stage"), stage.c_str()));
        require(contains(field(field(rules, "reason_by_verdict"), text.c_str()), reason), "verdict/reason mismatch");
    }
    if (stage == "eval") {
        bool timed_out = json_object_get_boolean(field(message, "wall_timeout"));
        require(reason != "WALL_TIMEOUT" || timed_out, "missing wall timeout evidence");
        require(!timed_out || reason == "WALL_TIMEOUT" || reason == "OOM_KILL" || reason == "SYSTEM_ERROR",
                "wall timeout evidence mismatch");
    }
}
} // namespace algoos::protocol
