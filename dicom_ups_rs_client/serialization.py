"""
Content-type negotiation, serialization, and response parsing for UPS-RS.

This module handles:
- Building HTTP headers for DICOM content-type negotiation.
- Serializing request bodies for both application/dicom+json and application/dicom+xml.
- Parsing success and error responses, dispatching on Content-Type.

WebSocket messages are always application/dicom+json per PS3.18 Section 8.10.5
and are NOT handled here.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from pydicom import Dataset

logger = logging.getLogger(__name__)

CONTENT_TYPE_JSON = "application/dicom+json"
CONTENT_TYPE_XML = "application/dicom+xml"

if TYPE_CHECKING:
    pass


def make_headers(content_type: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """
    Build HTTP headers for a UPS-RS request.

    The Content-Type and Accept headers are set to the negotiated content type.
    Additional headers can be merged via the ``extra`` parameter.

    Args:
        content_type: MIME type to use for Content-Type and Accept
            (``application/dicom+json`` or ``application/dicom+xml``).
        extra: Optional mapping of additional headers to merge into the result.
            Keys in ``extra`` override the defaults only when they do not duplicate
            Content-Type or Accept (those are always set from ``content_type``).

    Returns:
        A new dict containing at minimum ``Content-Type`` and ``Accept`` headers.

    """
    headers: dict[str, str] = {
        "Content-Type": content_type,
        "Accept": content_type,
    }
    if extra:
        headers.update(extra)
    return headers


def serialize_request(data: dict[str, Any] | Dataset, content_type: str) -> bytes:
    """
    Serialize a request body according to the negotiated content type.

    For ``application/dicom+xml``, the data is converted to a pydicom
    ``Dataset`` (if it is not already one) and then serialised via
    ``pydicom_xml.to_xml()``.  For all other content types (including the
    default ``application/dicom+json``), the data is serialised as JSON.

    Args:
        data: Request body as a JSON-compatible dict or a pydicom ``Dataset``.
        content_type: MIME type controlling the serialisation path.

    Returns:
        UTF-8–encoded bytes ready to be sent as an HTTP request body.

    Raises:
        ValueError: If ``data`` cannot be converted to a Dataset for XML
            serialisation.

    """
    if content_type == CONTENT_TYPE_XML:
        from pydicom_xml import to_xml

        if isinstance(data, Dataset):
            ds = data
        else:
            ds = Dataset.from_json(json.dumps(data))
        return to_xml(ds)

    # Default: JSON
    return json.dumps(data).encode("utf-8")


def extract_boundary(content_type_header: str) -> str | None:
    """
    Extract the multipart boundary string from a Content-Type header value.

    Args:
        content_type_header: The full value of the Content-Type HTTP header,
            e.g. ``multipart/related; type="application/dicom+xml"; boundary=xyz``.

    Returns:
        The boundary string without surrounding quotes, or ``None`` when the
        header does not contain a boundary parameter.

    """
    match = re.search(r'boundary="?([^";]+)"?', content_type_header, re.IGNORECASE)
    return match.group(1) if match else None


def parse_response_body(
    content: bytes,
    text: str,
    content_type_header: str,
) -> Any:  # noqa: ANN401  – mirrors response.json() return type
    """
    Parse an HTTP response body based on Content-Type.

    Decision logic:

    1. If the Content-Type contains ``application/dicom+xml`` → parse as a
       single DICOM XML document via ``pydicom_xml.from_xml()``.
    2. If the Content-Type contains ``multipart/related`` → extract the
       boundary and parse each MIME part as DICOM XML via
       ``pydicom_xml.from_xml()``, returning a ``list[Dataset]``.
    3. Otherwise → call ``json.loads(text)`` (the historic behaviour).

    Args:
        content: Raw response body bytes.
        text: Response body decoded as a string (used for JSON fallback).
        content_type_header: Value of the HTTP ``Content-Type`` response header.

    Returns:
        A ``Dataset``, a ``list[Dataset]``, or the result of ``json.loads(text)``
        depending on the content type.

    Raises:
        json.JSONDecodeError: If the JSON fallback path is taken but the body
            is not valid JSON.

    """
    # Check multipart BEFORE checking for application/dicom+xml because the
    # multipart Content-Type header also contains the type parameter value
    # 'application/dicom+xml', which would otherwise be matched first.
    if "multipart/related" in content_type_header:
        boundary = extract_boundary(content_type_header)
        return _parse_multipart_xml(content, boundary)

    if CONTENT_TYPE_XML in content_type_header:
        from pydicom_xml import from_xml

        return from_xml(content)

    # Default JSON path
    return json.loads(text)


def _parse_multipart_xml(content: bytes, boundary: str | None) -> list[Dataset]:
    """
    Parse a multipart/related body whose parts are DICOM XML documents.

    This handles the search response format where a server returns multiple
    NativeDicomModel documents wrapped in a MIME multipart envelope.

    Args:
        content: Full multipart body bytes.
        boundary: Multipart boundary string (without leading ``--``).
            When ``None``, an empty list is returned.

    Returns:
        A list of ``Dataset`` objects parsed from each MIME part body.

    """
    from pydicom_xml import from_xml

    if boundary is None:
        logger.warning("multipart/related response missing boundary parameter; returning empty list")
        return []

    delimiter = f"--{boundary}".encode()
    end_delimiter = f"--{boundary}--".encode()

    datasets: list[Dataset] = []
    parts = content.split(delimiter)

    for part in parts:
        stripped = part.strip()
        if not stripped or stripped == b"--" or stripped == end_delimiter.lstrip(b"--"):
            continue
        # Each MIME part has headers followed by a blank line, then the body
        if b"\r\n\r\n" in stripped:
            _, part_body = stripped.split(b"\r\n\r\n", 1)
        elif b"\n\n" in stripped:
            _, part_body = stripped.split(b"\n\n", 1)
        else:
            part_body = stripped

        # Remove trailing boundary marker if present
        part_body = part_body.rstrip(b"\r\n")
        if not part_body:
            continue

        try:
            ds = from_xml(part_body)
            datasets.append(ds)
        except Exception:
            logger.exception("Failed to parse multipart DICOM XML part; skipping")

    return datasets
