"""Command-line interface for the DICOM UPS-RS client."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydicom import dcmread
from pydicom.uid import generate_uid

import dicom_ups_rs_client.ups_rs_client as _ups_rs_module
from dicom_ups_rs_client.serialization import CONTENT_TYPE_JSON, CONTENT_TYPE_XML

# UPSRSClient is used only in type annotations here; at runtime the client is
# created via ``_ups_rs_module.UPSRSClient(...)`` so that test patches targeting
# ``dicom_ups_rs_client.ups_rs_client.UPSRSClient`` take effect.
if TYPE_CHECKING:
    from dicom_ups_rs_client.ups_rs_client import UPSRSClient


def _event_handler(event_data: dict[str, Any]) -> None:
    """
    Handle incoming UPS-RS events.

    Args:
        event_data: dictionary containing event information

    """
    try:
        event_type_id = event_data.get("00001002", {}).get("Value", ["unknown"])[0]
        affected_sop_instance_uid = event_data.get("00001000", {}).get("Value", ["unknown"])[0]
        print(f"\nEVENT RECEIVED: {event_type_id} - Workitem: {affected_sop_instance_uid}")
    except (KeyError, IndexError):
        print("\nEVENT RECEIVED: (unable to extract event type or workitem UID)")

    print(json.dumps(event_data, indent=2))
    print("-" * 60)


def main() -> None:
    """Execute Main CLI entry point for UPS-RS client."""
    parser = argparse.ArgumentParser(description="DICOM UPS-RS Client")
    parser.add_argument(
        "--server",
        type=str,
        required=True,
        help="URL of the UPS-RS server (e.g., http://localhost:5000)",
    )
    parser.add_argument(
        "--aetitle",
        type=str,
        help="Application Entity Title for subscription operations",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds")
    parser.add_argument("--max-retries", type=int, default=3, help="Maximum number of request retries")

    # Add SSL/TLS related arguments
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Disable SSL certificate verification (not recommended)",
    )
    parser.add_argument(
        "--ca-bundle",
        type=str,
        help="Path to CA bundle file for SSL verification",
    )
    parser.add_argument(
        "--client-cert",
        type=str,
        help="Path to client certificate file (.pem with both cert and key)",
    )
    parser.add_argument(
        "--client-cert-key",
        type=str,
        help="Path to separate client key file (use with --client-cert for separate files)",
    )
    parser.add_argument(
        "--websocket-url-override",
        type=str,
        help="Override WebSocket URL template. Use {aetitle} as placeholder. Example: wss://example.com:9443/ws/subscribers/{aetitle}",
    )
    parser.add_argument(
        "--content-type",
        choices=[CONTENT_TYPE_JSON, CONTENT_TYPE_XML],
        default=CONTENT_TYPE_JSON,
        help="MIME type for request/response content negotiation",
    )

    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Create workitem command
    create_parser = subparsers.add_parser("create", help="Create a new workitem")
    create_parser.add_argument("--workitem-uid", type=str, help="Optional UID for the workitem")
    create_parser.add_argument("--input-file", type=str, help="JSON file containing workitem data")
    create_parser.add_argument("--input-dcm", type=str, help="DICOM file containing workitem data")

    # Retrieve workitem command
    retrieve_parser = subparsers.add_parser("retrieve", help="Retrieve a workitem")
    retrieve_parser.add_argument(
        "--workitem-uid",
        type=str,
        required=True,
        help="UID of the workitem to retrieve",
    )
    retrieve_parser.add_argument(
        "--output-file",
        type=str,
        help="Output file to save the retrieved workitem JSON",
    )

    # Search workitems command
    search_parser = subparsers.add_parser("search", help="Search for workitems")
    search_parser.add_argument(
        "--match",
        action="append",
        help="Match parameters (e.g., '00741000=SCHEDULED')",
        default=[],
    )
    search_parser.add_argument(
        "--includefield",
        action="append",
        help="Fields to include in results",
        default=[],
    )
    search_parser.add_argument("--fuzzy", action="store_true", help="Enable fuzzy matching")
    search_parser.add_argument(
        "--state",
        choices=["SCHEDULED", "IN PROGRESS", "CANCELED", "COMPLETED"],
        help="Filter by Procedure Step State (00741000)",
    )
    search_parser.add_argument(
        "--readiness",
        choices=["READY", "UNAVAILABLE", "INCOMPLETE"],
        help="Filter by Input Readiness State (00404041)",
    )
    search_parser.add_argument(
        "--start-date",
        type=str,
        help="Filter by Scheduled Start Date (00404005) in YYYYMMDD format",
    )
    search_parser.add_argument("--label", type=str, help="Filter by Procedure Step Label (00741204)")
    search_parser.add_argument("--offset", type=int, default=0, help="Starting position of results")
    search_parser.add_argument("--limit", type=int, help="Maximum number of results to return")
    search_parser.add_argument("--no-cache", action="store_true", help="Request non-cached results")
    search_parser.add_argument("--output-file", type=str, help="Output file to save search results")
    search_parser.add_argument("--summary", action="store_true", help="Display only a summary of results")
    search_parser.add_argument(
        "--display-fields",
        type=str,
        help="Comma-separated list of fields to display in output summary",
    )

    # Update workitem command
    update_parser = subparsers.add_parser("update", help="Update a workitem")
    update_parser.add_argument("--workitem-uid", type=str, required=True, help="UID of the workitem to update")
    update_parser.add_argument("--transaction-uid", type=str, help="Transaction UID")
    update_parser.add_argument("--input-file", type=str, help="JSON file containing update data")
    update_parser.add_argument("--procedure-label", type=str, help="Set the Procedure Step Label (0074,1204)")
    update_parser.add_argument(
        "--procedure-description",
        type=str,
        help="Set the Procedure Step Description (0040,0007)",
    )

    # Change workitem state command
    state_parser = subparsers.add_parser("change-state", help="Change workitem state")
    state_parser.add_argument(
        "--workitem-uid",
        type=str,
        required=True,
        help="UID of the workitem to change state",
    )
    state_parser.add_argument(
        "--state",
        type=str,
        required=True,
        choices=["IN PROGRESS", "COMPLETED", "CANCELED"],
        help="New state for the workitem",
    )
    state_parser.add_argument(
        "--transaction-uid",
        type=str,
        help="Transaction UID (required for COMPLETED/CANCELED states, optional for IN PROGRESS)",
    )

    # Request cancellation command
    cancel_parser = subparsers.add_parser("request-cancel", help="Request cancellation of a workitem")
    cancel_parser.add_argument(
        "--workitem-uid",
        type=str,
        required=True,
        help="UID of the workitem to request cancellation for",
    )
    cancel_parser.add_argument("--reason", type=str, help="Reason for the cancellation request")
    cancel_parser.add_argument("--contact-name", type=str, help="Display name of the contact person")
    cancel_parser.add_argument(
        "--contact-uri",
        type=str,
        help="URI for contacting the requestor (e.g., mailto:user@example.com)",
    )

    # Subscribe command
    subscribe_parser = subparsers.add_parser("subscribe", help="Subscribe to workitem events")
    subscribe_group = subscribe_parser.add_mutually_exclusive_group(required=True)
    subscribe_group.add_argument("--worklist", action="store_true", help="Subscribe to the entire worklist")
    subscribe_group.add_argument(
        "--filtered-worklist",
        action="store_true",
        help="Subscribe to a filtered worklist",
    )
    subscribe_group.add_argument("--workitem", type=str, help="UID of a specific workitem to subscribe to")
    subscribe_parser.add_argument(
        "--filter",
        action="append",
        help="Filter parameters for filtered worklist (e.g., '00741000=SCHEDULED')",
        default=[],
    )
    subscribe_parser.add_argument(
        "--deletion-lock",
        action="store_true",
        help="Request deletion lock for the subscription",
    )
    subscribe_parser.add_argument(
        "--monitor",
        action="store_true",
        help="Monitor for event notifications after subscribing",
    )

    # Unsubscribe command
    unsubscribe_parser = subparsers.add_parser("unsubscribe", help="Unsubscribe from workitem events")
    unsubscribe_group = unsubscribe_parser.add_mutually_exclusive_group(required=True)
    unsubscribe_group.add_argument("--worklist", action="store_true", help="Unsubscribe from the entire worklist")
    unsubscribe_group.add_argument(
        "--filtered-worklist",
        action="store_true",
        help="Unsubscribe from a filtered worklist",
    )
    unsubscribe_group.add_argument("--workitem", type=str, help="UID of a specific workitem to unsubscribe from")
    unsubscribe_parser.add_argument(
        "--filter",
        action="append",
        help="Filter parameters for filtered worklist (e.g., '00741000=SCHEDULED')",
        default=[],
    )
    unsubscribe_parser.add_argument(
        "--deletion-lock",
        action="store_true",
        help="Request deletion lock for the unsubscription",
    )

    args = parser.parse_args()

    # Set up logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Check for required command
    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Determine SSL verification setting
    verify_ssl: bool | str = True
    if args.no_verify_ssl:
        verify_ssl = False
    elif args.ca_bundle:
        verify_ssl = args.ca_bundle

    # Validate client certificate arguments
    if args.client_cert_key and not args.client_cert:
        parser.error("--client-cert-key requires --client-cert to also be specified")

    # Determine client certificate setting
    client_cert: str | tuple[str, str] | None = None
    if args.client_cert:
        if args.client_cert_key:
            client_cert = (args.client_cert, args.client_cert_key)
        else:
            client_cert = args.client_cert

    # Initialize client with SSL settings.
    # Access UPSRSClient through the module reference so that tests can patch
    # ``dicom_ups_rs_client.ups_rs_client.UPSRSClient`` and have it take effect.
    client = _ups_rs_module.UPSRSClient(
        base_url=args.server,
        aetitle=args.aetitle,
        timeout=args.timeout,
        max_retries=args.max_retries,
        verify_ssl=verify_ssl,
        client_cert=client_cert,
        websocket_url_override=args.websocket_url_override,
        content_type=args.content_type,
    )

    try:
        # Execute the requested command
        if args.command == "create":
            _handle_create_command(client, args)
        elif args.command == "retrieve":
            _handle_retrieve_command(client, args)
        elif args.command == "search":
            _handle_search_command(client, args)
        elif args.command == "update":
            _handle_update_command(client, args)
        elif args.command == "change-state":
            _handle_change_state_command(client, args)
        elif args.command == "request-cancel":
            _handle_cancel_request_command(client, args)
        elif args.command == "subscribe":
            _handle_subscribe_command(client, args)
        elif args.command == "unsubscribe":
            _handle_unsubscribe_command(client, args)
    finally:
        # Ensure proper cleanup
        client.close()


def _handle_create_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the create workitem command."""
    # Generate a DICOM UID if one wasn't provided on the command line
    workitem_uid = args.workitem_uid or str(generate_uid())

    # Load workitem data if provided
    workitem_data = None
    if args.input_file:
        try:
            input_path = Path(args.input_file)
            with input_path.open() as f:
                workitem_data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to load workitem data from {args.input_file}: {str(e)}")
            sys.exit(1)

    if args.input_dcm:
        try:
            dcm_path = Path(args.input_dcm)
            local_json_path = Path(dcm_path.name.removesuffix("dcm") + "json")

            workitem_ds = dcmread(args.input_dcm)
            # Make sure the workitem_uid is consistent in the post and the data
            if not args.workitem_uid:
                workitem_uid = str(workitem_ds.SOPInstanceUID)
            else:
                workitem_ds.SOPInstanceUID = workitem_uid
            workitem_uid = workitem_uid or str(generate_uid())

            workitem_data = json.loads(workitem_ds.to_json())
            local_json_path.write_text(json.dumps(workitem_data, indent=2))

        except Exception as e:
            logging.error(f"Failed to load workitem data from {args.input_dcm}: {str(e)}")
            sys.exit(1)

    # Create workitem
    success, response = client.create_workitem(workitem_data, workitem_uid)

    if success:
        print("Workitem created successfully")
        print(json.dumps(response, indent=2, default=str))
        print(f"Workitem UID: {workitem_uid}")
        sys.exit(0)
    else:
        print(f"Failed to create workitem: {response}")
        sys.exit(1)


