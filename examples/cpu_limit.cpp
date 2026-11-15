#include <array>
#include <cstdint>
#include <csignal>
#include <cstdlib>
#include <ctime>
#include <iostream>
#include <sys/prctl.h>
#include <thread>

std::int64_t process_cpu_ms() {
    timespec time{};
    if (clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &time) != 0) std::exit(2);
    return static_cast<std::int64_t>(time.tv_sec) * 1000 + time.tv_nsec / 1'000'000;
}

int main() {
    if (prctl(PR_SET_DUMPABLE, 0L, 0L, 0L, 0L) != 0) return 2;
#ifdef ALGOOS_TEST_CPU_HARD_LIMIT
    if (std::signal(SIGXCPU, SIG_IGN) == SIG_ERR) return 2;
#else
    if (std::signal(SIGXCPU, SIG_DFL) == SIG_ERR) return 2;
#endif
    long long a, b;
    if (!(std::cin >> a >> b)) return 2;
    // 正数用例触及内核秒级上限；负数用例在软上限前正常退出。
    const auto target = process_cpu_ms() + (a >= 0 ? 3200 : 600);
    const auto burn = [target] { while (process_cpu_ms() < target) {} };
    std::array<std::thread, 5> workers;
    for (auto& worker : workers) worker = std::thread(burn);
    burn();
    for (auto& worker : workers) worker.join();
    std::cout << a + b << '\n';
}
