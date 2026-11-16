// Native special-judge worker: runs an external checker under the sandbox.
#include <signal.h>

#include <atomic>
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

#include "algoos/protocol/json.hpp"
#include "algoos/protocol/messages.hpp"
#include "algoos/worker/socket_server.hpp"
#include "algoos/worker/special_service.hpp"

namespace {
using algoos::protocol::Json;
std::atomic<bool> g_stop{false};
void handle_signal(int) { g_stop.store(true); }

struct Options {
  std::string socket{"/tmp/algoos-special.sock"};
  std::string work_root{"/tmp/algoos-special"};
  int workers{2};
  int64_t queue_capacity{512};
  bool use_cgroup{true};
  bool use_seccomp{true};
  bool use_namespaces{true};
  bool readonly_root{true};
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
    else if (arg == "--workers") options.workers = std::stoi(next("--workers"));
    else if (arg == "--queue-capacity") options.queue_capacity = std::stoll(next("--queue-capacity"));
    else if (arg == "--no-cgroup") options.use_cgroup = false;
    else if (arg == "--no-seccomp") options.use_seccomp = false;
    else if (arg == "--no-namespaces") options.use_namespaces = false;
    else if (arg == "--writable-root") options.readonly_root = false;
    else throw std::runtime_error("unknown option: " + arg);
  }
  return options;
}
}  // namespace

int main(int argc, char** argv) {
  try {
    const auto options = parse_options(argc, argv);
    algoos::worker::SpecialService::Config config;
    config.work_root = options.work_root;
    config.sandbox.use_cgroup = options.use_cgroup;
    config.sandbox.use_seccomp = options.use_seccomp;
    config.sandbox.use_namespaces = options.use_namespaces;
    config.sandbox.readonly_root = options.readonly_root;
    algoos::worker::SpecialService service(config);

    auto run_handler = [&](Json request) {
      if (algoos::protocol::string_field(request.get(), "cmd") != "judge")
        throw std::runtime_error("expected cmd=judge");
      const auto task = algoos::protocol::parse_special_task(
          algoos::protocol::object_field(request.get(), "task"));
      auto* paths_json = algoos::protocol::object_field(request.get(), "paths");
      const algoos::worker::SpecialPaths paths{
          algoos::protocol::string_field(paths_json, "input"),
          algoos::protocol::string_field(paths_json, "output"),
          algoos::protocol::string_field(paths_json, "answer"),
          algoos::protocol::string_field(paths_json, "checker"),
      };
      const auto outcome = service.judge(task, paths);
      Json response = Json::object();
      response.add("ok", json_object_new_boolean(true));
      response.add_string("verdict", outcome.verdict);
      response.add_string("reason", outcome.reason);
      response.add_string("detail", outcome.detail);
      response.add_string("stderr_tail", outcome.stderr_tail);
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
        response.add_string("role", "special");
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
    std::cout << "algoos-special ready socket=" << options.socket << '\n';
    while (!g_stop.load()) std::this_thread::sleep_for(std::chrono::milliseconds(100));
    server.stop();
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "algoos-special: " << error.what() << '\n';
    return 1;
  }
}