def _handle_retrieve_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the retrieve workitem command."""
    success, response = client.retrieve_workitem(args.workitem_uid)

    if success:
        print("Workitem retrieved successfully")
        formatted_response = json.dumps(response, indent=2, default=str)
        print(formatted_response)

        # Save to file if requested
        if args.output_file:
            try:
                output_path = Path(args.output_file)
                with output_path.open("w") as f:
                    f.write(formatted_response)
                print(f"Workitem saved to {args.output_file}")
            except Exception as e:
                print(f"Failed to save workitem to file: {str(e)}")
                sys.exit(1)

        sys.exit(0)
    else:
        print(f"Failed to retrieve workitem: {response}")
        sys.exit(1)


def _handle_search_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the search workitems command."""
    # Parse match parameters
    match_parameters: dict[str, str] = {}
    for param in args.match:
        if "=" in param:
            key, value = param.split("=", 1)
            match_parameters[key] = value
        else:
            logging.warning(f"Ignoring invalid match parameter (missing '='): {param}")

    # Add common search parameters if provided
    if args.state:
        match_parameters["00741000"] = args.state

    if args.readiness:
        match_parameters["00404041"] = args.readiness

    if args.start_date:
        match_parameters["00404005"] = args.start_date

    if args.label:
        match_parameters["00741204"] = args.label

    # Inform user about search criteria
    _summarize_search_criteria(match_parameters)

    # Perform search
    success, response = client.search_workitems(
        match_parameters,
        args.includefield,
        args.fuzzy,
        args.offset,
        args.limit,
        args.no_cache,
    )

    if not success:
        print(f"Failed to search workitems: {response}")
        sys.exit(1)

    if isinstance(response, list) and response:
        result_count = len(response)
        print(f"Search returned {result_count} result(s)")

        # Display summary if requested or full results
        if args.summary:
            _summarize_search_results(args, response)
        else:
            # Print full formatted results
            formatted_response = json.dumps(response, indent=2, default=str)
            print(formatted_response)

        # Save to file if requested
        if args.output_file:
            try:
                output_path = Path(args.output_file)
                with output_path.open("w") as f:
                    f.write(json.dumps(response, indent=2, default=str))
                print(f"Search results saved to {args.output_file}")
            except Exception as e:
                print(f"Failed to save search results to file: {str(e)}")
                sys.exit(1)
    else:
        print("No matching workitems found")

    sys.exit(0)


