from __future__ import annotations

import msvcrt


def stop_key_pressed() -> bool:
    while msvcrt.kbhit():
        key = msvcrt.getwch()
        if key in {"\r", "\n", "\x1b"}:
            return True
        if key in {"\x00", "\xe0"} and msvcrt.kbhit():
            msvcrt.getwch()
    return False
