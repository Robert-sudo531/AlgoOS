#include "algoos/worker/server.hpp"

#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <stdexcept>

namespace algoos::worker {
namespace {

constexpr std::size_t kMaxPayload = 8ULL << 20;

void check(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

}  // namespace

JsonServer::JsonServer(Options options, Handler handler, Handler inline_handler)
    : options_(std::move(options)),
      handler_(std::move(handler)),
      inline_handler_(std::move(inline_handler)) {}

JsonServer::~JsonServer() { stop(); }

void JsonServer::start() {
  check(!running_.exchange(true), "server already running");
  listen_fd_ = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  check(listen_fd_ >= 0, std::string("socket: ") + std::strerror(errno));
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  check(options_.socket_path.size() < sizeof(address.sun_path), "socket path too long");
  std::strncpy(address.sun_path, options_.socket_path.c_str(), sizeof(address.sun_path) - 1);
  (void)unlink(options_.socket_path.c_str());
  check(bind(listen_fd_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0,
        std::string("bind: ") + std::strerror(errno));
  (void)chmod(options_.socket_path.c_str(), 0600);
  check(listen(listen_fd_, options_.backlog) == 0,
        std::string("listen: ") + std::strerror(errno));
  workers_.reserve(static_cast<std::size_t>(options_.workers));
  for (int index = 0; index < options_.workers; ++index)
    workers_.emplace_back(&JsonServer::worker_loop, this);
  acceptor_ = std::thread(&JsonServer::accept_loop, this);
}

void JsonServer::stop() {
  if (!running_.exchange(false)) return;
  condition_.notify_all();
  if (listen_fd_ >= 0) {
    (void)shutdown(listen_fd_, SHUT_RDWR);
    (void)close(listen_fd_);
    listen_fd_ = -1;
  }
  (void)unlink(options_.socket_path.c_str());
  if (acceptor_.joinable()) acceptor_.join();
  for (auto& thread : workers_) {
    if (thread.joinable()) thread.join();
  }
  workers_.clear();
}

bool JsonServer::read_frame(int fd, std::string& payload) {
  uint32_t length = 0;
  ssize_t got = 0;
  char* target = reinterpret_cast<char*>(&length);
  std::size_t remaining = sizeof(length);
  while (remaining > 0) {
    got = read(fd, target, remaining);
    if (got < 0 && errno == EINTR) continue;
    if (got <= 0) return false;
    target += got;
    remaining -= static_cast<std::size_t>(got);
  }
  const uint32_t host_length = ntohl(length);
  if (host_length > kMaxPayload) return false;
  payload.resize(host_length);
  target = payload.data();
  remaining = host_length;
  while (remaining > 0) {
    got = read(fd, target, remaining);
    if (got < 0 && errno == EINTR) continue;
    if (got <= 0) return false;
    target += got;
    remaining -= static_cast<std::size_t>(got);
  }
  return true;
}

bool JsonServer::write_frame(int fd, const std::string& payload) {
  const uint32_t length = htonl(static_cast<uint32_t>(payload.size()));
  std::string frame(reinterpret_cast<const char*>(&length), sizeof(length));
  frame += payload;
  std::size_t written = 0;
  while (written < frame.size()) {
    const auto got = write(fd, frame.data() + written, frame.size() - written);
    if (got < 0 && errno == EINTR) continue;
    if (got <= 0) return false;
    written += static_cast<std::size_t>(got);
  }
  return true;
}

void JsonServer::respond(int fd, const protocol::Json& response) {
  (void)write_frame(fd, response.dump());
}

void JsonServer::accept_loop() {
  while (running_.load()) {
    int fd = accept4(listen_fd_, nullptr, nullptr, SOCK_CLOEXEC);
    if (fd < 0) {
      if (errno == EINTR) continue;
      break;
    }
    std::string payload;
    if (!read_frame(fd, payload)) {
      close(fd);
      continue;
    }
    bool inline_request = false;
    try {
      auto request = protocol::Json::parse(payload);
      if (protocol::has(request.get(), "cmd")) {
        const auto cmd = protocol::string_field(request.get(), "cmd", true);
        inline_request = cmd == "cancel" || cmd == "ping" || cmd == "stats";
      }
    } catch (const std::exception&) {
      inline_request = true;  // parse errors are cheap to answer
    }
    if (inline_request) {
      handle_one(fd, std::move(payload), true);
      continue;
    }
    {
      std::lock_guard guard(mutex_);
      if (static_cast<int64_t>(queue_.size()) >= options_.queue_capacity) {
        protocol::Json busy = protocol::Json::object();
        busy.add("ok", json_object_new_boolean(false));
        busy.add_string("error", "busy");
        respond(fd, busy);
        close(fd);
        continue;
      }
      queue_.push_back(Request{fd, std::move(payload)});
    }
    condition_.notify_one();
  }
}

void JsonServer::worker_loop() {
  for (;;) {
    Request request{-1, {}};
    {
      std::unique_lock lock(mutex_);
      condition_.wait(lock, [&] { return !running_.load() || !queue_.empty(); });
      if (!running_.load() && queue_.empty()) return;
      request = std::move(queue_.front());
      queue_.pop_front();
    }
    handle_one(request.fd, std::move(request.payload), false);
  }
}

void JsonServer::handle_one(int fd, std::string payload, bool inline_request) {
  protocol::Json response = protocol::Json::object();
  try {
    auto request = protocol::Json::parse(payload);
    auto result = inline_request ? inline_handler_(std::move(request)) : handler_(std::move(request));
    response = std::move(result);
  } catch (const std::exception& error) {
    protocol::Json failure = protocol::Json::object();
    failure.add("ok", json_object_new_boolean(false));
    failure.add_string("error", error.what());
    response = std::move(failure);
  }
  respond(fd, response);
  close(fd);
}

}  // namespace algoos::worker
