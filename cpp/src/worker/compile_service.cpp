#include "algoos/worker/compile_service.hpp"

#include <sys/stat.h>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <random>
#include <stdexcept>
#include <utility>

#include "algoos/protocol/digest.hpp"
#include "algoos/sandbox/sandbox.hpp"

namespace algoos::worker {
namespace {
namespace fs = std::filesystem;
using algoos::sandbox::Limits;

std::string unique_name() {
  static std::random_device device;
  static std::mt19937_64 generator(device());
  return std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + "-" +
         std::to_string(generator());
}

std::string tail(std::string value, std::size_t maximum) {
  if (value.size() <= maximum) return value;
  return value.substr(value.size() - maximum);
}
}  // namespace

CompileService::CompileService(Config config) : config_(std::move(config)) {
  // Query the frozen compiler version once; it participates in the build key.
  try {
    const auto directory = fs::temp_directory_path() / ("algoos-version-" + unique_name());
    fs::create_directories(directory);
    const auto stdout_path = directory / "stdout";
    const auto stderr_path = directory / "stderr";
    const auto result = sandbox::run_bounded_process(
        config_.compiler, {"--version"}, directory.string(), "", stdout_path.string(),
        stderr_path.string(), Limits{1000, 3000, 512LL << 20, 1LL << 20}, false);
    std::string text;
    if (result.evidence.wifexited && result.evidence.exit_code == 0) {
      std::ifstream input(stdout_path);
      std::getline(input, text);
    }
    compiler_version_ = text.empty() ? "unknown" : text;
    fs::remove_all(directory);
  } catch (const std::exception&) {
    compiler_version_ = "unknown";
  }
}

CompileOutcome CompileService::compile(const protocol::CompileSpec& spec) {
  CompileOutcome outcome;
  outcome.compiler_profile = spec.compiler_profile;
  outcome.compiler_version = compiler_version_;
  const auto directory = fs::path(config_.work_root) / ("compile-" + unique_name());
  fs::create_directories(directory);
  const auto stdout_path = directory / "stdout";
  const auto stderr_path = directory / "stderr";
  const auto binary_path = fs::path(spec.output_dir) / "program";
  fs::create_directories(spec.output_dir);

  std::vector<std::string> arguments = spec.flags;
  arguments.emplace_back("-x");
  arguments.emplace_back("c++");
  arguments.emplace_back(spec.source);
  arguments.emplace_back("-o");
  arguments.emplace_back(binary_path.string());

  const auto result = sandbox::run_bounded_process(
      spec.compiler.empty() ? config_.compiler : spec.compiler, arguments, directory.string(), "",
      stdout_path.string(), stderr_path.string(), spec.limits, false);

  outcome.stderr_tail = tail(result.stderr_tail, 4096);
  outcome.cpu_ms = result.evidence.cpu_ms;
  outcome.wall_ms = result.evidence.wall_ms;
  outcome.build_key = protocol::hash_field(protocol::canonical_json(
      {{"source_hash", spec.source_hash},
       {"compiler_profile", spec.compiler_profile},
       {"compiler_version", compiler_version_},
       {"target_platform", spec.platform}}));

  if (result.evidence.setup_failed) {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = result.setup_error;
    fs::remove_all(directory);
    return outcome;
  }
  if (result.evidence.wall_dog_fired) {
    outcome.verdict = "CE";
    outcome.reason = "COMPILE_TIMEOUT";
    outcome.detail = "compiler wall deadline reached";
    fs::remove_all(directory);
    return outcome;
  }
  if (result.evidence.wifsignaled) {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = "compiler was terminated by signal " +
                     std::to_string(result.evidence.term_sig);
    fs::remove_all(directory);
    return outcome;
  }
  if (!result.evidence.wifexited || result.evidence.exit_code != 0) {
    outcome.verdict = "CE";
    outcome.reason = "COMPILE_ERROR";
    outcome.detail = "compiler exited with code " +
                     std::to_string(result.evidence.exit_code);
    fs::remove_all(directory);
    return outcome;
  }
  try {
    if (!fs::is_regular_file(binary_path) || fs::file_size(binary_path) == 0) {
      outcome.verdict = "SE";
      outcome.reason = "SYSTEM_ERROR";
      outcome.detail = "compiler produced no binary";
      fs::remove_all(directory);
      return outcome;
    }
  } catch (const std::exception& error) {
    outcome.verdict = "SE";
    outcome.reason = "SYSTEM_ERROR";
    outcome.detail = std::string("cannot stat binary: ") + error.what();
    fs::remove_all(directory);
    return outcome;
  }
  outcome.status = "COMPILED";
  outcome.reason = "NONE";
  outcome.binary = binary_path.string();
  fs::remove_all(directory);
  return outcome;
}

}  // namespace algoos::worker
