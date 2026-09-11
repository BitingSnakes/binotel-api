"""Enumerations used by Binotel API responses."""

from enum import StrEnum


class Disposition(StrEnum):
    """Describe the final or current disposition of a call."""

    ANSWER = "ANSWER"
    TRANSFER = "TRANSFER"
    ONLINE = "ONLINE"
    CALLING = "CALLING"
    BUSY = "BUSY"
    NOANSWER = "NOANSWER"
    CANCEL = "CANCEL"
    CONGESTION = "CONGESTION"
    CHANUNAVAIL = "CHANUNAVAIL"
    VM = "VM"
    VM_SUCCESS = "VM-SUCCESS"
    SMS_SENDING = "SMS-SENDING"
    SMS_SUCCESS = "SMS-SUCCESS"
    SMS_FAILED = "SMS-FAILED"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
