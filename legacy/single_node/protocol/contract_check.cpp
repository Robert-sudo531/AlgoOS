// 一次读取 stdin 中的一条 JSON，验证后原样输出字段（补默认 status）。
#include "codec_v1.hpp"
#include <fstream>
#include <iostream>
#include <iterator>

int main(int argc, char** argv) {
    try {
        algoos::protocol::require(argc == 2, "usage: contract_check policy_v1.json");
        std::ifstream input(argv[1], std::ios::binary);
        algoos::protocol::require(input.good(), "cannot read policy file");
        std::string policy_text{std::istreambuf_iterator<char>(input), {}};
        std::string wire{std::istreambuf_iterator<char>(std::cin), {}};
        auto policy = algoos::protocol::parse(policy_text);
        auto message = algoos::protocol::parse(wire);
        algoos::protocol::normalize(message.get(), policy.get());
        std::cout << json_object_to_json_string_ext(message.get(), JSON_C_TO_STRING_PLAIN) << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
