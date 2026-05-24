"""Command-line interface for the DICOM UPS-RS client."""

from __future__ import annotations

import argparse
import logging
import sys

import dicom_ups_rs_client.ups_rs_client as _ups_rs_module
from dicom_ups_rs_client.cli_commands import (
    handle_cancel_request_command,
    handle_change_state_command,
    handle_create_command,
    handle_retrieve_command,
    handle_search_command,
    handle_subscribe_command,
    handle_unsubscribe_command,
    handle_update_command,
)
from dicom_ups_rs_client.serialization import CONTENT_TYPE_JSON, CONTENT_TYPE_XML


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
    parser.add_argument(
        "--server-flavor",
        choices=["standard", "dcm4chee"],
        default="standard",
        help=(
            "URL convention to use. 'standard' (default) follows PS3.18 strictly. "
            "'dcm4chee' adapts to dcm4chee-arc's non-conformant convention: requester AET "
            "as a path segment on state/cancelrequest, and Transaction UID in the request "
            "body on update."
        ),
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
        server_flavor=args.server_flavor,
    )

    try:
        # Execute the requested command
        if args.command == "create":
            handle_create_command(client, args)
        elif args.command == "retrieve":
            handle_retrieve_command(client, args)
        elif args.command == "search":
            handle_search_command(client, args)
        elif args.command == "update":
            handle_update_command(client, args)
        elif args.command == "change-state":
            handle_change_state_command(client, args)
        elif args.command == "request-cancel":
            handle_cancel_request_command(client, args)
        elif args.command == "subscribe":
            handle_subscribe_command(client, args)
        elif args.command == "unsubscribe":
            handle_unsubscribe_command(client, args)
    finally:
        # Ensure proper cleanup
        client.close()
