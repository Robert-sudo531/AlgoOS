"""normalize_common must match the C++ comparator semantics."""

from algoos_control.text import normalize_common


def test_normalize_matches_contract() -> None:
    assert normalize_common(b"a\r\nb  \r\n\n") == b"a\nb"
    assert normalize_common(b" x \t\n") == b" x"
    assert normalize_common(b"\n\n") == b""
    assert normalize_common(b"") == b""
    assert normalize_common(b"a\rb") == b"a\nb"
