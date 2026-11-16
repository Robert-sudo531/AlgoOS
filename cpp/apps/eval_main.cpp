// Native evaluation worker: bounded RunnerPool + SandboxManager + CancelRegistry.
#include <signal.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

#include "algoos/protocol/json.hpp"
#include "algoos/protocol/messages.hpp"
#include "algoos/worker/cancel_registry.hpp"
#include "algoos/worker/eval_service.hpp"
#include "algoos/worker/socket_server.hpp"

namespace {
using algoos::protocol::Json;
std::atomic<bool> g_stop{false};

void handle_signal(int) { g_stop.store(true); }

struct Options {
  std::string socket{"/tmp/algoos-eval.sock"};
  std::string node_id{"eval-01"};
  std::string work_root{"/tmp/algoos-eval"};
  int workers{4};
  int64_t queue_capacity{1024};
  bool use_cgroup{true};
  bool use_seccomp{true};
  bool use_namespaces{true};
  bool readonly_root{true};
  std::string cgroup_root{"/sys/fs/cgroup"};
};

Options parse_options(int argc, char** argv) {
  Options options;
  for (int index = 1; index < argc; ++index) {
    const std::string arg = argv[index];
    auto next = [&](const char* name) -> std::string {
      if (index + 1 >= argc) throw std::runtime_error(std::string("missing value for ") + name);
      return argv[++index];
    };
    if (arg == "--socket") options.socket = next("--socket");
    else if (arg == "--node-id") options.node_id = next("--node-id");
    else if (arg == "--work-root") options.work_root = next("--work-root");
    else if (arg == "--workers") options.workers = std::stoi(next("--workers"));
    else if (arg == "--queue-capacity") options.queue_capacity = std::stoll(next("--queue-capacity"));
    else if (arg == "--cgroup-root") options.cgroup_root = next("--cgroup-root");
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
    algoos::worker::CancelRegistry registry;
    algoos::worker::EvalService::Config config;
    config.work_root = options.work_root;
    config.sandbox.use_cgroup = options.use_cgroup;
    config.sandbox.use_seccomp = options.use_seccomp;
    config.sandbox.use_namespaces = options.use_namespaces;
    config.sandbox.readonly_root = options.readonly_root;
    config.sandbox.cgroup_root = options.cgroup_root;
    algoos::worker::EvalService service(config, registry);

    auto run_handler = [&](Json request) {
      const auto cmd = algoos::protocol::string_field(request.get(), "cmd");
      auto* task_json = algoos::protocol::object_field(request.get(), "task");
      const auto task = algoos::protocol::parse_eval_task(task_json);
      if (task.target_node_id != options.node_id)
        throw std::runtime_error("task target node differs from this worker");
      auto* paths_json = algoos::protocol::object_field(request.get(), "paths");
      const algoos::worker::EvalPaths paths{
          algoos::protocol::string_field(paths_json, "binary"),
          algoos::protocol::string_field(paths_json, "input"),
          algoos::protocol::has(paths_json, "answer") &&
                  algoos::protocol::field(paths_json, "answer") != nullptr
              ? algoos::protocol::string_field(paths_json, "answer")
              : std::string(),
      };
      if (cmd == "run") {
        if (paths.answer.empty()) throw std::runtime_error("run requires an answer path");
        const auto outcome = service.judge(task, paths);
        auto result = algoos::protocol::make_eval_result(
            task, options.node_id, outcome.verdict, outcome.reason, outcome.evidence,
            outcome.out_bytes, outcome.memory_source, outcome.stderr_tail, outcome.detail,
            outcome.short_circuited);
        return result;
      }
      if (cmd != "run_raw") throw std::runtime_error("expected cmd=run|run_raw");
      const auto outcome = service.run_raw(task, paths);
      Json response = Json::object();
      response.add("ok", json_object_new_boolean(true));
      if (outcome.cancelled) {
        response.add_string("kind", "cancelled");
        response.add_string("verdict", outcome.verdict.empty() ? "CANCELLED" : outcome.verdict);
        response.add_string("reason", outcome.reason);
      } else if (outcome.comparable) {
        response.add_string("kind", "compare");
        response.add_string("out_path", outcome.out_path);
        response.add_string("work_dir", outcome.work_dir);
      } else {
        response.add_string("kind", "resource");
        response.add_string("verdict", outcome.verdict);
        response.add_string("reason", outcome.reason);
        response.add_string("out_path", outcome.out_path);
        response.add_string("work_dir", outcome.work_dir);
      }
      response.add("cpu_ms", json_object_new_int64(outcome.evidence.cpu_ms));
      response.add("wall_ms", json_object_new_int64(outcome.evidence.wall_ms));
      response.add("wall_timeout",
                   json_object_new_boolean(outcome.evidence.wall_dog_fired));
      response.add("out_bytes", json_object_new_int64(outcome.out_bytes));
      if (outcome.evidence.wifsignaled) {
        response.add_null("exit_code");
        response.add("signal", json_object_new_int64(outcome.evidence.term_sig));
      } else if (outcome.evidence.wifexited) {
        response.add("exit_code", json_object_new_int64(outcome.evidence.exit_code));
        response.add("signal", json_object_new_int(0));
      } else {
        response.add_null("exit_code");
        response.add_null("signal");
      }
      if (outcome.evidence.cgroup_memory_peak) {
        response.add("mem_bytes",
                     json_object_new_int64(*outcome.evidence.cgroup_memory_peak));
        response.add_string("memory_source", "cgroup_peak");
      } else {
        response.add("mem_bytes",
                     json_object_new_int64(outcome.evidence.ru_maxrss_bytes));
        response.add_string("memory_source", "rss_peak");
      }
      response.add_string("stderr_tail", outcome.stderr_tail);
      response.add_string("detail", outcome.detail);
      return response;
    };

    auto inline_handler = [&](Json request) {
      const auto cmd = algoos::protocol::string_field(request.get(), "cmd");
      Json response = Json::object();
      response.add("ok", json_object_new_boolean(true));
      if (cmd == "ping") {
        response.add_string("status", "ready");
        response.add_string("node_id", options.node_id);
        response.add_string("role", "eval");
        response.add("active", json_object_new_int64(
                                  static_cast<int64_t>(registry.active_count())));
      } else if (cmd == "cancel") {
        const auto file_id = algoos::protocol::string_field(request.get(), "file_id");
        const auto reason = algoos::protocol::has(request.get(), "reason")
                                ? algoos::protocol::string_field(request.get(), "reason")
                                : std::string("SHORT_CIRCUITED");
        if (algoos::protocol::has(request.get(), "judge_id") &&
            algoos::protocol::field(request.get(), "judge_id") != nullptr) {
          registry.cancel_case(file_id, algoos::protocol::string_field(request.get(), "judge_id"),
                               reason);
        } else {
          std::optional<int64_t> boundary;
          if (algoos::protocol::has(request.get(), "sequence_boundary") &&
              algoos::protocol::field(request.get(), "sequence_boundary") != nullptr) {
            boundary = algoos::protocol::int_field(request.get(), "sequence_boundary", 1);
          }
          registry.cancel_submission(file_id, boundary, reason);
        }
      } else if (cmd == "stats") {
        response.add("active", json_object_new_int64(
                                  static_cast<int64_t>(registry.active_count())));
      } else {
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
    std::cout << "algoos-eval ready node=" << options.node_id << " socket=" << options.socket
              << " workers=" << options.workers << '\n';
    while (!g_stop.load()) std::this_thread::sleep_for(std::chrono::milliseconds(100));
    server.stop();
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "algoos-eval: " << error.what() << '\n';
    return 1;
  }
}
