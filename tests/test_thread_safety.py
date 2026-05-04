"""Tests for issue #2: thread-safe event callback invocation."""

import asyncio
import json
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest

from dicom_ups_rs_client.ups_rs_client import UPSRSClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_EVENT: dict[str, Any] = {
    "00001000": {"vr": "UI", "Value": ["1.2.3.4.5"]},
    "00001002": {"vr": "US", "Value": [1]},
    "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    "00404041": {"vr": "CS", "Value": ["READY"]},
}


def _make_client(**kwargs: object) -> UPSRSClient:
    """Create a UPSRSClient with mocked session, forwarding extra kwargs."""
    with patch("requests.Session"):
        return UPSRSClient(base_url="http://example.com/dicom-web", aetitle="TEST_AE", **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Issue #2 — Thread safety: _callback_lock exists and serialises callbacks
# ---------------------------------------------------------------------------


class TestEventCallbackThreadSafety:
    """Tests for issue #2: thread-safe event callback invocation."""

    def test_callback_lock_is_initialised_on_client(self) -> None:
        """Contract: every new client instance has a threading.Lock for callbacks."""
        client = _make_client()
        assert hasattr(client, "_callback_lock"), "Client must expose _callback_lock"
        assert isinstance(client._callback_lock, type(threading.Lock())), "_callback_lock must be a threading.Lock"

    @pytest.mark.asyncio
    async def test_callback_invoked_from_main_thread_uses_lock(self) -> None:
        """Contract: when _handle_message is called the callback runs inside the lock."""
        client = _make_client()

        lock_held_during_callback: list[bool] = []

        def callback(event_data: dict[str, Any]) -> None:
            # The lock is non-reentrant. If it is held by _handle_message, we
            # cannot acquire it — which is what we want to verify.
            acquired = client._callback_lock.acquire(blocking=False)
            # acquired==True means lock was NOT held (bad); False means it was held (good).
            lock_held_during_callback.append(not acquired)
            if acquired:
                client._callback_lock.release()

        client.event_callback = callback

        await client._handle_message(json.dumps(SAMPLE_EVENT))

        assert lock_held_during_callback == [True], "Lock must be held when event_callback is called"

    def test_callback_invoked_from_background_thread_uses_lock(self) -> None:
        """Contract: callback dispatched from a non-main thread is wrapped in the lock."""
        client = _make_client()
        call_count: list[int] = [0]
        errors: list[str] = []

        def callback(event_data: dict[str, Any]) -> None:
            # Verify lock is held (non-reentrant lock cannot be acquired if already held)
            acquired = client._callback_lock.acquire(blocking=False)
            if acquired:
                errors.append("Lock was NOT held during background callback invocation")
                client._callback_lock.release()
            call_count[0] += 1

        client.event_callback = callback

        # Simulate being called from a background thread
        def run_in_background() -> None:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(client._handle_message(json.dumps(SAMPLE_EVENT)))
            finally:
                loop.close()

        t = threading.Thread(target=run_in_background)
        t.start()
        t.join(timeout=3.0)

        assert call_count[0] == 1, "Callback must be invoked exactly once"
        assert errors == [], "\n".join(errors)

    def test_concurrent_callbacks_are_serialised(self) -> None:
        """Contract: simultaneous background-thread callbacks do not overlap."""
        client = _make_client()
        concurrent_count: list[int] = [0]
        max_concurrent: list[int] = [0]
        errors: list[str] = []

        def slow_callback(event_data: dict[str, Any]) -> None:
            concurrent_count[0] += 1
            if concurrent_count[0] > 1:
                errors.append(f"Concurrent invocations detected: {concurrent_count[0]}")
            max_concurrent[0] = max(max_concurrent[0], concurrent_count[0])
            time.sleep(0.05)  # hold for a moment to expose overlap
            concurrent_count[0] -= 1

        client.event_callback = slow_callback

        def run_one() -> None:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(client._handle_message(json.dumps(SAMPLE_EVENT)))
            finally:
                loop.close()

        threads = [threading.Thread(target=run_one, daemon=True) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert errors == [], "Callbacks must never overlap: " + "; ".join(errors)
        assert max_concurrent[0] == 1, "At most one callback should run at a time"

    def test_callback_lock_released_on_exception(self) -> None:
        """Contract: callback lock is released even when the callback raises."""
        client = _make_client()
        calls: list[int] = [0]

        def flaky_callback(event_data: dict[str, Any]) -> None:
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("boom")

        client.event_callback = flaky_callback

        def run_one() -> None:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(client._handle_message(json.dumps(SAMPLE_EVENT)))
            except RuntimeError:
                pass
            finally:
                loop.close()

        # First call raises, second should still execute (lock not stuck)
        t1 = threading.Thread(target=run_one, daemon=True)
        t1.start()
        t1.join(timeout=3.0)

        t2 = threading.Thread(target=run_one, daemon=True)
        t2.start()
        t2.join(timeout=3.0)

        assert not t2.is_alive(), "Second thread should not hang — callback lock may be stuck"
        assert calls[0] == 2, f"Expected 2 callback invocations, got {calls[0]}"

    @pytest.mark.asyncio
    async def test_no_callback_assigned_logs_warning(self) -> None:
        """Contract: when no event_callback is set a warning is logged, not an error."""
        client = _make_client()
        client.event_callback = None

        with patch.object(client.logger, "warning") as mock_warning:
            await client._handle_message(json.dumps(SAMPLE_EVENT))
            mock_warning.assert_called_once()
            assert "event_callback" in mock_warning.call_args[0][0].lower()
