# DICOM UPS-RS Client

A comprehensive Python client for interacting with DICOM UPS-RS (Unified Procedure Step - RESTful Services) servers.

## Features

- Complete implementation of UPS-RS operations:
  - Create, retrieve, update, and search workitems
  - Change workitem state
  - Request cancellation
  - Subscribe to and receive event notifications
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
```

Run `ups-rs-client --help` to see all available commands and options.

## License

MIT License

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
