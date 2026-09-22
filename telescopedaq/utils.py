from __future__ import annotations

try:
    import msvcrt
except ImportError:
    msvcrt = None


def stop_key_pressed() -> bool:
    if msvcrt is None:
        return False
    while msvcrt.kbhit():
        key = msvcrt.getwch()
        if key in {"\r", "\n", "\x1b"}:
            return True
        if key in {"\x00", "\xe0"} and msvcrt.kbhit():
            msvcrt.getwch()
    return False
