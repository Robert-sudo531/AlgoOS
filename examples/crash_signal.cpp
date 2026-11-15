#include <cstdlib>
#include <iostream>
#include <sys/prctl.h>

int main() {
    // 只关闭此样例的转储，避免宿主的崩溃收集延迟 wait4 回收。
    if (prctl(PR_SET_DUMPABLE, 0L, 0L, 0L, 0L) != 0) {
        std::cerr << "cannot disable core dump for this test\n";
        return 2;
    }
    long long a, b;
    std::cin >> a >> b;
    std::cout << a + b << std::endl;
    std::abort();  // 主动触发 SIGABRT，验证崩溃信号判定。
}
