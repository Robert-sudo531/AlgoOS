// 本地一次性 Worker 入口，队列消费和对象获取由后续模块接入。
#include "eval_task_v1.hpp"

#include <iostream>
#include <regex>
#include <string_view>
#include <unistd.h>

namespace {
namespace fs = std::filesystem;
namespace proto = algoos::protocol;
namespace worker = algoos::worker;
constexpr auto kVersion = "0.1.0-local";

struct Config {
    std::string node_id;
    fs::path work_dir;
    proto::Json policy;
};

void usage(std::ostream& output) {
    output << "usage: eval-worker --version | check CONFIG | run CONFIG BINARY INPUT ANSWER RUN_NAME\n";
}

Config load_config(const fs::path& path) {
    auto config = worker::read_json_file(path);
    proto::require(proto::integer(config.get(), "ver", 1) == 1, "unsupported worker config version");
    const auto parent = fs::absolute(path).parent_path();
    const auto resolve = [&](const char* key) {
        const auto value = proto::string(config.get(), key);
        proto::require(value.find('\0') == std::string::npos, std::string("invalid config path: ") + key);
        return (parent / value).lexically_normal();
    };
    auto policy = worker::load_policy(resolve("policy_file"));
    const auto node = proto::string(config.get(), "node_id");
    worker::check_node_id(node, policy.get());
    const auto work_dir = resolve("work_dir");
    proto::require(fs::is_directory(work_dir) && access(work_dir.c_str(), W_OK | X_OK) == 0,
                   "work_dir is not a writable directory: " + work_dir.string());
    return {node, work_dir, std::move(policy)};
}

void print_status(const Config& config) {
    proto::Json result(json_object_new_object(), json_object_put);
    proto::require(result != nullptr, "cannot allocate status");
    json_object_object_add(result.get(), "status", json_object_new_string("ready"));
    json_object_object_add(result.get(), "worker_version", json_object_new_string(kVersion));
    json_object_object_add(result.get(), "protocol_version", json_object_new_int(1));
    json_object_object_add(result.get(), "node_id", json_object_new_string(config.node_id.c_str()));
    std::cout << json_object_to_json_string_ext(result.get(), JSON_C_TO_STRING_PLAIN) << '\n';
}
}  // namespace

int main(int argc, char** argv) {
    try {
        const auto action = argc > 1 ? std::string_view(argv[1]) : std::string_view{};
        if (argc == 2 && action == "--help") {
            usage(std::cout);
            return 0;
        }
        if (argc == 2 && action == "--version") {
            std::cout << "algoos-eval-worker " << kVersion << '\n';
            return 0;
        }
        const bool checking = argc == 3 && action == "check";
        if (!checking && !(argc == 7 && action == "run")) {
            usage(std::cerr);
            return 1;
        }
        const auto config = load_config(argv[2]);
        if (checking) {
            print_status(config);
            return 0;
        }
        static const std::regex run_name_pattern("[A-Za-z0-9_-]{1,80}");
        proto::require(std::regex_match(argv[6], run_name_pattern), "invalid run name");
        const worker::LocalFiles files{argv[3], argv[4], argv[5], config.work_dir / argv[6]};
        std::cout << worker::evaluate_task(std::cin, config.policy.get(), config.node_id, files) << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "eval-worker: " << error.what() << '\n';
        return 1;
    }
}
