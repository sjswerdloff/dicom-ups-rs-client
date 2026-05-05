"""Asynchronous operation wrappers mixin for DICOM UPS-RS Client."""

import asyncio
from typing import Any

from dicom_ups_rs_client.enums import UPSState


class AsyncOperationsMixin:
    """
    Mixin providing asynchronous wrappers for UPS-RS core operations.

    Each method is a thin async wrapper that submits the corresponding
    synchronous method to ``self.executor`` via ``loop.run_in_executor``.
    """

    async def create_workitem_async(
        self,
        workitem_data: dict[str, Any] | None = None,
        workitem_uid: str | None = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Asynchronously create a new workitem on the UPS-RS server.

        Args:
            workitem_data: dictionary containing the workitem dataset
            workitem_uid: Optional UID for the workitem

        Returns:
            tuple containing success status and either the response data or error message

        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, lambda: self.create_workitem(workitem_data, workitem_uid))  # type: ignore[attr-defined]

    async def retrieve_workitem_async(self, workitem_uid: str) -> tuple[bool, dict[str, Any] | str]:
        """
        Asynchronously retrieve a workitem from the UPS-RS server.

        Args:
            workitem_uid: UID of the workitem to retrieve

        Returns:
            tuple containing success status and either the response data or error message

        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, lambda: self.retrieve_workitem(workitem_uid))  # type: ignore[attr-defined]

    async def search_workitems_async(
        self,
        match_parameters: dict[str, str],
        include_fields: list[str] | None = None,
        fuzzy_matching: bool = False,
        offset: int = 0,
        limit: int | None = None,
        no_cache: bool = False,
    ) -> tuple[bool, list[dict[str, Any]] | str]:
        """
        Asynchronously search for workitems on the UPS-RS server.

        Args:
            match_parameters: dictionary of attribute/value pairs to match
            include_fields: Optional list of additional fields to include in results
            fuzzy_matching: Whether to use fuzzy matching (default: False)
            offset: Starting position of results (default: 0)
            limit: Maximum number of results to return (default: None)
            no_cache: Whether to request non-cached results (default: False)

        Returns:
            tuple containing success status and either the response data or error message

        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,  # type: ignore[attr-defined]
            lambda: self.search_workitems(match_parameters, include_fields, fuzzy_matching, offset, limit, no_cache),  # type: ignore[attr-defined]
        )

    async def update_workitem_async(
        self,
        workitem_uid: str,
        transaction_uid: str | None,
        update_data: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Asynchronously update a workitem on the UPS-RS server.

        Args:
            workitem_uid: UID of the workitem to update
            transaction_uid: Transaction UID for the update
            update_data: dictionary containing the workitem attributes to update

        Returns:
            tuple containing success status and either the response data or error message

        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,  # type: ignore[attr-defined]
            lambda: self.update_workitem(workitem_uid, transaction_uid, update_data),  # type: ignore[attr-defined]
        )

    async def change_workitem_state_async(
        self,
        workitem_uid: str,
        new_state: str | UPSState,
        transaction_uid: str | None = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Asynchronously change the state of a workitem on the UPS-RS server.

        Args:
            workitem_uid: UID of the workitem to update
            new_state: New state for the workitem
            transaction_uid: Transaction UID (required for state changes)

        Returns:
            tuple containing success status and either the response data or error message

        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,  # type: ignore[attr-defined]
            lambda: self.change_workitem_state(workitem_uid, new_state, transaction_uid),  # type: ignore[attr-defined]
        )

    async def request_cancellation_async(
        self,
        workitem_uid: str,
        reason: str | None = None,
        contact_name: str | None = None,
        contact_uri: str | None = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Asynchronously request cancellation of a workitem on the UPS-RS server.

        Args:
            workitem_uid: UID of the workitem to request cancellation for
            reason: Optional reason for the cancellation request
            contact_name: Optional display name of the contact person
            contact_uri: Optional URI for contacting the requestor

        Returns:
            tuple containing success status and either the response data or error message

        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,  # type: ignore[attr-defined]
            lambda: self.request_cancellation(workitem_uid, reason, contact_name, contact_uri),  # type: ignore[attr-defined]
        )
