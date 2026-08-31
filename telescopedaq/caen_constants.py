"""ctypes declarations copied from installed CAENDigitizer 2.17 headers."""

from __future__ import annotations

import ctypes as ct

MAX_UINT16_CHANNEL_SIZE = 64
MAX_LICENSE_LENGTH = 17

CAEN_DGTZ_USB = 0
CAEN_DGTZ_TRGMODE_DISABLED = 0
CAEN_DGTZ_TRGMODE_ACQ_ONLY = 1
CAEN_DGTZ_SW_CONTROLLED = 0
CAEN_DGTZ_SLAVE_TERMINATED_READOUT_MBLT = 0
CAEN_DGTZ_IOLEVEL_NIM = 0
CAEN_DGTZ_IOLEVEL_TTL = 1
CAEN_DGTZ_TRIGGER_ON_RISING_EDGE = 0
CAEN_DGTZ_TRIGGER_ON_FALLING_EDGE = 1

ERROR_NAMES = {
    0: "Success", -1: "CommError", -2: "GenericError", -3: "InvalidParam",
    -4: "InvalidLinkType", -5: "InvalidHandle", -7: "BadBoardType",
    -13: "InvalidChannelNumber", -16: "WrongAcqMode",
    -17: "FunctionNotAllowed", -18: "Timeout", -19: "InvalidBuffer",
    -20: "EventNotFound", -21: "InvalidEvent", -22: "OutOfMemory",
    -24: "DigitizerNotFound", -25: "DigitizerAlreadyOpen",
    -26: "DigitizerNotReady", -29: "DPPFirmwareNotSupported",
    -99: "NotYetImplemented",
}


class BoardInfo(ct.Structure):
    _fields_ = [
        ("ModelName", ct.c_char * 12), ("Model", ct.c_uint32),
        ("Channels", ct.c_uint32), ("FormFactor", ct.c_uint32),
        ("FamilyCode", ct.c_uint32), ("ROC_FirmwareRel", ct.c_char * 20),
        ("AMC_FirmwareRel", ct.c_char * 40), ("SerialNumber", ct.c_uint32),
        ("MezzanineSerNum", (ct.c_char * 8) * 4), ("PCB_Revision", ct.c_uint32),
        ("ADC_NBits", ct.c_uint32), ("SAMCorrectionDataLoaded", ct.c_uint32),
        ("CommHandle", ct.c_int), ("VMEHandle", ct.c_int),
        ("License", ct.c_char * MAX_LICENSE_LENGTH),
    ]


class EventInfo(ct.Structure):
    _fields_ = [
        ("EventSize", ct.c_uint32), ("BoardId", ct.c_uint32),
        ("Pattern", ct.c_uint32), ("ChannelMask", ct.c_uint32),
        ("EventCounter", ct.c_uint32), ("TriggerTimeTag", ct.c_uint32),
    ]


class Uint16Event(ct.Structure):
    _fields_ = [
        ("ChSize", ct.c_uint32 * MAX_UINT16_CHANNEL_SIZE),
        ("DataChannel", ct.POINTER(ct.c_uint16) * MAX_UINT16_CHANNEL_SIZE),
    ]
