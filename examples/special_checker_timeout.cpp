// Special-judge SE fixture: the checker exceeds its wall-clock budget.
#include <iostream>

int main() {
  long long n;
  if (!(std::cin >> n)) return 0;
  std::cout << "SLEEP\n";
  return 0;
}