def _summarize_search_criteria(match_parameters: dict[str, str]) -> None:
    """Print a summary of the search criteria."""
    if match_parameters:
        print("Searching with criteria:")
        for tag, value in match_parameters.items():
            tag_name = ""
            if tag == "00741000":
                tag_name = "Procedure Step State"
            elif tag == "00404041":
                tag_name = "Input Readiness State"
            elif tag == "00404005":
                tag_name = "Scheduled Start Date"
            elif tag == "00741204":
                tag_name = "Procedure Step Label"

            if tag_name:
                print(f"  {tag_name} ({tag}) = {value}")
            else:
                print(f"  {tag} = {value}")
    else:
        print("Searching with no criteria (will return all workitems)")


def _summarize_search_results(args: argparse.Namespace, response: list[Any]) -> None:
    """Print a summary of the search results."""
    print("\nSummary of Workitems:")
    print("-" * 80)

    # Determine which fields to display in summary
    display_fields = (
        args.display_fields.split(",") if args.display_fields else ["00080018", "00741000", "00404041", "00741204"]
    )
    # Print header
    header_row = []
    for field in display_fields:
        if field == "00080018":
            header_row.append("SOP Instance UID")
        elif field == "00404005":
            header_row.append("Scheduled Start")
        elif field == "00404041":
            header_row.append("Input Readiness")
        elif field == "00741000":
            header_row.append("Procedure Step State")
        elif field == "00741204":
            header_row.append("Procedure Label")
        else:
            header_row.append(field)

    print(" | ".join(header_row))
    print("-" * 80)

    # Print each workitem
    for wi in response:
        row = []
        for field in display_fields:
            if isinstance(wi, dict) and field in wi and "Value" in wi[field] and wi[field]["Value"]:
                value = wi[field]["Value"][0]
                # Truncate long values
                if isinstance(value, str) and len(value) > 30:
                    value = f"{value[:27]}..."
                row.append(str(value))
            else:
                row.append("N/A")

        print(" | ".join(row))

    print("-" * 80)


