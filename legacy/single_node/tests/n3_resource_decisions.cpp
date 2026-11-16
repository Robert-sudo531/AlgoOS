// 明确为合成证据；只验证共用分类函数，不模拟一次真实 OOM。
#include "../judge/native/eval_core.hpp"

#include <csignal>
#include <iostream>
#include <stdexcept>

int main() {
    using namespace algoos::eval;
    constexpr Limits limits{1000, 1000, 64LL << 20, 1};
    try {
        const auto expect = [&](ProcessEvidence evidence, std::string_view verdict, std::string_view reason) {
            const auto decision = classify_process(evidence, limits);
            if (verdict.empty() ? decision.has_value()
                                : !decision || decision->verdict != verdict || decision->reason != reason)
                throw std::runtime_error("unexpected classification: " + std::string(reason));
        };
        expect({1000, 0, 0, false}, "", "continue without comparable memory evidence");
        expect({1000, 0, 0, false, limits.mem_bytes}, "", "continue at equal peak and CPU limits");
        expect({1001, 0, 0, false, limits.mem_bytes + 1}, "MLE", "MEM_PEAK_OVER");
        expect({1001, 7, 0, false, limits.mem_bytes + 1}, "RE", "NONZERO_EXIT");
        expect({1001, std::nullopt, SIGKILL, true, std::nullopt, true}, "MLE", "OOM_KILL");
        expect({1001, std::nullopt, SIGKILL, true}, "TLE", "WALL_TIMEOUT");
        bool unclassified = false;
        try {
            (void)classify_process({1, std::nullopt, SIGKILL, false}, limits);
        } catch (const std::runtime_error& error) {
            unclassified = std::string_view(error.what()).starts_with("unclassified signal");
        }
        if (!unclassified) throw std::runtime_error("SIGKILL alone must not assert OOM or CPU limit");
        std::cout << "7 synthetic resource decisions passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
