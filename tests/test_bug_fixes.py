"""Tests for bug fixes: issues #2, #14, and #17."""

import json
import threading
import time
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from dicom_ups_rs_client.ups_rs_client import UPSRSClient, UPSRSValidationError

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
        import asyncio

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
        import asyncio

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

    @pytest.mark.asyncio
    async def test_no_callback_assigned_logs_warning(self) -> None:
        """Contract: when no event_callback is set a warning is logged, not an error."""
        client = _make_client()
        client.event_callback = None

        with patch.object(client.logger, "warning") as mock_warning:
            await client._handle_message(json.dumps(SAMPLE_EVENT))
            mock_warning.assert_called_once()
            assert "event_callback" in mock_warning.call_args[0][0].lower()


# ---------------------------------------------------------------------------
# Issue #17 — KeyError on bad websocket_url_override template
# ---------------------------------------------------------------------------


class TestWebSocketUrlOverrideTemplate:
    """Tests for issue #17: KeyError guard on websocket_url_override formatting."""

    def _subscribe_and_check(self, client: UPSRSClient) -> None:
        """Invoke _send_subscription_request with a mocked session that returns 201."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {}
        mock_response.headers = {"Content-Location": "ws://example.com/ws/subscribers/TEST_AE"}
        client.session.request = Mock(return_value=mock_response)
        client._send_subscription_request("http://example.com/workitems/subscribers/TEST_AE")

    def test_valid_aetitle_placeholder_is_expanded(self) -> None:
        """Contract: a template with {aetitle} is expanded to the actual AE title."""
        client = _make_client(websocket_url_override="wss://custom.example.com:8443/ws/subscribers/{aetitle}")
        self._subscribe_and_check(client)
        assert client.ws_url == "wss://custom.example.com:8443/ws/subscribers/TEST_AE"

    def test_template_without_placeholder_is_used_verbatim(self) -> None:
        """Contract: a template without any placeholder is used as-is without error."""
        client = _make_client(websocket_url_override="wss://fixed.example.com:8443/ws/subscribers/static")
        self._subscribe_and_check(client)
        assert client.ws_url == "wss://fixed.example.com:8443/ws/subscribers/static"

    def test_unknown_placeholder_raises_validation_error(self) -> None:
        """Contract: a template with an unsupported placeholder raises UPSRSValidationError."""
        client = _make_client(websocket_url_override="wss://custom.example.com:8443/ws/subscribers/{unknown_key}")
        with pytest.raises(UPSRSValidationError, match="unsupported placeholder"):
            self._subscribe_and_check(client)

    def test_validation_error_message_names_the_bad_key(self) -> None:
        """Contract: the error message identifies the problematic placeholder name."""
        client = _make_client(websocket_url_override="wss://custom.example.com:8443/ws/{typo_key}/subscribers")
        with pytest.raises(UPSRSValidationError, match="typo_key"):
            self._subscribe_and_check(client)

    def test_original_key_error_is_chained(self) -> None:
        """Contract: the UPSRSValidationError chains the original KeyError as __cause__."""
        client = _make_client(websocket_url_override="wss://custom.example.com/{bad}")
        with pytest.raises(UPSRSValidationError) as exc_info:
            self._subscribe_and_check(client)
        assert isinstance(exc_info.value.__cause__, KeyError)


# ---------------------------------------------------------------------------
# Issue #14 — Require --client-cert when --client-cert-key is given
# ---------------------------------------------------------------------------


class TestClientCertKeyCLIValidation:
    """Tests for issue #14: --client-cert-key requires --client-cert."""

    def test_client_cert_key_without_client_cert_exits_with_error(self) -> None:
        """Contract: --client-cert-key without --client-cert causes a SystemExit(2)."""
        import sys

        from dicom_ups_rs_client.ups_rs_client import main  # type: ignore[attr-defined]

        argv = [
            "--server",
            "http://example.com/dicom-web",
            "--client-cert-key",
            "/path/to/client.key",
            "search",
        ]
        with patch.object(sys, "argv", ["ups_rs_client"] + argv):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 2, "argparse should exit with code 2 for invalid argument combinations"

    def test_client_cert_key_without_client_cert_error_message_is_clear(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Contract: the error message mentions both --client-cert-key and --client-cert."""
        import sys

        from dicom_ups_rs_client.ups_rs_client import main  # type: ignore[attr-defined]

        argv = [
            "--server",
            "http://example.com/dicom-web",
            "--client-cert-key",
            "/path/to/client.key",
            "search",
        ]
        with patch.object(sys, "argv", ["ups_rs_client"] + argv):
            with pytest.raises(SystemExit):
                main()
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "client-cert-key" in combined or "client_cert_key" in combined

    def test_client_cert_alone_is_accepted(self) -> None:
        """Contract: --client-cert without --client-cert-key is valid (PEM file with both)."""
        import sys

        from dicom_ups_rs_client.ups_rs_client import main  # type: ignore[attr-defined]

        argv = [
            "--server",
            "http://example.com/dicom-web",
            "--client-cert",
            "/path/to/client.pem",
            "search",
        ]
        with patch.object(sys, "argv", ["ups_rs_client"] + argv):
            # The client will attempt to connect; mock out everything after arg parsing.
            with patch("dicom_ups_rs_client.ups_rs_client.UPSRSClient") as mock_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = Mock(return_value=mock_client)
                mock_client.__exit__ = Mock(return_value=False)
                mock_client.search_workitems.return_value = (True, [])
                mock_cls.return_value = mock_client
                # Should not raise SystemExit(2)
                try:
                    main()
                except SystemExit as exc:
                    assert exc.code != 2, "Unexpected SystemExit(2): solo --client-cert should be valid"

    def test_both_client_cert_and_key_together_are_accepted(self) -> None:
        """Contract: --client-cert together with --client-cert-key is valid."""
        import sys

        from dicom_ups_rs_client.ups_rs_client import main  # type: ignore[attr-defined]

        argv = [
            "--server",
            "http://example.com/dicom-web",
            "--client-cert",
            "/path/to/client.crt",
            "--client-cert-key",
            "/path/to/client.key",
            "search",
        ]
        with patch.object(sys, "argv", ["ups_rs_client"] + argv):
            with patch("dicom_ups_rs_client.ups_rs_client.UPSRSClient") as mock_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = Mock(return_value=mock_client)
                mock_client.__exit__ = Mock(return_value=False)
                mock_client.search_workitems.return_value = (True, [])
                mock_cls.return_value = mock_client
                try:
                    main()
                except SystemExit as exc:
                    assert exc.code != 2, "Unexpected SystemExit(2): --client-cert + --client-cert-key should be valid"
