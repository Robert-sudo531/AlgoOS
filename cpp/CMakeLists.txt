cmake_minimum_required(VERSION 3.28)
project(algoos_runner LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

option(ALGOOS_SANITIZE "Build with ASan/UBSan" OFF)
option(ALGOOS_BUILD_TESTS "Build unit tests" ON)

find_package(PkgConfig REQUIRED)
pkg_check_modules(JSONC REQUIRED json-c)
pkg_check_modules(SECCOMP REQUIRED libseccomp)
find_package(Threads REQUIRED)

add_compile_options(-Wall -Wextra -Wpedantic -Wconversion -Wshadow -pipe)
add_compile_options(-O2 -march=native)
if(ALGOOS_SANITIZE)
  add_compile_options(-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer)
  add_link_options(-fsanitize=address,undefined)
endif()

add_library(algoos_core STATIC
  src/protocol/json.cpp
  src/protocol/messages.cpp
  src/protocol/digest.cpp
  src/sandbox/sandbox.cpp
  src/sandbox/classify.cpp
  src/runner/comparator.cpp
  src/worker/server.cpp
  src/worker/cancel_registry.cpp
  src/worker/compile_service.cpp
  src/worker/eval_service.cpp
  src/worker/special_service.cpp
)
target_include_directories(algoos_core PUBLIC include ${JSONC_INCLUDE_DIRS} ${SECCOMP_INCLUDE_DIRS})
target_link_directories(algoos_core PUBLIC ${JSONC_LIBRARY_DIRS} ${SECCOMP_LIBRARY_DIRS})
target_link_libraries(algoos_core PUBLIC ${JSONC_LIBRARIES} ${SECCOMP_LIBRARIES} Threads::Threads)

foreach(app algoos-eval algoos-compile algoos-special)
  string(REPLACE "-" "_" app_target ${app})
  add_executable(${app_target} apps/${app}.cpp)
  target_link_libraries(${app_target} PRIVATE algoos_core)
  set_target_properties(${app_target} PROPERTIES OUTPUT_NAME ${app})
endforeach()

if(ALGOOS_BUILD_TESTS)
  enable_testing()
  add_executable(algoos_unit_tests tests/unit_tests.cpp)
  target_link_libraries(algoos_unit_tests PRIVATE algoos_core)
  add_test(NAME algoos_unit COMMAND algoos_unit_tests)
endif()
