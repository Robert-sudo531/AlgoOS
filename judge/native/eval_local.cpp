// 本地验证入口：从 stdin 读 v1 任务，文件路径由调用者单独提供。
#include "eval_task_v1.hpp"

#include <iostream>

int main(int argc, char** argv) {
    try {
        algoos::protocol::require(argc == 7, "usage: eval_local POLICY NODE_ID BINARY INPUT ANSWER NEW_OUTPUT_DIR");
        auto policy = algoos::worker::load_policy(argv[1]);
        const algoos::worker::LocalFiles files{argv[3], argv[4], argv[5], argv[6]};
        std::cout << algoos::worker::evaluate_task(std::cin, policy.get(), argv[2], files) << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "eval_local: " << error.what() << '\n';
        return 1;
    }
}