def _handle_update_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the update workitem command."""
    # Prepare update data
    update_data: dict[str, Any] = {}

    # Load from file if provided
    if args.input_file:
        try:
            input_path = Path(args.input_file)
            with input_path.open() as f:
                update_data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to load update data from {args.input_file}: {str(e)}")
            sys.exit(1)

    # Add command line attributes if provided
    if args.procedure_label:
        update_data["00741204"] = {"vr": "LO", "Value": [args.procedure_label]}

    if args.procedure_description:
        update_data["00400007"] = {"vr": "LO", "Value": [args.procedure_description]}

    if not args.transaction_uid:
        print("Transaction UID not provided, only valid if UPS is SCHEDULED")

    # Ensure we have some update data
    if not update_data:
        logging.error("No update data provided. Use --input-file or command line options.")
        sys.exit(1)

    # Update workitem
    success, response = client.update_workitem(args.workitem_uid, args.transaction_uid, update_data)

    if success:
        print("Workitem updated successfully")

        # Display any warnings
        if isinstance(response, dict) and "warning" in response:
            print(f"Warning: {response['warning']}")

        # Display full response if available and verbose
        if args.verbose and isinstance(response, dict) and "response" in response:
            print("\nFull response:")
            print(json.dumps(response["response"], indent=2, default=str))

        sys.exit(0)
    else:
        print(f"Failed to update workitem: {response}")
        sys.exit(1)


def _handle_change_state_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the change workitem state command."""
    # Change workitem state
    success, response = client.change_workitem_state(args.workitem_uid, args.state, args.transaction_uid)

    if success:
        print(f"Workitem state changed successfully to {args.state}")

        # Display transaction UID for future reference
        if isinstance(response, dict) and "transaction_uid" in response:
            print(f"Transaction UID: {response['transaction_uid']}")
            print("Keep this UID for future state changes to this workitem")

        # Display any warnings
        if isinstance(response, dict) and "warning" in response:
            print(f"Warning: {response['warning']}")

        # Display full response if verbose
        if args.verbose and isinstance(response, dict) and "response" in response:
            print("\nFull response:")
            print(json.dumps(response["response"], indent=2, default=str))

        sys.exit(0)
    else:
        print(f"Failed to change workitem state: {response}")
        sys.exit(1)


