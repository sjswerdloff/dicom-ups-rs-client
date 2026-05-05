# Insights

## XML Content Type Support Implementation (2026-05-05)

### pydicom-xml API Discovery

The handoff document described functions like `dataset_to_xml`, `dataset_from_xml`,
`extract_boundary`, and `datasets_from_multipart_xml`. None of these exist in the actual
library. The real API is just two functions: `to_xml(dataset) -> bytes` and
`from_xml(bytes) -> Dataset`. Always verify library APIs against the installed package,
not documentation written before the library was finalized.

### Multipart Check Must Precede XML Check

`parse_response_body` must test `"multipart/related" in content_type_header` BEFORE
`CONTENT_TYPE_XML in content_type_header`. A multipart/related response for XML search
results has a Content-Type like:

```
multipart/related; type="application/dicom+xml"; boundary=abc
```

This header contains `application/dicom+xml` as a substring, so the XML branch would
match first and pass the entire multipart body to `from_xml()`, which fails. Ordering
matters when one content-type header is a substring of another.

### Late-Binding Module Import Preserves Patchability

When splitting a monolithic module into `cli.py` and `ups_rs_client.py`, importing
`UPSRSClient` directly in `cli.py` via `from ... import UPSRSClient` creates a local
binding that test patches to `dicom_ups_rs_client.ups_rs_client.UPSRSClient` cannot
reach. The fix is to use `import dicom_ups_rs_client.ups_rs_client as _ups_rs_module`
and call `_ups_rs_module.UPSRSClient(...)` at call-time, so the patch on the module
attribute is honored at runtime.

### WebSocket Timing Contracts Are Implicit

The `asyncio.wait_for(websocket.recv(), timeout=1.0)` pattern in the WebSocket receive
loop is load-bearing for test behavior. Tests that mock connection errors rely on this
specific timeout to exit cleanly. When rewriting the loop, preserving the original
`wait_for` call preserved test timing contracts that were not explicitly documented.

### Module-Level Imports vs Fixture-Local Imports for Test Type Annotations

When a test fixture returns a type from another test module (e.g., `MockSession` from
`conftest.py`), using a quoted forward reference (`"MockSession"`) with a local import
inside the fixture body triggers UP037 (remove quotes from type annotation). The correct
pattern is to import at module level so the name is available for the annotation without
quotes.

### Splitting a 1000+ Line File

The `exceptions.py`, `enums.py`, `serialization.py`, `cli.py` split was done cleanly by:
1. Writing new modules with the extracted code
2. Updating `ups_rs_client.py` to import from those modules
3. Re-exporting from `__init__.py` for backward compatibility
4. Keeping `main()` and `_event_handler` re-exported from `ups_rs_client.py` for any
   existing callers that import them from there

The re-export wrapper approach avoids breaking existing import paths while enabling the
split.

### Search Response Returns a List, Not a Dict

The `_parse_success_response` helper initially wrapped non-dict parsed JSON in
`{"data": parsed}`. UPS-RS search workitems responses are JSON arrays. Tests expect a
`list`, not a dict wrapper. The correct behavior is to return `parsed` directly when it
is already a list, only wrapping genuinely unexpected types.
