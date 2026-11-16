// User-cancel fixture: sleep long enough for the API cancel to arrive.
#include <chrono>
#include <iostream>
#include <thread>

int main() {
  long long a;
  long long b;
  if (!(std::cin >> a >> b)) return 0;
  std::this_thread::sleep_for(std::chrono::seconds(10));
  std::cout << a + b << '\n';
  return 0;
}
