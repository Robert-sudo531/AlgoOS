from judge.checker import check_output

assert check_output("3\n", "3\n")
assert not check_output("4\n", "3\n")
assert check_output("3\r\n", "3\n")
assert check_output("3\r", "3\n")
assert not check_output("3 \n", "3\n")
assert not check_output("3", "3\n")

print("6项输出比对检查通过")