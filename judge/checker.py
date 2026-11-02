def normalize_newlines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def check_output(actual_output, expected_output):
    return (
        normalize_newlines(actual_output)
        == normalize_newlines(expected_output)
    )