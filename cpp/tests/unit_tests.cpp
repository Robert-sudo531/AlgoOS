#include <csignal>
#include <fcntl.h>
#include <unistd.h>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "algoos/protocol/digest.hpp"
#include "algoos/protocol/json.hpp"
#include "algoos/runner/comparator.hpp"
#include "algoos/sandbox/classify.hpp"
#include "algoos/sandbox/sandbox.hpp"

namespace {
namespace fs = std::filesystem;
using algoos::sandbox::Evidence;
using algoos::sandbox::Limits;
using algoos::sandbox::Reason;
using algoos::sandbox::Verdict;

int failures = 0;

void expect(bool condition, const std::string& label) {
  if (!condition) {
    std::cerr << "FAIL: " << label << '\n';
    ++failures;
  } else {
    std::cout << "ok: " << label << '\n';
  }
}

std::string read_file(const fs::path& path) {
  std::ifstream input(path);
  return std::string(std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>());
}

void test_digest() {
  expect(algoos::protocol::sha256_hex("abc") ==
             "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
         "sha256 abc");
  expect(algoos::protocol::sha256_hex("") ==
             "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
         "sha256 empty");
  expect(algoos::protocol::valid_hash("sha256:" + algoos::protocol::sha256_hex("abc")),
         "hash wire format");
}

void test_json() {
  auto parsed = algoos::protocol::Json::parse(R"({"ver":1,"name":"中文","nested":{"a":true}})");
  expect(algoos::protocol::int_field(parsed.get(), "ver") == 1, "json int");
  expect(algoos::protocol::string_field(parsed.get(), "name") == "中文", "json utf-8");
  bool rejected = false;
  try {
    (void)algoos::protocol::Json::parse(R"({"a":1,"a":2})");
  } catch (const std::exception&) {
    rejected = true;
  }
  expect(rejected, "duplicate keys rejected");
  rejected = false;
  try {
    (void)algoos::protocol::Json::parse(R"({"a":9223372036854775808})");
  } catch (const std::exception&) {
    rejected = true;
  }
  expect(rejected, "overflowing integer rejected");
  rejected = false;
  try {
    (void)algoos::protocol::Json::parse(R"({"a":NaN})");
  } catch (const std::exception&) {
    rejected = true;
  }
  expect(rejected, "NaN rejected");
}

void test_compare() {
  using algoos::runner::normalize_common;
  expect(normalize_common("a\r\nb  \r\n\n") == "a\nb", "normalize crlf + trailing blanks");
  expect(normalize_common(" x \t\n") == " x", "preserve leading space");
  bool truncated = false;
  std::string error;
  const auto temporary = fs::temp_directory_path() / "algoos-unit-compare";
  fs::create_directories(temporary);
  {
    std::ofstream output(temporary / "out");
    output << "hello\n";
  }
  const auto bytes = algoos::runner::read_bounded((temporary / "out").string(), 8, truncated, error);
  expect(bytes.has_value() && *bytes == std::string("hello\n") && !truncated, "read bounded exact");
  truncated = false;
  const auto capped = algoos::runner::read_bounded((temporary / "out").string(), 3, truncated, error);
  expect(capped.has_value() && capped->size() == 3 && truncated, "read bounded truncated");
  fs::remove_all(temporary);
}

Evidence base_evidence() {
  Evidence evidence;
  evidence.started = true;
  evidence.wifexited = true;
  evidence.exit_code = 0;
  evidence.cpu_ms = 100;
  evidence.wall_ms = 120;
  evidence.ru_maxrss_bytes = 1024;
  evidence.cgroup_memory_peak = 2048;
  evidence.out_size = 10;
  return evidence;
}

void test_classify() {
  Limits limits{1000, 3000, 256LL << 20, 10};
  Evidence evidence = base_evidence();
  expect(algoos::sandbox::classify(evidence, limits) == std::nullopt, "healthy needs compare");

  evidence.oom_kill = 1;
  auto decision = algoos::sandbox::classify(evidence, limits);
  expect(decision && decision->verdict == Verdict::MLE && decision->reason == Reason::OOM_KILL,
         "OOM takes priority");

  evidence = base_evidence();
  evidence.wifsignaled = true;
  evidence.wifexited = false;
  evidence.term_sig = SIGKILL;
  evidence.wall_dog_fired = true;
  decision = algoos::sandbox::classify(evidence, limits);
  expect(decision && decision->verdict == Verdict::TLE &&
             decision->reason == Reason::WALL_TIMEOUT, "wall timeout");

  evidence = base_evidence();
  evidence.wifsignaled = true;
  evidence.wifexited = false;
  evidence.term_sig = SIGSEGV;
  decision = algoos::sandbox::classify(evidence, limits);
  expect(decision && decision->verdict == Verdict::RE && decision->reason == Reason::CRASH_SIGNAL,
         "crash signal");

  evidence = base_evidence();
  evidence.exit_code = 3;
  evidence.wifexited = true;
  decision = algoos::sandbox::classify(evidence, limits);
  expect(decision && decision->verdict == Verdict::RE &&
             decision->reason == Reason::NONZERO_EXIT, "non-zero exit");

  evidence = base_evidence();
  evidence.cgroup_memory_peak = limits.mem_bytes + 1;
  decision = algoos::sandbox::classify(evidence, limits);
  expect(decision && decision->verdict == Verdict::MLE &&
             decision->reason == Reason::MEM_PEAK_OVER, "memory peak");

  evidence = base_evidence();
  evidence.cpu_ms = limits.cpu_ms + 1;
  decision = algoos::sandbox::classify(evidence, limits);
  expect(decision && decision->verdict == Verdict::TLE &&
             decision->reason == Reason::CPU_TIMEOUT, "cpu time over");

  evidence = base_evidence();
  evidence.out_size = limits.out_bytes + 1;
  decision = algoos::sandbox::classify(evidence, limits);
  expect(decision && decision->verdict == Verdict::OLE &&
             decision->reason == Reason::OLE_TRUNCATED, "output size over");

  expect(algoos::sandbox::name(Verdict::OLE) == "OLE", "verdict name");
}

void test_sandbox() {
  if (geteuid() != 0) {
    std::cout << "skip: sandbox test requires root\n";
    return;
  }
  const auto root = fs::temp_directory_path() / ("algoos-unit-sandbox-" + std::to_string(getpid()));
  const auto work = root / "work";
  fs::create_directories(work);
  {
    std::ofstream input(work / "input");
    input << "1 2\n";
  }
  algoos::sandbox::RunRequest request;
  request.executable = "/bin/cat";
  request.input = (work / "input").string();
  request.work_dir = (work / "run").string();
  request.limits = Limits{1000, 3000, 256LL << 20, 64};
  request.config.use_cgroup = true;
  request.config.use_seccomp = true;
  request.config.use_namespaces = true;
  const auto result = algoos::sandbox::run(request);
  if (result.evidence.setup_failed) {
    std::cerr << "sandbox setup error: " << result.setup_error << "\n";
    for (const auto& warning : result.warnings) std::cerr << "warning: " << warning << "\n";
  }
  expect(!result.evidence.setup_failed, "sandbox setup succeeds");
  expect(result.evidence.wifexited && result.evidence.exit_code == 0, "sandbox child exits 0");
  expect(result.evidence.out_size == 4, "sandbox output captured");
  expect(result.evidence.cgroup_memory_peak.has_value(), "cgroup memory peak recorded");
  expect(result.sandbox_mode == "full", "sandbox reports full isolation");

  algoos::sandbox::RunRequest failing;
  failing.executable = "/bin/sh";
  failing.arguments = {"-c", "kill -SEGV $$"};
  failing.input = (work / "input").string();
  failing.work_dir = (work / "run2").string();
  failing.limits = Limits{1000, 3000, 256LL << 20, 64};
  const auto crashed = algoos::sandbox::run(failing);
  if (crashed.evidence.setup_failed) std::cerr << "crash case setup: " << crashed.setup_error << "\n";
  expect(crashed.evidence.wifsignaled && crashed.evidence.term_sig == SIGSEGV,
         "crash signal observed");

  algoos::sandbox::RunRequest slow;
  slow.executable = "/bin/sleep";
  slow.arguments = {"5"};
  slow.input = (work / "input").string();
  slow.work_dir = (work / "run3").string();
  slow.limits = Limits{100, 150, 256LL << 20, 64};
  const auto timed = algoos::sandbox::run(slow);
  if (timed.evidence.setup_failed) std::cerr << "timeout case setup: " << timed.setup_error << "\n";
  expect(timed.evidence.wall_dog_fired, "wall watchdog fires");
  expect(algoos::sandbox::classify(timed.evidence, slow.limits) &&
             algoos::sandbox::classify(timed.evidence, slow.limits)->verdict == Verdict::TLE,
         "watchdog classifies TLE");
  fs::remove_all(root);
}

}  // namespace

int main() {
  try {
    test_digest();
    test_json();
    test_compare();
    test_classify();
    test_sandbox();
    if (failures == 0) std::cout << "all unit tests passed\n";
    return failures == 0 ? 0 : 1;
  } catch (const std::exception& error) {
    std::cerr << "unit test fatal: " << error.what() << '\n';
    return 1;
  }
}
