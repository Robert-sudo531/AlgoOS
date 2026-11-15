#include <array>
#include <cerrno>
#include <csignal>
#include <iostream>
#include <sys/prctl.h>
#include <unistd.h>

int main() {
    long long a, b;
    if (!(std::cin >> a >> b)) return 2;
    if (a >= 0) {
        // A+B 测试点 01：正常退出，归一化输出超过答案长度。
        std::cout << a + b << " extra\n";
        return 0;
    }

    // 测试点 02：持续写到文件上限，实际触发 SIGXFSZ。
    if (prctl(PR_SET_DUMPABLE, 0L, 0L, 0L, 0L) != 0 ||
        std::signal(SIGXFSZ, SIG_DFL) == SIG_ERR) return 2;
    std::array<char, 1024> block;
    block.fill('x');
    for (int index = 0; index < 8; ++index) {
        ssize_t count;
        do { count = write(STDOUT_FILENO, block.data(), block.size()); }
        while (count < 0 && errno == EINTR);
        if (count <= 0) return 2;
    }
    return 0;
}
