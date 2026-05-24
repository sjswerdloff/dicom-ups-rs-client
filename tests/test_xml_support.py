"""
Tests for XML content-type support (application/dicom+xml).

These tests verify the XML path through UPSRSClient without changing the
existing JSON tests. All mocking happens at the HTTP session layer.

Coverage plan:
- _make_headers: XML and JSON modes
- serialize_request: JSON dict → XML bytes, Dataset → XML bytes, JSON fallback
- parse_response_body: XML single, multipart XML, JSON fallback
- extract_boundary: normal, quoted, missing
- _parse_multipart_xml: happy path, no boundary, malformed part
- UPSRSClient (XML mode):
  - create_workitem sends XML body with correct Content-Type
  - retrieve_workitem sends correct Accept header
  - search_workitems sends correct Accept header
  - update_workitem sends XML body
  - change_workitem_state sends XML body
  - request_cancellation sends XML body
- Response parsing dispatches on Content-Type header
- Error responses always parsed as JSON
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from pydicom import Dataset
from pydicom_xml import from_xml, to_xml
from requests.structures import CaseInsensitiveDict

from dicom_ups_rs_client.serialization import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_XML,
    _parse_multipart_xml,
    extract_boundary,
    make_headers,
    parse_response_body,
    serialize_request,
)
from dicom_ups_rs_client.ups_rs_client import UPSRSClient
from tests.conftest import MockSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_JSON: dict[str, Any] = {
    "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    "00404041": {"vr": "CS", "Value": ["READY"]},
}


def _sample_dataset() -> Dataset:
    return Dataset.from_json(json.dumps(_SAMPLE_JSON))


def _sample_xml() -> bytes:
    return to_xml(_sample_dataset())


# ---------------------------------------------------------------------------
# XML-specific mock response and fixtures
# ---------------------------------------------------------------------------


class XmlMockResponse:
    """Mock for requests.Response that carries XML content."""

    def __init__(
        self,
        status_code: int = 200,
        xml_bytes: bytes = b"",
        content_type: str = CONTENT_TYPE_XML,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """Initialize a mock XML response."""
        self.status_code = status_code
        self.content = xml_bytes
        self.text = xml_bytes.decode("utf-8", errors="replace") if xml_bytes else ""
        base_headers: dict[str, str] = {"Content-Type": content_type}
        if extra_headers:
            base_headers.update(extra_headers)
        self.headers = CaseInsensitiveDict(base_headers)
        self.reason = ""
        self.url = "http://example.com/mock"
        self.encoding = "utf-8"
        self.elapsed = timedelta(milliseconds=100)
        self.cookies: dict[str, str] = {}
        self.history: list[Any] = []
        self.request = None
        self._json_data: dict[str, Any] | None = None

    def json(self) -> dict[str, Any]:
        """Return JSON data (not typically called for XML responses)."""
        if self._json_data is None:
            raise ValueError("No JSON data; this is an XML response")
        return self._json_data

    @property
    def ok(self) -> bool:
        """Return True if status code is 2xx."""
        return 200 <= self.status_code < 400


def _build_multipart_xml_body(datasets: list[Dataset], boundary: str) -> bytes:
    """Build a multipart/related body with DICOM XML parts."""
    parts = b""
    for ds in datasets:
        xml = to_xml(ds)
        parts += (f"--{boundary}\r\nContent-Type: {CONTENT_TYPE_XML}\r\n\r\n").encode() + xml + b"\r\n"
    parts += f"--{boundary}--".encode()
    return parts


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session_xml() -> MockSession:
    """Provide a fresh MockSession for XML tests."""
    return MockSession()


@pytest.fixture
def mock_ups_rs_xml_client(mock_session_xml: object) -> UPSRSClient:
    """Provide a UPS-RS client configured for XML content type."""
    with patch("requests.Session", return_value=mock_session_xml):
        client = UPSRSClient(
            base_url="http://example.com/dicom-web",
            aetitle="TEST_AE",
            timeout=30,
            max_retries=0,  # No retries in tests
            retry_delay=0,
            content_type=CONTENT_TYPE_XML,
        )
        client.session = mock_session_xml  # type: ignore[assignment]
        return client


@pytest.fixture
def xml_response_factory() -> Callable[..., XmlMockResponse]:
    """Create mock XML responses."""

    def _create(
        status_code: int = 200,
        xml_bytes: bytes = b"",
        content_type: str = CONTENT_TYPE_XML,
        extra_headers: dict[str, str] | None = None,
    ) -> XmlMockResponse:
        return XmlMockResponse(
            status_code=status_code,
            xml_bytes=xml_bytes,
            content_type=content_type,
            extra_headers=extra_headers,
        )

    return _create


@pytest.fixture
def multipart_xml_response_factory() -> Callable[..., XmlMockResponse]:
    """Create mock multipart/related XML responses."""

    def _create(datasets: list[Dataset], boundary: str = "DicomBoundary") -> XmlMockResponse:
        body = _build_multipart_xml_body(datasets, boundary)
        ct = f'multipart/related; type="{CONTENT_TYPE_XML}"; boundary={boundary}'
        return XmlMockResponse(status_code=200, xml_bytes=body, content_type=ct)

    return _create


# ---------------------------------------------------------------------------
# make_headers
# ---------------------------------------------------------------------------


class TestMakeHeaders:
    """Tests for the make_headers helper."""

    def test_json_content_type(self) -> None:
        """Contract: JSON content type sets both Content-Type and Accept."""
        headers = make_headers(CONTENT_TYPE_JSON)
        assert headers["Content-Type"] == CONTENT_TYPE_JSON
        assert headers["Accept"] == CONTENT_TYPE_JSON

    def test_xml_content_type(self) -> None:
        """Contract: XML content type sets both Content-Type and Accept."""
        headers = make_headers(CONTENT_TYPE_XML)
        assert headers["Content-Type"] == CONTENT_TYPE_XML
        assert headers["Accept"] == CONTENT_TYPE_XML

    def test_extra_headers_merged(self) -> None:
        """Contract: extra headers are merged into the result."""
        headers = make_headers(CONTENT_TYPE_JSON, extra={"Cache-Control": "no-cache"})
        assert headers["Cache-Control"] == "no-cache"
        assert headers["Content-Type"] == CONTENT_TYPE_JSON

    def test_extra_headers_cannot_override_content_type(self) -> None:
        """Contract: extra dict cannot override Content-Type or Accept."""
        headers = make_headers(CONTENT_TYPE_JSON, extra={"Content-Type": "text/plain", "Accept": "text/html"})
        # Protected headers are filtered out — content_type always wins
        assert headers["Content-Type"] == CONTENT_TYPE_JSON
        assert headers["Accept"] == CONTENT_TYPE_JSON


# ---------------------------------------------------------------------------
# serialize_request
# ---------------------------------------------------------------------------


class TestSerializeRequest:
    """Tests for the serialize_request helper."""

    def test_json_serializes_dict(self) -> None:
        """Contract: JSON mode encodes dict as UTF-8 JSON bytes."""
        result = serialize_request(_SAMPLE_JSON, CONTENT_TYPE_JSON)
        assert isinstance(result, bytes)
        parsed = json.loads(result)
        assert parsed == _SAMPLE_JSON

    def test_xml_serializes_dict_to_xml_bytes(self) -> None:
        """Contract: XML mode converts dict to DICOM NativeDicomModel XML bytes."""
        result = serialize_request(_SAMPLE_JSON, CONTENT_TYPE_XML)
        assert isinstance(result, bytes)
        assert b"NativeDicomModel" in result
        # Round-trip: parse back to Dataset and verify
        ds = from_xml(result)
        assert "ProcedureStepState" in ds
        assert ds.ProcedureStepState == "SCHEDULED"

    def test_xml_accepts_dataset_directly(self) -> None:
        """Contract: XML mode accepts a pydicom Dataset directly."""
        ds = _sample_dataset()
        result = serialize_request(ds, CONTENT_TYPE_XML)
        assert isinstance(result, bytes)
        assert b"NativeDicomModel" in result

    def test_xml_serializes_empty_dict(self) -> None:
        """Contract: XML mode serializes empty dict to valid (empty) XML."""
        result = serialize_request({}, CONTENT_TYPE_XML)
        assert isinstance(result, bytes)
        assert b"NativeDicomModel" in result

    def test_json_serializes_empty_dict(self) -> None:
        """Contract: JSON mode serializes empty dict to '{}' bytes."""
        result = serialize_request({}, CONTENT_TYPE_JSON)
        assert result == b"{}"


# ---------------------------------------------------------------------------
# extract_boundary
# ---------------------------------------------------------------------------


class TestExtractBoundary:
    """Tests for the extract_boundary helper."""

    def test_extracts_unquoted_boundary(self) -> None:
        """Contract: extracts boundary without quotes."""
        ct = 'multipart/related; type="application/dicom+xml"; boundary=abc123'
        assert extract_boundary(ct) == "abc123"

    def test_extracts_quoted_boundary(self) -> None:
        """Contract: extracts boundary with surrounding quotes."""
        ct = 'multipart/related; boundary="my--boundary"'
        assert extract_boundary(ct) == "my--boundary"

    def test_returns_none_when_no_boundary(self) -> None:
        """Contract: returns None when boundary param is absent."""
        ct = "multipart/related; type=application/dicom+xml"
        assert extract_boundary(ct) is None

    def test_case_insensitive(self) -> None:
        """Contract: boundary parameter name is case-insensitive."""
        ct = "multipart/related; BOUNDARY=xyz"
        assert extract_boundary(ct) == "xyz"


# ---------------------------------------------------------------------------
# parse_response_body
# ---------------------------------------------------------------------------


class TestParseResponseBody:
    """Tests for the parse_response_body dispatcher."""

    def test_json_content_type_returns_dict(self) -> None:
        """Contract: application/dicom+json returns a dict."""
        data = {"foo": "bar"}
        result = parse_response_body(json.dumps(data).encode(), json.dumps(data), "application/dicom+json")
        assert result == data

    def test_json_content_type_returns_list(self) -> None:
        """Contract: application/dicom+json can return a list."""
        data = [{"foo": "bar"}, {"baz": 42}]
        result = parse_response_body(json.dumps(data).encode(), json.dumps(data), "application/dicom+json")
        assert result == data

    def test_xml_content_type_returns_dataset(self) -> None:
        """Contract: application/dicom+xml returns a pydicom Dataset."""
        xml = _sample_xml()
        result = parse_response_body(xml, xml.decode(), "application/dicom+xml; charset=utf-8")
        assert isinstance(result, Dataset)
        assert result.ProcedureStepState == "SCHEDULED"

    def test_multipart_xml_returns_list_of_datasets(self) -> None:
        """Contract: multipart/related returns a list of pydicom Datasets."""
        xml = _sample_xml()
        boundary = "MyBoundary"
        multipart = (
            (f"--{boundary}\r\nContent-Type: {CONTENT_TYPE_XML}\r\n\r\n").encode() + xml + (f"\r\n--{boundary}--").encode()
        )

        ct = f'multipart/related; type="{CONTENT_TYPE_XML}"; boundary={boundary}'
        result = parse_response_body(multipart, multipart.decode(errors="replace"), ct)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], Dataset)

    def test_no_content_type_falls_back_to_json(self) -> None:
        """Contract: empty Content-Type string falls back to JSON parsing."""
        data = {"key": "value"}
        result = parse_response_body(json.dumps(data).encode(), json.dumps(data), "")
        assert result == data


# ---------------------------------------------------------------------------
# _parse_multipart_xml
# ---------------------------------------------------------------------------


class TestParseMultipartXml:
    """Tests for the internal _parse_multipart_xml helper."""

    def test_parses_single_part(self) -> None:
        """Contract: correctly parses a single-part multipart body."""
        xml = _sample_xml()
        boundary = "TestBound"
        body = (
            (f"--{boundary}\r\nContent-Type: {CONTENT_TYPE_XML}\r\n\r\n").encode() + xml + f"\r\n--{boundary}--\r\n".encode()
        )

        result = _parse_multipart_xml(body, boundary)
        assert len(result) == 1
        assert isinstance(result[0], Dataset)

    def test_parses_multiple_parts(self) -> None:
        """Contract: correctly parses a multi-part multipart body."""
        xml = _sample_xml()
        boundary = "MultiPart"
        part = (f"--{boundary}\r\nContent-Type: {CONTENT_TYPE_XML}\r\n\r\n").encode() + xml + b"\r\n"
        body = part + part + f"--{boundary}--".encode()

        result = _parse_multipart_xml(body, boundary)
        assert len(result) == 2
        assert all(isinstance(ds, Dataset) for ds in result)

    def test_returns_empty_list_when_boundary_is_none(self) -> None:
        """Contract: returns empty list when boundary is None."""
        result = _parse_multipart_xml(b"some content", None)
        assert result == []

    def test_skips_malformed_parts(self) -> None:
        """Contract: malformed XML parts are skipped, not raised."""
        boundary = "SkipBad"
        bad_part = (f"--{boundary}\r\nContent-Type: {CONTENT_TYPE_XML}\r\n\r\nNOT XML AT ALL").encode()
        body = bad_part + f"\r\n--{boundary}--".encode()

        result = _parse_multipart_xml(body, boundary)
        assert result == []  # Bad part is skipped


# ---------------------------------------------------------------------------
# UPSRSClient XML mode — request headers and body
# ---------------------------------------------------------------------------


def _make_xml_client(mock_session: object) -> UPSRSClient:
    """Create a UPSRSClient configured for XML content type."""
    with patch("requests.Session", return_value=mock_session):
        client = UPSRSClient(
            base_url="http://example.com/dicom-web",
            aetitle="TEST_AE",
            content_type=CONTENT_TYPE_XML,
        )
        client.session = mock_session  # type: ignore[assignment]
        return client


class TestXmlClientCreateWorkitem:
    """Tests for create_workitem in XML mode."""

    def test_sends_xml_content_type_header(self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable) -> None:
        """Contract: XML mode sends application/dicom+xml Content-Type."""
        response = xml_response_factory(status_code=201, xml_bytes=_sample_xml())
        mock_ups_rs_xml_client.session.add_response("POST", r"http://example.com/dicom-web/workitems", response)

        mock_ups_rs_xml_client.create_workitem(_SAMPLE_JSON)

        assert len(mock_ups_rs_xml_client.session.requests) == 1
        req = mock_ups_rs_xml_client.session.requests[0]
        assert req["headers"]["Content-Type"] == CONTENT_TYPE_XML
        assert req["headers"]["Accept"] == CONTENT_TYPE_XML

    def test_sends_xml_body(self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable) -> None:
        """Contract: XML mode sends serialized XML bytes as body."""
        response = xml_response_factory(status_code=201, xml_bytes=_sample_xml())
        mock_ups_rs_xml_client.session.add_response("POST", r"http://example.com/dicom-web/workitems", response)

        mock_ups_rs_xml_client.create_workitem(_SAMPLE_JSON)

        req = mock_ups_rs_xml_client.session.requests[0]
        # The body is sent via 'data' (bytes) not 'json' (dict)
        assert req.get("json") is None
        body = req.get("data", b"")
        assert isinstance(body, bytes)
        assert b"NativeDicomModel" in body

    def test_success_response_parsed(self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable) -> None:
        """Contract: XML response body is parsed and returned as dict."""
        response = xml_response_factory(status_code=201, xml_bytes=_sample_xml())
        mock_ups_rs_xml_client.session.add_response("POST", r"http://example.com/dicom-web/workitems", response)

        success, result = mock_ups_rs_xml_client.create_workitem(_SAMPLE_JSON)

        assert success is True
        # Result should contain the DICOM attribute data
        assert isinstance(result, dict)


class TestXmlClientRetrieveWorkitem:
    """Tests for retrieve_workitem in XML mode."""

    def test_sends_xml_accept_header(self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable) -> None:
        """Contract: XML mode sends application/dicom+xml Accept header."""
        uid = "1.2.3.4.5"
        response = xml_response_factory(status_code=200, xml_bytes=_sample_xml())
        mock_ups_rs_xml_client.session.add_response("GET", rf"http://example.com/dicom-web/workitems/{uid}", response)

        mock_ups_rs_xml_client.retrieve_workitem(uid)

        req = mock_ups_rs_xml_client.session.requests[0]
        assert req["headers"]["Accept"] == CONTENT_TYPE_XML
        # GET requests should not send Content-Type
        assert "Content-Type" not in req["headers"]

    def test_xml_response_parsed_to_dict(self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable) -> None:
        """Contract: XML response is parsed and returned as dict."""
        uid = "1.2.3.4.5"
        response = xml_response_factory(status_code=200, xml_bytes=_sample_xml())
        mock_ups_rs_xml_client.session.add_response("GET", rf"http://example.com/dicom-web/workitems/{uid}", response)

        success, result = mock_ups_rs_xml_client.retrieve_workitem(uid)

        assert success is True
        assert isinstance(result, dict)
        # Should contain the DICOM tag data from the XML
        assert "00741000" in result


class TestXmlClientSearchWorkitems:
    """Tests for search_workitems in XML mode."""

    def test_sends_xml_accept_header(
        self, mock_ups_rs_xml_client: UPSRSClient, multipart_xml_response_factory: Callable
    ) -> None:
        """Contract: XML mode sends application/dicom+xml Accept header for search."""
        boundary = "SearchBoundary"
        response = multipart_xml_response_factory(datasets=[_sample_dataset()], boundary=boundary)
        mock_ups_rs_xml_client.session.add_response("GET", r"http://example.com/dicom-web/workitems\?", response)

        mock_ups_rs_xml_client.search_workitems({})

        req = mock_ups_rs_xml_client.session.requests[0]
        assert req["headers"]["Accept"] == CONTENT_TYPE_XML
        assert "Content-Type" not in req["headers"]

    def test_multipart_xml_response_parsed_to_list(
        self, mock_ups_rs_xml_client: UPSRSClient, multipart_xml_response_factory: Callable
    ) -> None:
        """Contract: multipart/related XML response parsed to list of dicts."""
        boundary = "MultiSearchBound"
        response = multipart_xml_response_factory(datasets=[_sample_dataset(), _sample_dataset()], boundary=boundary)
        mock_ups_rs_xml_client.session.add_response("GET", r"http://example.com/dicom-web/workitems\?", response)

        success, result = mock_ups_rs_xml_client.search_workitems({})

        assert success is True
        assert isinstance(result, list)
        assert len(result) == 2


class TestXmlClientUpdateWorkitem:
    """Tests for update_workitem in XML mode."""

    def test_sends_xml_body(self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable) -> None:
        """Contract: XML mode sends serialized XML bytes for update."""
        uid = "1.2.3.4.5"
        txn_uid = "5.6.7.8.9"
        response = xml_response_factory(status_code=200, xml_bytes=b"")
        response._json_data = {"status": "OK"}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_client.session.add_response(
            "POST", rf"http://example.com/dicom-web/workitems/{uid}\?Transaction-uid={txn_uid}", response
        )

        update_data = {"00741204": {"vr": "LO", "Value": ["Updated"]}}
        mock_ups_rs_xml_client.update_workitem(uid, txn_uid, update_data)

        req = mock_ups_rs_xml_client.session.requests[0]
        assert req["headers"]["Content-Type"] == CONTENT_TYPE_XML
        body = req.get("data", b"")
        assert isinstance(body, bytes)
        assert b"NativeDicomModel" in body


class TestXmlClientChangeWorkitemState:
    """Tests for change_workitem_state in XML mode."""

    def test_sends_xml_body_with_state_tag(self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable) -> None:
        """Contract: state change payload serialized to XML with correct DICOM tag."""
        uid = "1.2.3.4.5"
        txn_uid = "9.8.7.6.5"
        response = xml_response_factory(status_code=200)
        response._json_data = {"status": "OK"}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_client.session.add_response("PUT", rf"http://example.com/dicom-web/workitems/{uid}/state", response)

        mock_ups_rs_xml_client.change_workitem_state(uid, "COMPLETED", transaction_uid=txn_uid)

        req = mock_ups_rs_xml_client.session.requests[0]
        body = req.get("data", b"")
        assert isinstance(body, bytes)
        assert b"NativeDicomModel" in body
        # Verify the state value is present in the XML
        assert b"COMPLETED" in body


class TestXmlClientRequestCancellation:
    """Tests for request_cancellation in XML mode."""

    def test_sends_xml_body(self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable) -> None:
        """Contract: cancellation request serialized to XML."""
        uid = "1.2.3.4.5"
        response = xml_response_factory(status_code=202)
        response._json_data = {"status": "Accepted"}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_client.session.add_response(
            "POST", rf"http://example.com/dicom-web/workitems/{uid}/cancelrequest", response
        )

        mock_ups_rs_xml_client.request_cancellation(uid, reason="Testing", contact_name="Cora")

        req = mock_ups_rs_xml_client.session.requests[0]
        assert req["headers"]["Content-Type"] == CONTENT_TYPE_XML
        body = req.get("data", b"")
        assert isinstance(body, bytes)
        assert b"NativeDicomModel" in body
        assert b"Testing" in body


# ---------------------------------------------------------------------------
# Default JSON client still works (regression guard)
# ---------------------------------------------------------------------------


class TestJsonClientUnchanged:
    """Regression guard: default JSON client uses JSON everywhere."""

    def test_create_workitem_json_mode(
        self, mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: Callable
    ) -> None:
        """Contract: default JSON client sends json=dict, not data=bytes."""
        response = response_factory(
            status_code=201, json_data={"status": "Success"}, headers={"Content-Location": "/workitems/1.2.3"}
        )
        mock_ups_rs_client.session.add_response("POST", r"http://example.com/dicom-web/workitems", response)

        success, result = mock_ups_rs_client.create_workitem(sample_workitem)

        assert success is True
        req = mock_ups_rs_client.session.requests[0]
        assert req["headers"]["Content-Type"] == CONTENT_TYPE_JSON
        assert req.get("json") == sample_workitem
        assert req.get("data") is None


# ---------------------------------------------------------------------------
# Error response always parsed as JSON
# ---------------------------------------------------------------------------


class TestErrorResponseParsing:
    """Tests that error responses use JSON parsing regardless of content type."""

    def test_xml_client_parses_json_error_response(
        self, mock_ups_rs_xml_client: UPSRSClient, response_factory: Callable
    ) -> None:
        """Contract: error bodies are parsed as JSON even in XML mode."""
        from tests.conftest import MockResponse

        error_resp = MockResponse(
            status_code=400,
            json_data={"error": "Bad request"},
            headers={"Content-Type": "application/json"},
        )
        mock_ups_rs_xml_client.session.add_response("POST", r"http://example.com/dicom-web/workitems", error_resp)

        success, result = mock_ups_rs_xml_client.create_workitem(_SAMPLE_JSON)

        assert success is False
        assert isinstance(result, str)
        assert "400" in result


# ---------------------------------------------------------------------------
# Content type stored on client
# ---------------------------------------------------------------------------


class TestContentTypeConfiguration:
    """Tests for content_type parameter on UPSRSClient."""

    def test_default_content_type_is_json(self) -> None:
        """Contract: default content_type is application/dicom+json."""
        with patch("requests.Session"):
            client = UPSRSClient(base_url="http://example.com")
            assert client.content_type == CONTENT_TYPE_JSON

    def test_xml_content_type_stored(self) -> None:
        """Contract: XML content_type is stored correctly."""
        with patch("requests.Session"):
            client = UPSRSClient(base_url="http://example.com", content_type=CONTENT_TYPE_XML)
            assert client.content_type == CONTENT_TYPE_XML

    def test_make_headers_uses_stored_content_type(self) -> None:
        """Contract: _make_headers uses self.content_type."""
        with patch("requests.Session"):
            client = UPSRSClient(base_url="http://example.com", content_type=CONTENT_TYPE_XML)
            headers = client._make_headers()
            assert headers["Content-Type"] == CONTENT_TYPE_XML
            assert headers["Accept"] == CONTENT_TYPE_XML


# ---------------------------------------------------------------------------
# XML round-trip regression coverage for PS3.18 wire-form correctness
# ---------------------------------------------------------------------------


class TestXmlClientUpdateUrlShape:
    """Contracts for the URL form used by update_workitem in XML mode."""

    def test_transaction_uid_is_query_param_with_canonical_case(
        self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable
    ) -> None:
        """PS3.18 §11.6.1: Transaction UID is the 'Transaction-uid' query parameter."""
        uid = "1.2.3.4.5"
        txn_uid = "5.6.7.8.9"
        response = xml_response_factory(status_code=200)
        response._json_data = {"status": "OK"}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_client.session.add_response(
            "POST", rf"http://example.com/dicom-web/workitems/{uid}\?Transaction-uid={txn_uid}", response
        )

        mock_ups_rs_xml_client.update_workitem(uid, txn_uid, {"00741204": {"vr": "LO", "Value": ["x"]}})

        req = mock_ups_rs_xml_client.session.requests[0]
        assert req["method"] == "POST"
        assert f"?Transaction-uid={txn_uid}" in req["url"]
        # Standard flavor: txn UID is in the URL, not the body
        assert b"00081195" not in req.get("data", b"")


class TestXmlClientChangeStateAllStates:
    """Cover IN PROGRESS / COMPLETED / CANCELED state transitions in XML mode."""

    @pytest.mark.parametrize("state", ["IN PROGRESS", "COMPLETED", "CANCELED"])
    def test_state_value_serialized_in_xml_body(
        self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable, state: str
    ) -> None:
        """Each terminal state lands in the XML body as a CS attribute with Transaction UID."""
        uid = "1.2.3.4.5"
        txn_uid = "9.8.7.6.5"
        response = xml_response_factory(status_code=200)
        response._json_data = {"status": "OK"}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_client.session.add_response("PUT", rf"http://example.com/dicom-web/workitems/{uid}/state", response)

        mock_ups_rs_xml_client.change_workitem_state(uid, state, transaction_uid=txn_uid)

        req = mock_ups_rs_xml_client.session.requests[0]
        body = req.get("data", b"")
        assert isinstance(body, bytes)
        assert b"NativeDicomModel" in body
        assert state.encode() in body
        # Transaction UID (0008,1195) must accompany every state change
        assert b"00081195" in body or txn_uid.encode() in body

    def test_requester_aetitle_in_url_query(self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable) -> None:
        """PS3.18 §11.7.1: requester AE Title is the 'requester' query parameter (standard flavor)."""
        uid = "1.2.3.4.5"
        txn_uid = "9.8.7.6.5"
        response = xml_response_factory(status_code=200)
        response._json_data = {"status": "OK"}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_client.session.add_response("PUT", rf"http://example.com/dicom-web/workitems/{uid}/state", response)

        mock_ups_rs_xml_client.change_workitem_state(uid, "IN PROGRESS", transaction_uid=txn_uid)

        req = mock_ups_rs_xml_client.session.requests[0]
        assert "?requester=TEST_AE" in req["url"]


class TestXmlClientRequestCancellationUrlShape:
    """URL-shape contract for request_cancellation in XML mode (PS3.18 §11.8)."""

    def test_requester_aetitle_in_url_query(self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable) -> None:
        """PS3.18 §11.8: requester AE Title is the 'requester' query parameter (standard flavor)."""
        uid = "1.2.3.4.5"
        response = xml_response_factory(status_code=202)
        response._json_data = {"status": "Accepted"}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_client.session.add_response(
            "POST", rf"http://example.com/dicom-web/workitems/{uid}/cancelrequest", response
        )

        mock_ups_rs_xml_client.request_cancellation(uid, reason="end-to-end")

        req = mock_ups_rs_xml_client.session.requests[0]
        assert "?requester=TEST_AE" in req["url"]


class TestXmlClientSubscriptions:
    """Subscribe / unsubscribe URL contracts in XML mode."""

    def test_subscribe_to_worklist_url(self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable) -> None:
        """Global worklist subscribe POSTs to the well-known UID with the subscriber AET in the path."""
        response = xml_response_factory(status_code=201)
        response._json_data = {}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_client.session.add_response(
            "POST", r"http://example.com/dicom-web/workitems/1.2.840.10008.5.1.4.34.5/subscribers/TEST_AE", response
        )

        mock_ups_rs_xml_client.subscribe_to_worklist()

        req = mock_ups_rs_xml_client.session.requests[0]
        assert req["method"] == "POST"
        assert req["url"].endswith("/workitems/1.2.840.10008.5.1.4.34.5/subscribers/TEST_AE")

    def test_subscribe_to_filtered_worklist_encodes_filter(
        self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable
    ) -> None:
        """Filtered worklist subscribe includes filter as URL-encoded query parameter."""
        response = xml_response_factory(status_code=201)
        response._json_data = {}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_client.session.add_response(
            "POST",
            r"http://example.com/dicom-web/workitems/1.2.840.10008.5.1.4.34.5.1/subscribers/TEST_AE.*",
            response,
        )

        mock_ups_rs_xml_client.subscribe_to_filtered_worklist({"00741000": "SCHEDULED"})

        req = mock_ups_rs_xml_client.session.requests[0]
        # urlencode percent-encodes the '=' inside the DICOM filter syntax
        from urllib.parse import parse_qs, urlparse

        filter_value = parse_qs(urlparse(req["url"]).query)["filter"][0]
        assert filter_value == "00741000=SCHEDULED"

    def test_unsubscribe_from_worklist_uses_delete(
        self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable
    ) -> None:
        """Unsubscribe is HTTP DELETE on the subscriber resource."""
        response = xml_response_factory(status_code=200)
        response._json_data = {}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_client.session.add_response(
            "DELETE", r"http://example.com/dicom-web/workitems/1.2.840.10008.5.1.4.34.5/subscribers/TEST_AE", response
        )

        mock_ups_rs_xml_client.unsubscribe_from_worklist()

        req = mock_ups_rs_xml_client.session.requests[0]
        assert req["method"] == "DELETE"

    def test_subscribe_to_specific_workitem_url(
        self, mock_ups_rs_xml_client: UPSRSClient, xml_response_factory: Callable
    ) -> None:
        """Per-workitem subscribe POSTs to /workitems/{UID}/subscribers/{AET}."""
        uid = "1.2.3.4.5"
        response = xml_response_factory(status_code=201)
        response._json_data = {}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_client.session.add_response(
            "POST", rf"http://example.com/dicom-web/workitems/{uid}/subscribers/TEST_AE", response
        )

        mock_ups_rs_xml_client.subscribe_to_workitem(uid)

        req = mock_ups_rs_xml_client.session.requests[0]
        assert req["url"].endswith(f"/workitems/{uid}/subscribers/TEST_AE")


# ---------------------------------------------------------------------------
# dcm4chee-flavor URL/body shape regression (server_flavor="dcm4chee")
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ups_rs_xml_dcm4chee_client(mock_session_xml: object) -> UPSRSClient:
    """XML client configured for dcm4chee's non-conformant URL convention."""
    with patch("requests.Session", return_value=mock_session_xml):
        client = UPSRSClient(
            base_url="http://example.com/dicom-web",
            aetitle="TEST_AE",
            timeout=30,
            max_retries=0,
            retry_delay=0,
            content_type=CONTENT_TYPE_XML,
            server_flavor="dcm4chee",
        )
        client.session = mock_session_xml  # type: ignore[assignment]
        return client


