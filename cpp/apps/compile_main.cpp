// Native compile worker for the frozen cpp_baseline_v1 profile.
#include <signal.h>

#include <atomic>
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

#include "algoos/protocol/json.hpp"
#include "algoos/protocol/messages.hpp"
#include "algoos/worker/compile_service.hpp"
#include "algoos/worker/socket_server.hpp"

namespace {
using algoos::protocol::Json;
std::atomic<bool> g_stop{false};
void handle_signal(int) { g_stop.store(true); }

struct Options {
  std::string socket{"/tmp/algoos-compile.sock"};
  std::string work_root{"/tmp/algoos-compile"};
  std::string compiler{"/usr/bin/g++"};
  std::string platform{"linux-x86_64"};
  int workers{2};
  int64_t queue_capacity{256};
};

Options parse_options(int argc, char** argv) {
  Options options;
  for (int index = 1; index < argc; ++index) {
    const std::string arg = argv[index];
    auto next = [&](const char* name) {
      if (index + 1 >= argc) throw std::runtime_error(std::string("missing value for ") + name);
      return std::string(argv[++index]);
    };
    if (arg == "--socket") options.socket = next("--socket");
    else if (arg == "--work-root") options.work_root = next("--work-root");
    else if (arg == "--compiler") options.compiler = next("--compiler");
    else if (arg == "--platform") options.platform = next("--platform");
    else if (arg == "--workers") options.workers = std::stoi(next("--workers"));
    else if (arg == "--queue-capacity") options.queue_capacity = std::stoll(next("--queue-capacity"));
    else throw std::runtime_error("unknown option: " + arg);
  }
  return options;
}
}  // namespace

int main(int argc, char** argv) {
  try {
    const auto options = parse_options(argc, argv);
    algoos::worker::CompileService::Config config;
    config.compiler = options.compiler;
    config.work_root = options.work_root;
    config.platform = options.platform;
    algoos::worker::CompileService service(config);

    auto run_handler = [&](Json request) {
      if (algoos::protocol::string_field(request.get(), "cmd") != "compile")
        throw std::runtime_error("expected cmd=compile");
      auto spec = algoos::protocol::parse_compile_spec(
          algoos::protocol::object_field(request.get(), "spec"));
      if (spec.compiler != "g++" || spec.compiler_profile != "cpp_baseline_v1" ||
          spec.flags != std::vector<std::string>{"-std=c++17", "-O2", "-pipe"})
        throw std::runtime_error("unsupported compiler profile");
      spec.compiler = options.compiler;
      if (spec.platform.empty()) spec.platform = options.platform;
      const auto outcome = service.compile(spec);
      Json response = Json::object();
      response.add("ok", json_object_new_boolean(true));
      response.add_string("status", outcome.status);
      response.add_string("verdict", outcome.verdict);
      response.add_string("reason", outcome.reason);
      response.add_string("detail", outcome.detail);
      response.add_string("stderr_tail", outcome.stderr_tail);
      response.add_string("binary", outcome.binary);
      response.add_string("build_key", outcome.build_key);
      response.add_string("compiler_version", outcome.compiler_version);
      response.add_string("compiler_profile", outcome.compiler_profile);
      response.add("cpu_ms", json_object_new_int64(outcome.cpu_ms));
      response.add("wall_ms", json_object_new_int64(outcome.wall_ms));
      return response;
    };

    auto inline_handler = [&](Json request) {
      const auto cmd = algoos::protocol::string_field(request.get(), "cmd");
      Json response = Json::object();
      response.add("ok", json_object_new_boolean(true));
      if (cmd == "ping") {
        response.add_string("status", "ready");
        response.add_string("role", "compile");
        response.add_string("compiler_version", service.compiler_version());
      } else if (cmd != "stats") {
        throw std::runtime_error("unsupported inline command: " + cmd);
      }
      return response;
    };

    algoos::worker::JsonServer::Options server_options;
    server_options.socket_path = options.socket;
    server_options.queue_capacity = options.queue_capacity;
    server_options.workers = options.workers;
    algoos::worker::JsonServer server(server_options, run_handler, inline_handler);
    server.start();
    struct sigaction action {};
    action.sa_handler = handle_signal;
    sigaction(SIGINT, &action, nullptr);
    sigaction(SIGTERM, &action, nullptr);
    std::cout << "algoos-compile ready socket=" << options.socket
              << " compiler=" << service.compiler_version() << '\n';
    while (!g_stop.load()) std::this_thread::sleep_for(std::chrono::milliseconds(100));
    server.stop();
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "algoos-compile: " << error.what() << '\n';
    return 1;
  }
}
