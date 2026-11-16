"""common comparison normalization (mirrors cpp/src/runner/comparator.cpp).

The C++ worker owns final comparison; Python only derives the semantic output
limit from the standard answer at import time.  Keep the two implementations
byte-for-byte compatible; protocol tests check a shared fixture.
"""

from __future__ import annotations


def normalize_common(data: bytes) -> bytes:
    lines = []
    current = bytearray()
    index = 0
    while index < len(data):
        value = data[index]
        if value in (10, 13):
            _append_line(lines, current)
            current.clear()
            if value == 13 and index + 1 < len(data) and data[index + 1] == 10:
                index += 1
        else:
            current.append(value)
        index += 1
    _append_line(lines, current)
    while lines and lines[-1] == b"":
        lines.pop()
    if not lines:
        return b""
    return b"\n".join(lines)


def _append_line(lines: list[bytes], current: bytearray) -> None:
    lines.append(bytes(current).rstrip(b" \t"))