class TestXmlClientDcm4cheeFlavor:
    """dcm4chee flavor: requester as path segment, Transaction UID in body for update."""

    def test_update_puts_transaction_uid_in_body_not_url(
        self, mock_ups_rs_xml_dcm4chee_client: UPSRSClient, xml_response_factory: Callable
    ) -> None:
        """dcm4chee-flavor update places Transaction UID (0008,1195) in the body, not the URL."""
        uid = "1.2.3.4.5"
        txn_uid = "5.6.7.8.9"
        response = xml_response_factory(status_code=200)
        response._json_data = {"status": "OK"}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_dcm4chee_client.session.add_response(
            "POST", rf"http://example.com/dicom-web/workitems/{uid}$", response
        )

        mock_ups_rs_xml_dcm4chee_client.update_workitem(uid, txn_uid, {"00741204": {"vr": "LO", "Value": ["x"]}})

        req = mock_ups_rs_xml_dcm4chee_client.session.requests[0]
        assert "Transaction-uid" not in req["url"]
        # Transaction UID tag must be in the serialized XML body
        body = req.get("data", b"")
        assert b"00081195" in body or txn_uid.encode() in body

    def test_change_state_puts_requester_in_path(
        self, mock_ups_rs_xml_dcm4chee_client: UPSRSClient, xml_response_factory: Callable
    ) -> None:
        """dcm4chee-flavor state change uses /state/{AET} path segment, not ?requester=."""
        uid = "1.2.3.4.5"
        txn_uid = "9.8.7.6.5"
        response = xml_response_factory(status_code=200)
        response._json_data = {"status": "OK"}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_dcm4chee_client.session.add_response(
            "PUT", rf"http://example.com/dicom-web/workitems/{uid}/state/TEST_AE", response
        )

        mock_ups_rs_xml_dcm4chee_client.change_workitem_state(uid, "IN PROGRESS", transaction_uid=txn_uid)

        req = mock_ups_rs_xml_dcm4chee_client.session.requests[0]
        assert req["url"].endswith("/state/TEST_AE")
        assert "?requester=" not in req["url"]

    def test_cancelrequest_puts_requester_in_path(
        self, mock_ups_rs_xml_dcm4chee_client: UPSRSClient, xml_response_factory: Callable
    ) -> None:
        """dcm4chee-flavor cancelrequest uses /cancelrequest/{AET} path segment."""
        uid = "1.2.3.4.5"
        response = xml_response_factory(status_code=202)
        response._json_data = {"status": "Accepted"}
        response.text = "{}"
        response.content = b"{}"
        response.headers["Content-Type"] = CONTENT_TYPE_JSON
        mock_ups_rs_xml_dcm4chee_client.session.add_response(
            "POST", rf"http://example.com/dicom-web/workitems/{uid}/cancelrequest/TEST_AE", response
        )

        mock_ups_rs_xml_dcm4chee_client.request_cancellation(uid, reason="x")

        req = mock_ups_rs_xml_dcm4chee_client.session.requests[0]
        assert req["url"].endswith("/cancelrequest/TEST_AE")
        assert "?requester=" not in req["url"]
