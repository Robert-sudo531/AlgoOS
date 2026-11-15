#include <cerrno>
#include <iostream>
#include <string>
#include <sys/mman.h>
#include <sys/resource.h>
#include <unistd.h>

int main() {
    long long a, b;
    if (!(std::cin >> a >> b)) return 2;
    constexpr std::size_t mib = 1ULL << 20;
    rlimit limit{};
    if (getrlimit(RLIMIT_AS, &limit) != 0 || limit.rlim_cur != 64 * mib || limit.rlim_max != limit.rlim_cur)
        return 2;
    const std::size_t request = (a >= 0 ? 128 : 16) * mib;
    void* mapping = mmap(nullptr, request, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (a >= 0) {
        // 128 MiB 映射被 64 MiB 地址空间上限拒绝，不分配真实大内存。
        if (mapping != MAP_FAILED) {
            munmap(mapping, request);
            return 2;
        }
        if (errno != ENOMEM) return 2;
        std::cerr << "RLIMIT_AS=67108864 mmap(134217728)=ENOMEM\n";
        std::cout << a + b << '\n';
        return 7;
    }
    if (mapping == MAP_FAILED) return 2;
    const auto page_size = sysconf(_SC_PAGESIZE);
    if (page_size <= 0) return 2;
    auto* bytes = static_cast<volatile unsigned char*>(mapping);
    for (std::size_t index = 0; index < request; index += static_cast<std::size_t>(page_size)) bytes[index] = 1;
    munmap(mapping, request);
    // 4098 字节原始诊断刚好触到此用例文件上限；末尾 4096 字节切在 UTF-8 中间。
    const auto diagnostic = "\xe4\xb8\xad" + std::string(4095, 'x');
    std::cerr.write(diagnostic.data(), diagnostic.size());
    std::cout << a + b << '\n';
}
