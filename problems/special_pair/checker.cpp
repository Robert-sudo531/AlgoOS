/**
 * Special checker contract for AlgoOS.
 *
 * Invocation: checker <input> <participant-output> <standard-answer>
 * Exit status: 0 = accepted, 1 = wrong answer, anything else = system error.
 *
 * The standard answer is supplied for compatibility with the judge protocol;
 * this checker validates the participant output from the input alone, so many
 * different answers are accepted.
 */
#include <chrono>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>

namespace {
constexpr long long kSleepSeconds = 8;

bool read_long_long(std::istream& stream, long long& value) {
  std::string token;
  if (!(stream >> token)) return false;
  try {
    std::size_t consumed = 0;
    value = std::stoll(token, &consumed);
    return consumed == token.size();
  } catch (const std::exception&) {
    return false;
  }
}
}  // namespace

int main(int argc, char** argv) {
  if (argc != 4) {
    std::cerr << "usage: checker <input> <output> <answer>\n";
    return 2;
  }
  std::ifstream input(argv[1]);
  std::ifstream output(argv[2]);
  if (!input || !output) {
    std::cerr << "cannot open checker input files\n";
    return 2;
  }
  long long n = 0;
  if (!(input >> n) || n < 0) {
    std::cerr << "invalid input\n";
    return 2;
  }

  std::string first;
  if (!(output >> first)) return 1;
  // Deterministic hooks used by the integration tests to exercise SE paths.
  if (first == "CRASH") return 2;
  if (first == "SLEEP") {
    std::this_thread::sleep_for(std::chrono::seconds(kSleepSeconds));
    return 0;
  }
  long long a = 0;
  long long b = 0;
  try {
    std::size_t consumed = 0;
    a = std::stoll(first, &consumed);
    if (consumed != first.size() || !(output >> b)) return 1;
  } catch (const std::exception&) {
    return 1;
  }
  std::string extra;
  if (output >> extra) return 1;
  if (a < 0 || b < 0 || a + b != n) {
    std::cerr << "expected a+b=" << n << ", got " << a << "+" << b << "\n";
    return 1;
  }
  return 0;
}
