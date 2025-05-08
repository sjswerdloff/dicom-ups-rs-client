"""Tests for UPS-RS client integration scenarios combining multiple operations."""

import time
from unittest.mock import Mock

import pytest

from dicom_ups_rs_client.ups_rs_client import UPSRSClient, UPSState


def test_workitem_lifecycle(mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable) -> None:
    """
    Test a complete workitem lifecycle: create, retrieve, update, change state, complete.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_workitem: Sample workitem data
        response_factory: Factory for creating mock responses

    """
    # 1. Create a workitem
    workitem_uid = "1.2.3.4.5"

    # Configure mock response for create
    create_response = response_factory(
        status_code=201, json_data={"status": "Success"}, headers={"Content-Location": f"/workitems/{workitem_uid}"}
    )
    mock_ups_rs_client.session.add_response("POST", r"http://example.com/dicom-web/workitems", create_response)

    # Call create method
    success, result = mock_ups_rs_client.create_workitem(sample_workitem, workitem_uid)
    assert success is True

    # 2. Retrieve the workitem
    retrieve_response = response_factory(status_code=200, json_data=sample_workitem)
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", retrieve_response)

    # Call retrieve method
    success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)
    assert success is True
    assert result == sample_workitem

    # 3. Update the workitem
    update_data = {"00741204": {"vr": "LO", "Value": ["Updated Procedure Label"]}}

    update_response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response("PUT", f"http://example.com/dicom-web/workitems/{workitem_uid}", update_response)

    # Call update method (no transaction UID needed for SCHEDULED workitems)
    success, result = mock_ups_rs_client.update_workitem(workitem_uid, None, update_data)
    assert success is True

    # 4. Change state to IN PROGRESS
    in_progress_response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response(
        "PUT", f"http://example.com/dicom-web/workitems/{workitem_uid}/state", in_progress_response
    )

    # Call change_workitem_state method
    success, result = mock_ups_rs_client.change_workitem_state(workitem_uid, UPSState.IN_PROGRESS)
    assert success is True

    # Get the transaction UID generated for IN PROGRESS
    transaction_uid = result["transaction_uid"]
    assert transaction_uid is not None

    # 5. Complete the workitem
    complete_response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response(
        "PUT", f"http://example.com/dicom-web/workitems/{workitem_uid}/state", complete_response
    )

    # Call change_workitem_state method to complete
    success, result = mock_ups_rs_client.change_workitem_state(workitem_uid, UPSState.COMPLETED, transaction_uid)
    assert success is True


