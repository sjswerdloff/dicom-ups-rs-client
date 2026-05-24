"""Command handler implementations for the DICOM UPS-RS CLI."""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydicom import dcmread
from pydicom.uid import generate_uid


def _event_handler(event_data: dict[str, Any]) -> None:
    """
    Handle incoming UPS-RS events.

    Args:
        event_data: dictionary containing event information

    """
    try:
        event_type_id = event_data.get("00001002", {}).get("Value", ["unknown"])[0]
        affected_sop_instance_uid = event_data.get("00001000", {}).get("Value", ["unknown"])[0]
        procedure_step_state = event_data.get("00741000", {}).get("Value", ["unknown"])[0]
        print(f"\nEVENT RECEIVED: {event_type_id} - Workitem: {affected_sop_instance_uid}")
        print(f"Procedure Step State: {procedure_step_state}")
    except (KeyError, IndexError):
        print("\nEVENT RECEIVED: (unable to extract event type or workitem UID)")


if TYPE_CHECKING:
    import argparse

    from dicom_ups_rs_client.ups_rs_client import UPSRSClient


def handle_create_command(client: UPSRSClient, args: argparse.Namespace) -> None:
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


def handle_retrieve_command(client: UPSRSClient, args: argparse.Namespace) -> None:
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


def handle_search_command(client: UPSRSClient, args: argparse.Namespace) -> None:
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


def handle_update_command(client: UPSRSClient, args: argparse.Namespace) -> None:
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


def handle_change_state_command(client: UPSRSClient, args: argparse.Namespace) -> None:
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


def handle_cancel_request_command(client: UPSRSClient, args: argparse.Namespace) -> None:
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


def handle_subscribe_command(client: UPSRSClient, args: argparse.Namespace) -> None:
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


def handle_unsubscribe_command(client: UPSRSClient, args: argparse.Namespace) -> None:
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
        # Parse filter parameters (optional for unsubscribe; the server identifies
        # the subscription by the subscriber AE Title per PS3.18 §11.10)
        filter_params: dict[str, str] = {}
        for param in args.filter:
            if "=" in param:
                key, value = param.split("=", 1)
                filter_params[key] = value
            else:
                logging.warning(f"Ignoring invalid filter parameter (missing '='): {param}")

        success, response = client.unsubscribe_from_filtered_worklist(filter_params or None, args.deletion_lock)
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
