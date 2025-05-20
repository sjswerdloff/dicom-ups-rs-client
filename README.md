# DICOM UPS-RS Client

A comprehensive Python client for interacting with DICOM UPS-RS (Unified Procedure Step - RESTful Services) servers.

## Features

- Complete implementation of UPS-RS operations:
  - Create, retrieve, update, and search workitems
  - Change workitem state
  - Request cancellation
  - Subscribe to and receive event notifications
- SSL/TLS support:
  - SSL certificate verification (with option to disable)
  - Custom CA bundle support
  - Client certificate authentication
  - Support for both HTTP/HTTPS and WS/WSS protocols
- Command-line interface for all operations
- Context manager support for proper resource management
- Asynchronous API for better integration with async applications
- Comprehensive error handling and retry logic
- WebSocket-based event notification support
- Detailed logging

## Installation

From source:

```bash
git clone https://github.com/sjswerdloff/dicom-ups-rs-client.git
cd dicom-ups-rs-client
pip install -e .
```

## Usage

### As a Python Library

```python
from ups_rs_client import UPSRSClient, UPSState

# Initialize the client
client = UPSRSClient(base_url="http://example.com/dicom-web", aetitle="CLIENT_AE")

# Initialize with SSL options for HTTPS
client = UPSRSClient(
    base_url="https://secure.example.com/dicom-web",
    verify_ssl=True,  # Default is True
    client_cert=("/path/to/client.crt", "/path/to/client.key")  # Optional
)

# Using as a context manager (recommended)
with UPSRSClient(base_url="http://example.com/dicom-web") as client:
    # Create a workitem
    success, response = client.create_workitem()
    if success:
        workitem_uid = response.get("location").split("/")[-1]
        print(f"Created workitem: {workitem_uid}")

        # Change state to IN PROGRESS
        success, change_response = client.change_workitem_state(
            workitem_uid,
            UPSState.IN_PROGRESS
        )
        if success:
            transaction_uid = change_response["transaction_uid"]
            print(f"Transaction UID: {transaction_uid}")

            # Complete the workitem
            client.change_workitem_state(
                workitem_uid,
                UPSState.COMPLETED,
                transaction_uid
            )
```

### Using Async API

```python
import asyncio
from ups_rs_client import UPSRSClient

async def main():
    client = UPSRSClient(base_url="http://example.com/dicom-web")

    # Create a workitem asynchronously
    success, response = await client.create_workitem_async()

    # Retrieve multiple workitems concurrently
    tasks = [
        client.retrieve_workitem_async("1.2.3.4.5.6"),
        client.retrieve_workitem_async("1.2.3.4.5.7"),
        client.retrieve_workitem_async("1.2.3.4.5.8")
    ]
    results = await asyncio.gather(*tasks)

    # Clean up
    client.close()

asyncio.run(main())
```

### SSL/TLS Configuration

```python
# Disable SSL verification (not recommended for production)
client = UPSRSClient(
    base_url="https://example.com/dicom-web",
    verify_ssl=False
)

# Use custom CA bundle
client = UPSRSClient(
    base_url="https://example.com/dicom-web",
    verify_ssl="/path/to/ca-bundle.crt"
)

# Use client certificate authentication
client = UPSRSClient(
    base_url="https://example.com/dicom-web",
    client_cert=("/path/to/client.crt", "/path/to/client.key")
)

# Single file containing both certificate and key
client = UPSRSClient(
    base_url="https://example.com/dicom-web",
    client_cert="/path/to/client.pem"
)

# Handle WebSocket URL mismatches behind proxies
client = UPSRSClient(
    base_url="https://example.com:9443/dicom-web",
    websocket_url_override="wss://example.com:9443/ws/subscribers/{aetitle}"
)
```

### Event Notification Handling

```python
import time
from ups_rs_client import UPSRSClient

# Define an event handler
def handle_event(event_data):
    print(f"Received event: {event_data}")

# Initialize client with AE Title for subscription
client = UPSRSClient(
    base_url="http://example.com/dicom-web",
    aetitle="CLIENT_AE"
)

# Subscribe to workitem events
success, response = client.subscribe_to_worklist()
if success:
    # Connect WebSocket for event notifications
    client.connect_websocket(handle_event)

    try:
        # Keep the application running to receive events
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        # Disconnect when done
        client.disconnect()
        client.close()
```

### Command-Line Interface

The client includes a full-featured command-line interface:

```bash
# Create a workitem
ups-rs-client --server http://example.com/dicom-web create

# Retrieve a workitem
ups-rs-client --server http://example.com/dicom-web retrieve --workitem-uid 1.2.3.4.5.6

# Search for workitems
ups-rs-client --server http://example.com/dicom-web search --state "SCHEDULED"

# Change workitem state
ups-rs-client --server http://example.com/dicom-web change-state \
  --workitem-uid 1.2.3.4.5.6 \
  --state "IN PROGRESS"

# Subscribe and monitor events
ups-rs-client --server http://example.com/dicom-web --aetitle CLIENT_AE \
  subscribe --worklist --monitor

# Using HTTPS with SSL verification disabled (not recommended)
ups-rs-client --server https://example.com/dicom-web --no-verify-ssl create

# Using custom CA bundle
ups-rs-client --server https://example.com/dicom-web \
  --ca-bundle /path/to/ca-bundle.crt create

# Using client certificate
ups-rs-client --server https://example.com/dicom-web \
  --client-cert /path/to/client.crt --client-cert-key /path/to/client.key create

# Handle WebSocket URL issues behind proxies
ups-rs-client --server https://localhost:9443/dicom-web --no-verify-ssl \
  --aetitle SUBSCRIBER \
  --websocket-url-override "wss://localhost:9443/ws/subscribers/{aetitle}" \
  subscribe --worklist --monitor
```

Run `ups-rs-client --help` to see all available commands and options.

## License

Apache2 License

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
