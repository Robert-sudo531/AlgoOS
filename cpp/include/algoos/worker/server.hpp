#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "algoos/protocol/json.hpp"

namespace algoos::worker {

/// Bounded multi-threaded Unix-socket server used by all native workers.
///
/// Wire format is ``uint32 big-endian length + UTF-8 JSON``.  A request whose
/// top-level ``cmd`` is in ``inline_commands`` is handled on the accept thread
/// (cancellation and ping must not queue behind long-running cases).  Other
/// requests are placed in a bounded queue; when the queue is full the server
/// answers ``{"ok":false,"error":"busy"}`` which lets the Python agent apply
/// backpressure instead of growing memory.
class JsonServer {
 public:
  struct Options {
    std::string socket_path;
    int64_t queue_capacity{1024};
    int workers{4};
    int backlog{128};
  };

  using Handler = std::function<algoos::protocol::Json(algoos::protocol::Json)>;

  JsonServer(Options options, Handler handler, Handler inline_handler);
  ~JsonServer();

  void start();
  void stop();

 private:
  struct Request {
    int fd;
    std::string payload;
  };

  void accept_loop();
  void worker_loop();
  void handle_one(int fd, std::string payload, bool inline_request);
  void respond(int fd, const algoos::protocol::Json& response);
  static bool read_frame(int fd, std::string& payload);
  static bool write_frame(int fd, const std::string& payload);

  Options options_;
  Handler handler_;
  Handler inline_handler_;
  int listen_fd_{-1};
  std::atomic<bool> running_{false};
  std::mutex mutex_;
  std::condition_variable condition_;
  std::deque<Request> queue_;
  std::vector<std::thread> workers_;
  std::thread acceptor_;
};

}  // namespace algoos::worker
