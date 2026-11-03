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
