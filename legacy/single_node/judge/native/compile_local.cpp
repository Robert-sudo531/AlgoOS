// Local cpp_baseline_v1 entry point. Only accepts local, prepared source files.
#include <cerrno>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <spawn.h>
#include <stdexcept>
#include <sys/wait.h>

extern char** environ;
namespace fs = std::filesystem;

int main(int argc, char** argv) {
    try {
        if (argc != 3) throw std::runtime_error("usage: compile-local SOURCE NEW_OUTPUT_DIR");
        auto source = fs::absolute(argv[1]).string();
        const auto directory = fs::absolute(argv[2]);
        if (!fs::is_regular_file(source)) throw std::runtime_error("source is not a regular file");
        if (!fs::create_directory(directory)) throw std::runtime_error("output directory already exists");
        auto binary = (directory / "program").string();
        // Absolute source path cannot be mistaken for an extra compiler option.
        char compiler[] = "g++", standard[] = "-std=c++17", optimization[] = "-O2";
        char pipe_flag[] = "-pipe", output_flag[] = "-o", language_flag[] = "-x", language[] = "c++";
        char* args[] = {compiler, standard, optimization, pipe_flag, language_flag, language,
                        source.data(), output_flag, binary.data(), nullptr};
        pid_t pid;
        const int error = posix_spawnp(&pid, compiler, nullptr, nullptr, args, environ);
        if (error) throw std::runtime_error(std::string("g++ launch: ") + std::strerror(error));
        int status;
        pid_t finished;
        do { finished = waitpid(pid, &status, 0); } while (finished < 0 && errno == EINTR);
        if (finished != pid || !WIFEXITED(status)) throw std::runtime_error("compiler did not exit normally");
        if (WEXITSTATUS(status) == 1) {
            std::cout << "CE\n";
            return 2;
        }
        if (WEXITSTATUS(status) != 0 || !fs::is_regular_file(binary))
            throw std::runtime_error("compiler failed or produced no binary");
        std::cout << "COMPILED\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "compile-local: " << error.what() << '\n';
        return 1;
    }
}
