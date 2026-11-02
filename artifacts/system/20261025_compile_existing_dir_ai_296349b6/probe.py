import sys
from judge.compiler import compile_cpp

print("执行者：AI", flush=True)
print("调用真实 compile_cpp，构建目录已存在：", sys.argv[1], flush=True)
compile_cpp("examples/a_plus_b.cpp", sys.argv[1])
print("未拒绝已有目录：调用意外返回", flush=True)
