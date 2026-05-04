"""Tests for issue #17: KeyError guard on websocket_url_override formatting."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from dicom_ups_rs_client.ups_rs_client import UPSRSClient, UPSRSValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(**kwargs: object) -> UPSRSClient:
    """Create a UPSRSClient with mocked session, forwarding extra kwargs."""
    with patch("requests.Session"):
        return UPSRSClient(base_url="http://example.com/dicom-web", aetitle="TEST_AE", **kwargs)  # type: ignore[arg-type]


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
        """Contract: the error message identifies the problematic placeholder and template."""
        template = "wss://custom.example.com:8443/ws/{typo_key}/subscribers"
        client = _make_client(websocket_url_override=template)
        with pytest.raises(UPSRSValidationError, match="typo_key") as exc_info:
            self._subscribe_and_check(client)
        assert template in str(exc_info.value)

    def test_original_key_error_is_chained(self) -> None:
        """Contract: the UPSRSValidationError chains the original KeyError as __cause__."""
        client = _make_client(websocket_url_override="wss://custom.example.com/{bad}")
        with pytest.raises(UPSRSValidationError) as exc_info:
            self._subscribe_and_check(client)
        assert isinstance(exc_info.value.__cause__, KeyError)
