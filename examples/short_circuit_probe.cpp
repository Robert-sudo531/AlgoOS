// Ordered-short-circuit fixture.
//
// Case 2 fails immediately.  Every other case sleeps long enough that a
// correct implementation must finish case 1 (lower sequence) after the
// short-circuit boundary is published, while cases after the boundary are
// killed or never dispatched.
#include <chrono>
#include <iostream>
#include <thread>

int main() {
  long long case_id;
  if (!(std::cin >> case_id)) return 0;
  if (case_id == 2) {
    std::cout << 0 << '\n';
    return 0;
  }
  std::this_thread::sleep_for(std::chrono::milliseconds(2500));
  std::cout << 42 << '\n';
  return 0;
}
