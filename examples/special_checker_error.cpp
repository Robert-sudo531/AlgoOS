// Special-judge SE fixture: the checker exits with status 2.
#include <iostream>

int main() {
  long long n;
  if (!(std::cin >> n)) return 0;
  std::cout << "CRASH\n";
  return 0;
}
