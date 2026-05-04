"""Tests for issue #14: --client-cert-key requires --client-cert."""

import sys
from unittest.mock import MagicMock, Mock, patch

import pytest

from dicom_ups_rs_client.ups_rs_client import main  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Issue #14 — Require --client-cert when --client-cert-key is given
# ---------------------------------------------------------------------------


class TestClientCertKeyCLIValidation:
    """Tests for issue #14: --client-cert-key requires --client-cert."""

    def test_client_cert_key_without_client_cert_exits_with_error(self) -> None:
        """Contract: --client-cert-key without --client-cert causes a SystemExit(2)."""
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
                # Should not raise SystemExit at all — successful arg parsing
                try:
                    main()
                except SystemExit as exc:
                    if exc.code != 0:
                        pytest.fail(f"Unexpected SystemExit({exc.code}): solo --client-cert should be valid")

    def test_both_client_cert_and_key_together_are_accepted(self) -> None:
        """Contract: --client-cert together with --client-cert-key is valid."""
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
                    if exc.code != 0:
                        pytest.fail(f"Unexpected SystemExit({exc.code}): --client-cert + --client-cert-key should be valid")
