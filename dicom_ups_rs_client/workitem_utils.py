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
        Create a default workitem dataset with required attributes.

        Returns:
            dictionary containing a default workitem dataset

        """
        # Current time
        now = datetime.now()

        # Scheduled start and end times (start in 1 hour, end 2 hours after that)
        scheduled_start = (now + timedelta(hours=1)).strftime("%Y%m%d%H%M%S")
        scheduled_end = (now + timedelta(hours=3)).strftime("%Y%m%d%H%M%S")

        # Create a workitem with required attributes
        # This is a simplified example - in practice, you would include all required attributes
        # according to the DICOM standard (PS3.4 Table CC.2.5-3)
        workitem = {
            # Procedure Step State (0074,1000)
            "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
            # Input Readiness State (0040,4041)
            "00404041": {"vr": "CS", "Value": ["READY"]},
            # Scheduled Procedure Step Start DateTime (0040,4005)
            "00404005": {"vr": "DT", "Value": [scheduled_start]},
            # Scheduled Procedure Step End DateTime (0040,4011) - Optional but recommended
            "00404011": {"vr": "DT", "Value": [scheduled_end]},
            # Procedure Step Label (0074,1204)
            "00741204": {"vr": "LO", "Value": ["Example Procedure"]},
            # Workitem Type (0040,4000)
            "00404000": {"vr": "CS", "Value": ["IMAGE_PROCESSING"]},
            # Procedure Step Description (0040,0007)
            "00400007": {"vr": "LO", "Value": ["Example procedure step description"]},
        }

        return workitem
