#pragma once

#include "../../protocol/codec_v1.hpp"

#include <filesystem>
#include <istream>
#include <string>

namespace algoos::worker {

struct LocalFiles {
    std::filesystem::path executable;
    std::filesystem::path input;
    std::filesystem::path answer;
    std::filesystem::path output_dir;
};

protocol::Json read_json_file(const std::filesystem::path& path);
protocol::Json load_policy(const std::filesystem::path& path);
void check_node_id(const std::string& node_id, json_object* policy);

// 本地调用者负责准备并校验对象文件；消息中的身份字段不用于拼接文件路径。
std::string evaluate_task(std::istream& input, json_object* policy, const std::string& node_id,
                          const LocalFiles& files);

}  // namespace algoos::worker