def test_subscription_and_notification(
    mock_ups_rs_client: UPSRSClient,
    sample_workitem: dict,
    sample_event_notification: dict,
    response_factory: callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test subscription and notification flow."""
    # Set AE title for subscription
    mock_ups_rs_client.aetitle = "TEST_AE"

    # 1. Subscribe to worklist
    subscribe_response = response_factory(
        status_code=201,
        json_data={"status": "Success"},
        headers={"Content-Location": "http://example.com/dicom-web/subscribers/TEST_AE"},
    )
    mock_ups_rs_client.session.add_response(
        "POST",
        r"http://example.com/dicom-web/workitems/1\.2\.840\.10008\.5\.1\.4\.34\.5/subscribers/TEST_AE",
        subscribe_response,
    )

    # Call subscribe method
    success, result = mock_ups_rs_client.subscribe_to_worklist()
    assert success is True
    assert "ws_url" in result
    mock_ups_rs_client.ws_url = result["ws_url"]

    # 2. Create a workitem (which should trigger a notification)
    workitem_uid = "1.2.3.4.5"

    create_response = response_factory(
        status_code=201, json_data={"status": "Success"}, headers={"Content-Location": f"/workitems/{workitem_uid}"}
    )
    mock_ups_rs_client.session.add_response("POST", r"http://example.com/dicom-web/workitems", create_response)

    # Set up the sample notification with the correct workitem UID
    notification = dict(sample_event_notification)
    notification["00001000"]["Value"] = [workitem_uid]

    # 3. Replace the _websocket_client method with our test version
    async def mock_websocket_client() -> None:
        """Mock implementation that just calls the callback with our notification."""
        try:
            # Directly call the callback with our notification
            mock_ups_rs_client.event_callback(notification)
            return True
        except Exception as e:
            mock_ups_rs_client.logger.error(f"Mock websocket error: {str(e)}")
            return False

    # Patch the method
    monkeypatch.setattr(mock_ups_rs_client, "_websocket_client", mock_websocket_client)

    # 4. Connect to WebSocket
    event_callback = Mock()
    result = mock_ups_rs_client.connect_websocket(event_callback)
    assert result is True

    # 5. Create the workitem
    success, result = mock_ups_rs_client.create_workitem(sample_workitem, workitem_uid)
    assert success is True

    # 6. Wait for event to be processed (thread startup)
    time.sleep(0.5)

    # 7. Check that the event callback was called with the notification
    event_callback.assert_called_once_with(notification)


def test_filtered_subscription_workflow(
    mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable
) -> None:
    """
    Test filtered subscription workflow.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_workitem: Sample workitem data
        response_factory: Factory for creating mock responses

    """
    # Set AE title for subscription
    mock_ups_rs_client.aetitle = "TEST_AE"

    # 1. Create a filtered subscription for only SCHEDULED workitems
    subscribe_response = response_factory(
        status_code=201,
        json_data={"status": "Success"},
        headers={"Content-Location": "http://example.com/dicom-web/subscribers/TEST_AE"},
    )
    mock_ups_rs_client.session.add_response(
        "POST",
        r"http://example.com/dicom-web/workitems/1\.2\.840\.10008\.5\.1\.4\.34\.5\.1/subscribers/TEST_AE\?filter=",
        subscribe_response,
    )

    # Call subscribe method with filter
    filter_params = {"00741000": "SCHEDULED"}
    success, result = mock_ups_rs_client.subscribe_to_filtered_worklist(filter_params)
    assert success is True

    # 2. Search for workitems matching the filter
    search_response = response_factory(status_code=200, json_data=[sample_workitem])
    mock_ups_rs_client.session.add_response("GET", r"http://example.com/dicom-web/workitems\?", search_response)

    # Call search method
    success, result = mock_ups_rs_client.search_workitems(match_parameters=filter_params)
    assert success is True
    assert isinstance(result, list)
    assert len(result) == 1

    # 3. Unsubscribe from filtered worklist
    unsubscribe_response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response(
        "DELETE",
        r"http://example.com/dicom-web/workitems/1\.2\.840\.10008\.5\.1\.4\.34\.5\.1/subscribers/TEST_AE\?filter=",
        unsubscribe_response,
    )

    # Call unsubscribe method
    success, result = mock_ups_rs_client.unsubscribe_from_filtered_worklist(filter_params)
    assert success is True


def test_cancellation_workflow(mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable) -> None:
    """
    Test workitem cancellation workflow.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_workitem: Sample workitem data
        response_factory: Factory for creating mock responses

    """
    # 1. Create a workitem
    workitem_uid = "1.2.3.4.5"

    create_response = response_factory(
        status_code=201, json_data={"status": "Success"}, headers={"Content-Location": f"/workitems/{workitem_uid}"}
    )
    mock_ups_rs_client.session.add_response("POST", r"http://example.com/dicom-web/workitems", create_response)

    # Call create method
    success, result = mock_ups_rs_client.create_workitem(sample_workitem, workitem_uid)
    assert success is True

    # 2. Request cancellation
    cancel_response = response_factory(status_code=202, json_data={"status": "Accepted"})
    mock_ups_rs_client.session.add_response(
        "POST", f"http://example.com/dicom-web/workitems/{workitem_uid}/cancelrequest", cancel_response
    )

    # Call request_cancellation method
    reason = "Test cancellation reason"
    contact_name = "Test User"
    contact_uri = "mailto:test@example.com"

    success, result = mock_ups_rs_client.request_cancellation(workitem_uid, reason, contact_name, contact_uri)
    assert success is True

    # 3. Retrieve the workitem to confirm cancellation
    # In a real system, the workitem would be canceled by the performing system
    # But for this test, we'll simulate the workitem being in CANCELED state

    canceled_workitem = dict(sample_workitem)
    canceled_workitem["00741000"]["Value"] = ["CANCELED"]

    retrieve_response = response_factory(status_code=200, json_data=canceled_workitem)
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", retrieve_response)

    # Call retrieve method
    success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)
    assert success is True
    assert result["00741000"]["Value"][0] == "CANCELED"


@pytest.mark.asyncio()
async def test_async_workflow(mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable) -> None:
    """Test asynchronous workflow combining multiple operations."""
    # 1. Create three workitems asynchronously
    workitem_uid1 = "1.2.3.4.5"
    workitem_uid2 = "2.3.4.5.6"
    workitem_uid3 = "3.4.5.6.7"

    # Create variations of the sample workitem with different UIDs
    workitem1 = dict(sample_workitem)
    workitem1["00080018"]["Value"] = [workitem_uid1]
    workitem1["00741204"]["Value"] = ["Procedure 1"]

    workitem2 = dict(sample_workitem)
    workitem2["00080018"]["Value"] = [workitem_uid2]
    workitem2["00741204"]["Value"] = ["Procedure 2"]

    workitem3 = dict(sample_workitem)
    workitem3["00080018"]["Value"] = [workitem_uid3]
    workitem3["00741204"]["Value"] = ["Procedure 3"]

    # Configure mock responses - use EXACT URLs instead of patterns
    # Create responses
    for uid, workitem in [(workitem_uid1, workitem1), (workitem_uid2, workitem2), (workitem_uid3, workitem3)]:
        # Create response - use a specific URL parameter to distinguish
        create_response = response_factory(
            status_code=201, json_data={"status": "Success"}, headers={"Content-Location": f"/workitems/{uid}"}
        )
        mock_ups_rs_client.session.add_response(
            "POST", f"http://example.com/dicom-web/workitems\\?workitem={uid}", create_response
        )

        # Retrieve response - use exact URL
        retrieve_response = response_factory(status_code=200, json_data=workitem)
        mock_ups_rs_client.session.add_response(
            "GET",
            f"http://example.com/dicom-web/workitems/{uid}$",  # The $ ensures exact match
            retrieve_response,
        )

    # Rest of the test remains the same...
