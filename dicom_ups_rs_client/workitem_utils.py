"""Workitem utility mixin for DICOM UPS-RS Client."""

from datetime import datetime, timedelta
from typing import Any

from pydicom import uid


class WorkitemUtilsMixin:
    """
    Mixin providing workitem utility helpers for UPSRSClient.

    This mixin contains stateless helpers for UID validation and
    default workitem dataset construction.
    """

    @staticmethod
    def validate_uid(uid_string: str) -> bool:
        """
        Validate that a string is a valid DICOM UID.

        Args:
            uid_string: The UID string to validate

        Returns:
            bool: True if valid, False otherwise

        """
        return uid.UID(uid_string).is_valid

    def _create_default_workitem(self) -> dict[str, Any]:
        """
        Create a default workitem dataset with the attributes a conformant SCP requires.

        Built to satisfy the create-workitem validation in dcm4chee-arc 5.x. SOP Class UID
        (0008,0016) is intentionally omitted because it is determined by the endpoint
        and not allowed in the request body.

        Returns:
            dictionary containing a default workitem dataset

        """
        now = datetime.now()
        scheduled_start = (now + timedelta(hours=1)).strftime("%Y%m%d%H%M%S")
        scheduled_end = (now + timedelta(hours=3)).strftime("%Y%m%d%H%M%S")

        return {
            # Patient identification (Type 2 — present, may be empty)
            "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Default^Workitem^Patient"}]},
            "00100020": {"vr": "LO", "Value": ["DEFAULT001"]},
            "00100030": {"vr": "DA", "Value": ["19700101"]},
            "00100040": {"vr": "CS", "Value": ["O"]},
            # Admission / referenced request (Type 2 — empty)
            "00380010": {"vr": "LO"},
            "00380014": {"vr": "SQ"},
            "0040A370": {"vr": "SQ"},
            # Scheduled timing
            "00404005": {"vr": "DT", "Value": [scheduled_start]},
            "00404011": {"vr": "DT", "Value": [scheduled_end]},
            # Scheduled Workitem Code Sequence (Type 1 — must have a code)
            "00404018": {
                "vr": "SQ",
                "Value": [
                    {
                        "00080100": {"vr": "SH", "Value": ["121726"]},
                        "00080102": {"vr": "SH", "Value": ["DCM"]},
                        "00080104": {"vr": "LO", "Value": ["RT Treatment with Internal Verification"]},
                    }
                ],
            },
            # Input information / scheduled performers (Type 2 — empty)
            "00404021": {"vr": "SQ"},
            "00404025": {"vr": "SQ"},
            "00404026": {"vr": "SQ"},
            "00404027": {"vr": "SQ"},
            "00404034": {"vr": "SQ"},
            # Input Readiness State (Type 1)
            "00404041": {"vr": "CS", "Value": ["READY"]},
            # Scheduled Processing Parameters Sequence (Type 1 — may be empty)
            "00740120": {"vr": "SQ"},
            # Procedure Step State (Type 1) — always SCHEDULED on create
            "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
            # Scheduled Procedure Step Priority (Type 1)
            "00741200": {"vr": "CS", "Value": ["MEDIUM"]},
            # Worklist Label (Type 2) and Procedure Step Label (Type 1)
            "00741202": {"vr": "LO", "Value": ["DEFAULT_WORKLIST"]},
            "00741204": {"vr": "LO", "Value": ["Default workitem"]},
        }
