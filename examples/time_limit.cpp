#include <chrono>
#include <iostream>
#include <thread>

int main() {
    long long a, b;
    std::cin >> a >> b;
    std::cout << a + b << std::endl;  // 刷新输出，保留超时前的实际结果。
    std::this_thread::sleep_for(std::chrono::seconds(3));
    std::cout << "finished\n";  // 1 秒墙钟限制应在执行到这里前终止程序。
    return 0;
}
