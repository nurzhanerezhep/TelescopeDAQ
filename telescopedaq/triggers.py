"""Trigger source routing for DT5740D STANDARD firmware."""

from __future__ import annotations

import ctypes as ct
import time

from .caen_constants import (
    CAEN_DGTZ_TRGMODE_ACQ_ONLY,
    CAEN_DGTZ_TRGMODE_DISABLED,
    CAEN_DGTZ_IOLEVEL_NIM,
    CAEN_DGTZ_IOLEVEL_TTL,
)

TRIGGER_CODES = {"threshold": 1, "external": 2, "periodic": 3}


def configure_triggers(board, config) -> None:
    mode = config.trigger["mode"]
    external = config.data["external"]
    if mode == "external" and (
        external["polarity"] != "rising" or not external["save_all_enabled_channels"]
    ):
        raise ValueError(
            "Unsupported TRG-IN configuration: leading edge and all enabled channels required"
        )
    board._check(
        board.self_trigger_fn(board.handle, CAEN_DGTZ_TRGMODE_DISABLED, 0xF),
        "Disable group triggers",
    )
    if mode == "threshold":
        board._check(
            board.self_trigger_fn(board.handle, CAEN_DGTZ_TRGMODE_ACQ_ONLY, 1),
            "Enable group 0 trigger",
        )
    for source, fn in (
        ("periodic", board.sw_trigger_mode_fn),
        ("external", board.ext_trigger_fn),
    ):
        board._check(
            fn(
                board.handle,
                CAEN_DGTZ_TRGMODE_ACQ_ONLY
                if mode == source
                else CAEN_DGTZ_TRGMODE_DISABLED,
            ),
            f"Set {source} trigger",
        )
    level = (
        CAEN_DGTZ_IOLEVEL_TTL
        if external.get("io_level", "NIM") == "TTL"
        else CAEN_DGTZ_IOLEVEL_NIM
    )
    board._check(board.io_level_fn(board.handle, level), "SetIOLevel")
    if mode == "external":
        actual_mode, actual_level = ct.c_int(), ct.c_int()
        board._check(
            board.get_ext_trigger_fn(board.handle, ct.byref(actual_mode)),
            "GetExtTriggerInputMode",
        )
        board._check(
            board.get_io_level_fn(board.handle, ct.byref(actual_level)), "GetIOLevel"
        )
        if (
            actual_mode.value != CAEN_DGTZ_TRGMODE_ACQ_ONLY
            or actual_level.value != level
        ):
            raise RuntimeError("TRG-IN configuration readback mismatch")


class PeriodicTrigger:
    def __init__(self, interval_s: float):
        self.interval = interval_s
        self.next_at = time.monotonic() + interval_s

    def tick(self, board, now: float) -> None:
        if now >= self.next_at:
            board.send_software_trigger()
            self.next_at += self.interval * (
                int((now - self.next_at) // self.interval) + 1
            )