def _handle_cancel_request_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the request cancellation command."""
    # Request cancellation
    success, response = client.request_cancellation(args.workitem_uid, args.reason, args.contact_name, args.contact_uri)

    if not success:
        print(f"Failed to request cancellation: {response}")
        sys.exit(1)

    print("Cancellation request sent successfully")

    # Display any warnings
    if isinstance(response, dict) and "warning" in response:
        print(f"Warning: {response['warning']}")

    # Display full response if available and verbose
    if args.verbose and isinstance(response, dict) and "response" in response:
        print("\nFull response:")
        print(json.dumps(response["response"], indent=2, default=str))

    # Include note about processing
    print("\nNote: The cancellation request has been accepted by the server, but the workitem")
    print("owner is not obliged to honor the request and may not receive notification.")

    sys.exit(0)


def _handle_subscribe_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the subscribe command."""
    # Check if AE Title is provided
    if not args.aetitle:
        print("Error: AE Title (--aetitle) is required for subscription operations")
        sys.exit(1)

    # Handle different subscription types
    if args.worklist:
        success, response = client.subscribe_to_worklist(args.deletion_lock)
        subscription_type = "worklist"
    elif args.filtered_worklist:
        # Parse filter parameters
        filter_params: dict[str, str] = {}
        for param in args.filter:
            if "=" in param:
                key, value = param.split("=", 1)
                filter_params[key] = value
            else:
                logging.warning(f"Ignoring invalid filter parameter (missing '='): {param}")

        if not filter_params:
            logging.error("Filtered worklist subscription requires at least one filter parameter")
            sys.exit(1)

        success, response = client.subscribe_to_filtered_worklist(filter_params, args.deletion_lock)
        subscription_type = "filtered worklist"
    else:  # workitem
        success, response = client.subscribe_to_workitem(args.workitem, args.deletion_lock)
        subscription_type = f"workitem {args.workitem}"

    if success:
        print(f"Successfully subscribed to {subscription_type}")

        # Display WebSocket URL if available
        if isinstance(response, dict) and "ws_url" in response:
            print(f"WebSocket URL: {response['ws_url']}")

        # Display any warnings
        if isinstance(response, dict) and "warning" in response:
            print(f"Warning: {response['warning']}")

        # Start monitoring if requested
        if args.monitor:
            print("\nStarting event monitoring. Press Ctrl+C to stop.")

            # Set up signal handler for graceful shutdown
            def signal_handler(sig: int, frame: object) -> None:  # noqa: ARG001
                print("\nShutting down...")
                client.disconnect()
                sys.exit(0)

            signal.signal(signal.SIGINT, signal_handler)

            # Connect to WebSocket and start receiving events
            # assign a callback to perform application specific processing
            client.connect_websocket(event_callback=_event_handler)

            # Keep the main thread alive
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                client.disconnect()

        sys.exit(0)
    else:
        print(f"Failed to subscribe to {subscription_type}: {response}")
        sys.exit(1)


