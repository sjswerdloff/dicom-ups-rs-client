"""Enumerations for the DICOM UPS-RS client."""

from enum import Enum, auto


class UPSState(Enum):
    """UPS Procedure Step States as defined in DICOM."""

    SCHEDULED = auto()
    IN_PROGRESS = auto()
    CANCELED = auto()
    COMPLETED = auto()

    def __str__(self) -> str:
        """Return string representation for UPS-RS protocol."""
        return self.name.replace("_", " ")


class InputReadinessState(Enum):
    """UPS Input Readiness States as defined in DICOM."""

    READY = auto()
    UNAVAILABLE = auto()
    INCOMPLETE = auto()

    def __str__(self) -> str:
        """Return string representation for UPS-RS protocol."""
        return self.name
