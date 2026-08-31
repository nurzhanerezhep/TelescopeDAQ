from __future__ import annotations

import ctypes as ct
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

from .caen_constants import *  # constants mirror the installed C header
from .event import Event

LOG = logging.getLogger(__name__)


class CAENError(RuntimeError):
    pass


def _text(value: bytes) -> str:
    return value.split(b"\0", 1)[0].decode("ascii", errors="replace")


class CAENDigitizer:
    """Real DT5740 standard-waveform backend using CAENDigitizer.dll."""

    def __init__(self) -> None:
        self.lib: ct.WinDLL | None = None
        self.dll_path: Path | None = None
        self.handle = ct.c_int(-1)
        self.buffer = ct.c_char_p()
        self.buffer_size = ct.c_uint32()
        self.event_ptr = ct.c_void_p()
        self.is_open = False
        self.is_running = False
        self._event_id = 0
        self._timestamp_epoch = 0
        self._last_timetag = 0
        self._polarity = "negative"

    def _load_library(self) -> None:
        configured = os.environ.get("CAEN_DIGITIZER_DLL")
        candidates = [
            Path(configured) if configured else None,
            Path(r"C:\Program Files\CAEN\Digitizers\WaveDump\bin\CAENDigitizer.dll"),
            Path(r"C:\Program Files\CAEN\Digitizers\CAEN Dig1\bin\x86_64\CAENDigitizer.dll"),
            Path(r"C:\Program Files\CAEN\CoMPASS\server\win\CAENDigitizer.dll"),
        ]
        self.dll_path = next((p for p in candidates if p and p.is_file()), None)
        if self.dll_path is None:
            raise CAENError(
                "Не найдена CAENDigitizer.dll. Установите 64-bit CAENDigitizer Library "
                "или задайте CAEN_DIGITIZER_DLL. В Linux проверьте LD_LIBRARY_PATH."
            )
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(self.dll_path.parent))
        self.lib = ct.WinDLL(str(self.dll_path))
        self._declare_api()

    def _fn(self, name: str, *args: Any) -> Any:
        assert self.lib is not None
        fn = getattr(self.lib, f"CAEN_DGTZ_{name}")
        fn.argtypes = list(args)
        fn.restype = ct.c_int
        return fn

    def _declare_api(self) -> None:
        cint, u32, vp = ct.c_int, ct.c_uint32, ct.c_void_p
        self.open_fn = self._fn("OpenDigitizer", cint, cint, cint, u32, ct.POINTER(cint))
        self.close_fn = self._fn("CloseDigitizer", cint)
        self.reset_fn = self._fn("Reset", cint)
        self.info_fn = self._fn("GetInfo", cint, ct.POINTER(BoardInfo))
        self.record_fn = self._fn("SetRecordLength", cint, u32)
        self.post_fn = self._fn("SetPostTriggerSize", cint, u32)
        self.group_mask_fn = self._fn("SetGroupEnableMask", cint, u32)
        self.group_offset_fn = self._fn("SetGroupDCOffset", cint, u32, u32)
        self.threshold_fn = self._fn("SetGroupTriggerThreshold", cint, u32, u32)
        self.polarity_fn = self._fn("SetTriggerPolarity", cint, u32, cint)
        self.acq_mode_fn = self._fn("SetAcquisitionMode", cint, cint)
        self.self_trigger_fn = self._fn("SetGroupSelfTrigger", cint, cint, u32)
        self.sw_trigger_mode_fn = self._fn("SetSWTriggerMode", cint, cint)
        self.ext_trigger_fn = self._fn("SetExtTriggerInputMode", cint, cint)
        self.io_level_fn = self._fn("SetIOLevel", cint, cint)
        self.max_blt_fn = self._fn("SetMaxNumEventsBLT", cint, u32)
        self.clear_fn = self._fn("ClearData", cint)
        self.start_fn = self._fn("SWStartAcquisition", cint)
        self.stop_fn = self._fn("SWStopAcquisition", cint)
        self.send_trigger_fn = self._fn("SendSWtrigger", cint)
        self.malloc_buffer_fn = self._fn("MallocReadoutBuffer", cint, ct.POINTER(ct.c_char_p), ct.POINTER(u32))
        self.free_buffer_fn = self._fn("FreeReadoutBuffer", ct.POINTER(ct.c_char_p))
        self.allocate_event_fn = self._fn("AllocateEvent", cint, ct.POINTER(vp))
        self.free_event_fn = self._fn("FreeEvent", cint, ct.POINTER(vp))
        self.read_fn = self._fn("ReadData", cint, cint, ct.c_char_p, ct.POINTER(u32))
        self.num_events_fn = self._fn("GetNumEvents", cint, ct.c_char_p, u32, ct.POINTER(u32))
        self.event_info_fn = self._fn("GetEventInfo", cint, ct.c_char_p, u32, ct.c_int32, ct.POINTER(EventInfo), ct.POINTER(ct.c_char_p))
        self.decode_fn = self._fn("DecodeEvent", cint, ct.c_char_p, ct.POINTER(vp))

    def _check(self, code: int, function: str) -> None:
        if code != 0:
            name = ERROR_NAMES.get(code, "UnknownError")
            raise CAENError(f"{function}: CAEN error {code} ({name})")

    def open(self, config: Any) -> dict[str, Any]:
        self._load_library()
        c = config.caen
        self._check(self.open_fn(CAEN_DGTZ_USB, int(c["link_num"]), int(c["conet_node"]), int(c["vme_base_address"]), ct.byref(self.handle)), "CAEN_DGTZ_OpenDigitizer")
        self.is_open = True
        info = BoardInfo()
        self._check(self.info_fn(self.handle, ct.byref(info)), "CAEN_DGTZ_GetInfo")
        board = {
            "model": _text(bytes(info.ModelName)), "serial_number": int(info.SerialNumber),
            "hardware_groups": int(info.Channels), "adc_bits": int(info.ADC_NBits),
            "roc_firmware": _text(bytes(info.ROC_FirmwareRel)),
            "amc_firmware": _text(bytes(info.AMC_FirmwareRel)),
            "dll": str(self.dll_path),
        }
        if not board["model"].startswith("DT5740"):
            raise CAENError(f"Ожидался DT5740, подключён {board['model']}")
        return board

    def reset(self) -> None:
        self._check(self.reset_fn(self.handle), "CAEN_DGTZ_Reset")

    def configure(self, config: Any) -> None:
        c, channels, trigger = config.caen, config.channels, config.trigger
        self._polarity = channels["polarity"]
        threshold = int(channels["thresholds_adc"].get(0, channels["thresholds_adc"].get("0")))
        group_mask = 1
        self._check(self.acq_mode_fn(self.handle, CAEN_DGTZ_SW_CONTROLLED), "CAEN_DGTZ_SetAcquisitionMode")
        self._check(self.record_fn(self.handle, int(c["record_length_samples"])), "CAEN_DGTZ_SetRecordLength")
        self._check(self.post_fn(self.handle, 100 - int(c["pre_trigger_percent"])), "CAEN_DGTZ_SetPostTriggerSize")
        self._check(self.group_mask_fn(self.handle, group_mask), "CAEN_DGTZ_SetGroupEnableMask")
        self._check(self.group_offset_fn(self.handle, 0, int(c.get("dc_offset", 32768))), "CAEN_DGTZ_SetGroupDCOffset")
        self._check(self.threshold_fn(self.handle, 0, threshold), "CAEN_DGTZ_SetGroupTriggerThreshold")
        edge = CAEN_DGTZ_TRIGGER_ON_FALLING_EDGE if self._polarity == "negative" else CAEN_DGTZ_TRIGGER_ON_RISING_EDGE
        self._check(self.polarity_fn(self.handle, 0, edge), "CAEN_DGTZ_SetTriggerPolarity")
        self._check(self.self_trigger_fn(self.handle, CAEN_DGTZ_TRGMODE_ACQ_ONLY if trigger["mode"] == "self" else CAEN_DGTZ_TRGMODE_DISABLED, group_mask), "CAEN_DGTZ_SetGroupSelfTrigger")
        self._check(self.sw_trigger_mode_fn(self.handle, CAEN_DGTZ_TRGMODE_ACQ_ONLY if trigger.get("software_trigger_enabled") else CAEN_DGTZ_TRGMODE_DISABLED), "CAEN_DGTZ_SetSWTriggerMode")
        self._check(self.ext_trigger_fn(self.handle, CAEN_DGTZ_TRGMODE_ACQ_ONLY if trigger.get("external_trigger_enabled") else CAEN_DGTZ_TRGMODE_DISABLED), "CAEN_DGTZ_SetExtTriggerInputMode")
        self._check(self.io_level_fn(self.handle, CAEN_DGTZ_IOLEVEL_NIM), "CAEN_DGTZ_SetIOLevel")
        self._check(self.max_blt_fn(self.handle, int(c.get("max_events_blt", 32))), "CAEN_DGTZ_SetMaxNumEventsBLT")
        self._check(self.malloc_buffer_fn(self.handle, ct.byref(self.buffer), ct.byref(self.buffer_size)), "CAEN_DGTZ_MallocReadoutBuffer")
        self._check(self.allocate_event_fn(self.handle, ct.byref(self.event_ptr)), "CAEN_DGTZ_AllocateEvent")
        self._check(self.clear_fn(self.handle), "CAEN_DGTZ_ClearData")

    def start(self) -> None:
        self._check(self.start_fn(self.handle), "CAEN_DGTZ_SWStartAcquisition")
        self.is_running = True

    def read_events(self) -> list[Event]:
        actual = ct.c_uint32()
        self._check(self.read_fn(self.handle, CAEN_DGTZ_SLAVE_TERMINATED_READOUT_MBLT, self.buffer, ct.byref(actual)), "CAEN_DGTZ_ReadData")
        if actual.value == 0:
            return []
        count = ct.c_uint32()
        self._check(self.num_events_fn(self.handle, self.buffer, actual, ct.byref(count)), "CAEN_DGTZ_GetNumEvents")
        result: list[Event] = []
        for index in range(count.value):
            info, raw_event = EventInfo(), ct.c_char_p()
            self._check(self.event_info_fn(self.handle, self.buffer, actual, index, ct.byref(info), ct.byref(raw_event)), "CAEN_DGTZ_GetEventInfo")
            self._check(self.decode_fn(self.handle, raw_event, ct.byref(self.event_ptr)), "CAEN_DGTZ_DecodeEvent")
            decoded = ct.cast(self.event_ptr, ct.POINTER(Uint16Event)).contents
            size = int(decoded.ChSize[0])
            if size and decoded.DataChannel[0]:
                samples = np.ctypeslib.as_array(decoded.DataChannel[0], shape=(size,)).copy()
                timetag = int(info.TriggerTimeTag)
                if timetag < self._last_timetag:
                    self._timestamp_epoch += 1 << 32
                self._last_timetag = timetag
                result.append(Event(self._event_id, 0, self._timestamp_epoch + timetag, samples, 1, self._polarity))
                self._event_id += 1
        return result

    def stop(self) -> None:
        if self.is_running:
            self._check(self.stop_fn(self.handle), "CAEN_DGTZ_SWStopAcquisition")
            self.is_running = False

    def close(self) -> None:
        errors: list[Exception] = []
        try:
            self.stop()
        except Exception as exc:
            errors.append(exc)
        if self.event_ptr.value:
            code = self.free_event_fn(self.handle, ct.byref(self.event_ptr))
            if code != 0: errors.append(CAENError(f"CAEN_DGTZ_FreeEvent: {code}"))
        if self.buffer.value:
            code = self.free_buffer_fn(ct.byref(self.buffer))
            if code != 0: errors.append(CAENError(f"CAEN_DGTZ_FreeReadoutBuffer: {code}"))
        if self.is_open:
            code = self.close_fn(self.handle)
            self.is_open = False
            if code != 0: errors.append(CAENError(f"CAEN_DGTZ_CloseDigitizer: {code}"))
        for error in errors:
            LOG.error("Ошибка закрытия CAEN: %s", error)
