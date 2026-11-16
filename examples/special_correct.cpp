// Special-judge AC fixture: output one valid non-negative pair (0, n).
#include <iostream>

int main() {
  long long n;
  if (!(std::cin >> n)) return 0;
  std::cout << 0 << ' ' << n << '\n';
  return 0;
}
