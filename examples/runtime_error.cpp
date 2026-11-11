#include <iostream>

int main() {
    long long a, b;
    std::cin >> a >> b;
    std::cout << a + b << '\n';
    std::cerr << "intentional nonzero exit\n";
    return 7;  // 故意非零退出，即使输出正确，也应判为 RE。
}