def _handle_unsubscribe_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the unsubscribe command."""
    # Check if AE Title is provided
    if not args.aetitle:
        print("Error: AE Title (--aetitle) is required for subscription operations")
        sys.exit(1)

    # Handle different subscription types
    if args.worklist:
        success, response = client.unsubscribe_from_worklist(args.deletion_lock)
        subscription_type = "worklist"
    elif args.filtered_worklist:
        # Parse filter parameters
        filter_params = {}
        for param in args.filter:
            if "=" in param:
                key, value = param.split("=", 1)
                filter_params[key] = value
            else:
                logging.warning(f"Ignoring invalid filter parameter (missing '='): {param}")

        if not filter_params:
            logging.error("Filtered worklist subscription requires at least one filter parameter")
            sys.exit(1)

        success, response = client.unsubscribe_from_filtered_worklist(filter_params, args.deletion_lock)
        subscription_type = "filtered worklist"
    else:  # workitem
        success, response = client.unsubscribe_from_workitem(args.workitem, args.deletion_lock)
        subscription_type = f"workitem {args.workitem}"

    if success:
        print(f"Successfully unsubscribed from {subscription_type}")

        # Display any warnings
        if isinstance(response, dict) and "warning" in response:
            print(f"Warning: {response['warning']}")

        sys.exit(0)
    else:
        print(f"Failed to unsubscribe from {subscription_type}: {response}")
        sys.exit(1)
