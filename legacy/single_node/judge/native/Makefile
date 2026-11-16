CXX ?= g++
CXXFLAGS ?= -std=c++23 -O2 -march=native -pipe -Wall -Wextra -Wpedantic -pthread
JSON_CFLAGS := $(shell pkg-config --cflags json-c 2>/dev/null)
JSON_LIBS := $(shell pkg-config --libs json-c 2>/dev/null || echo -ljson-c)
LDLIBS ?= $(JSON_LIBS)

BUILD_DIR ?= $(CURDIR)
TARGET := $(BUILD_DIR)/eval_server
SAN_TARGET := $(BUILD_DIR)/eval_server_asan

.PHONY: all clean sanitize

all: $(TARGET)

$(TARGET): eval_server.cpp
	@mkdir -p $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $(JSON_CFLAGS) $< -o $@ $(LDLIBS)

sanitize:
	@mkdir -p $(BUILD_DIR)
	$(CXX) -std=c++23 -O1 -g -fsanitize=address,undefined \
		-fno-omit-frame-pointer -Wall -Wextra -Wpedantic -pthread \
		$(JSON_CFLAGS) eval_server.cpp -o $(SAN_TARGET) $(LDLIBS)

clean:
	rm -f $(TARGET) $(SAN_TARGET)

# N3.1 本地入口独立构建，原服务的默认目标与产物路径保留。
LOCAL_BUILD_DIR ?= $(abspath ../../work/native)
LOCAL_TARGET := $(LOCAL_BUILD_DIR)/eval_local

.PHONY: eval-local
eval-local: $(LOCAL_TARGET)

$(LOCAL_TARGET): eval_core.hpp eval_core.cpp eval_task_v1.hpp eval_task_v1.cpp eval_local.cpp ../../protocol/codec_v1.hpp
	@mkdir -p "$(@D)"
	$(CXX) $(filter-out -march=native,$(CXXFLAGS)) $(JSON_CFLAGS) eval_core.cpp eval_task_v1.cpp eval_local.cpp -o "$@" $(LDLIBS)

WORKER_BUILD_DIR ?= $(abspath ../../work/eval-worker-build)
WORKER_TARGET := $(WORKER_BUILD_DIR)/eval-worker
PYTHON ?= python3

.PHONY: eval-worker eval-worker-package
eval-worker: $(WORKER_TARGET)

$(WORKER_TARGET): eval_core.hpp eval_core.cpp eval_task_v1.hpp eval_task_v1.cpp eval_worker.cpp ../../protocol/codec_v1.hpp
	@mkdir -p "$(@D)"
	$(CXX) $(filter-out -march=native,$(CXXFLAGS)) $(JSON_CFLAGS) eval_core.cpp eval_task_v1.cpp eval_worker.cpp -o "$@" $(LDLIBS)

eval-worker-package: $(WORKER_TARGET)
	$(PYTHON) ../../scripts/package_eval_worker.py --binary "$(WORKER_TARGET)" --output-dir "$(WORKER_BUILD_DIR)/packages"

COMPILE_BUILD_DIR ?= $(abspath ../../work/compile-local-build)
COMPILE_TARGET := $(COMPILE_BUILD_DIR)/compile-local

.PHONY: compile-local
compile-local: $(COMPILE_TARGET)

$(COMPILE_TARGET): compile_local.cpp
	@mkdir -p "$(@D)"
	$(CXX) $(filter-out -march=native,$(CXXFLAGS)) $< -o "$@"
