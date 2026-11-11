#include <iostream>

int main() {
    long long a, b;
    std::cin >> a >> b;
    // 故意反向相减；输出长度不超限，用于验证答案内容错误。
    std::cout << b - a << '\n';
    return 0;
}
