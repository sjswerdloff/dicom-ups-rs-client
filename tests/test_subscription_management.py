"""Tests for UPS-RS client subscription management operations."""

from dicom_ups_rs_client.ups_rs_client import UPSRSClient


def test_subscribe_to_worklist(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test subscribing to the entire worklist.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Set AE title for subscription
    mock_ups_rs_client.aetitle = "TEST_AE"

    # Configure mock response
    response = response_factory(
        status_code=201,
        json_data={"status": "Success"},
        headers={"Content-Location": "http://example.com/dicom-web/subscribers/TEST_AE"},
    )
    mock_ups_rs_client.session.add_response(
        "POST", r"http://example.com/dicom-web/workitems/1\.2\.840\.10008\.5\.1\.4\.34\.5/subscribers/TEST_AE", response
    )

    # Call the method
    success, result = mock_ups_rs_client.subscribe_to_worklist()

    # Check response
    assert success is True
    assert "status" in result
    assert result["status"] == "Success"
    assert "content_location" in result
    assert result["content_location"] == "http://example.com/dicom-web/subscribers/TEST_AE"
    assert "ws_url" in result
    assert result["ws_url"] == "http://example.com/dicom-web/subscribers/TEST_AE"

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == "http://example.com/dicom-web/workitems/1.2.840.10008.5.1.4.34.5/subscribers/TEST_AE"


def test_subscribe_to_worklist_with_deletion_lock(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test subscribing to the worklist with deletion lock.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Set AE title for subscription
    mock_ups_rs_client.aetitle = "TEST_AE"

    # Configure mock response
    response = response_factory(
        status_code=201,
        json_data={"status": "Success"},
        headers={"Content-Location": "http://example.com/dicom-web/subscribers/TEST_AE"},
    )
    mock_ups_rs_client.session.add_response(
        "POST",
        r"http://example.com/dicom-web/workitems/1\.2\.840\.10008\.5\.1\.4\.34\.5/subscribers/TEST_AE\?deletionlock=true",
        response,
    )

    # Call the method with deletion_lock=True
    success, result = mock_ups_rs_client.subscribe_to_worklist(deletion_lock=True)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "POST"
    assert "deletionlock=true" in request["url"]


def test_subscribe_to_worklist_missing_aetitle(mock_ups_rs_client: UPSRSClient) -> None:
    """
    Test subscribing without an AE title.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client

    """
    # Ensure no AE title is set
    mock_ups_rs_client.aetitle = None

    # Call the method
    success, result = mock_ups_rs_client.subscribe_to_worklist()

    # Should fail without making a request
    assert success is False
    assert "AE Title is required for subscription operations" in result
    assert len(mock_ups_rs_client.session.requests) == 0


def test_subscribe_to_filtered_worklist(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test subscribing to a filtered worklist.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Set AE title for subscription
    mock_ups_rs_client.aetitle = "TEST_AE"

    # Configure mock response
    response = response_factory(
        status_code=201,
        json_data={"status": "Success"},
        headers={"Content-Location": "http://example.com/dicom-web/subscribers/TEST_AE"},
    )
    mock_ups_rs_client.session.add_response(
        "POST",
        r"http://example.com/dicom-web/workitems/1\.2\.840\.10008\.5\.1\.4\.34\.5\.1/subscribers/TEST_AE\?filter=",
        response,
    )

    # Call the method with filter parameters
    filter_params = {
        "00741000": "SCHEDULED",  # Procedure Step State
        "00741200": "HIGH",  # Scheduled Procedure Step Priority
    }
    success, result = mock_ups_rs_client.subscribe_to_filtered_worklist(filter_params)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "POST"
    assert "1.2.840.10008.5.1.4.34.5.1" in request["url"]  # Filtered worklist UID
    assert "filter=" in request["url"]

    # Check that filter parameters are in the URL
    filter_parts = request["url"].split("filter=")[1].split("&")[0].split(",")
    assert "00741000=SCHEDULED" in filter_parts
    assert "00741200=HIGH" in filter_parts


def test_subscribe_to_filtered_worklist_with_deletion_lock(
    mock_ups_rs_client: UPSRSClient, response_factory: callable
) -> None:
    """
    Test subscribing to a filtered worklist with deletion lock.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Set AE title for subscription
    mock_ups_rs_client.aetitle = "TEST_AE"

    # Configure mock response
    response = response_factory(
        status_code=201,
        json_data={"status": "Success"},
        headers={"Content-Location": "http://example.com/dicom-web/subscribers/TEST_AE"},
    )
    mock_ups_rs_client.session.add_response(
        "POST",
        r"http://example.com/dicom-web/workitems/1\.2\.840\.10008\.5\.1\.4\.34\.5\.1/subscribers/TEST_AE\?filter=.*&deletionlock=true",
        response,
    )

    # Call the method with filter parameters and deletion_lock=True
    filter_params = {"00741000": "SCHEDULED"}
    success, result = mock_ups_rs_client.subscribe_to_filtered_worklist(filter_params, deletion_lock=True)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert "deletionlock=true" in request["url"]


def test_subscribe_to_workitem(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test subscribing to a specific workitem.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Set AE title for subscription
    mock_ups_rs_client.aetitle = "TEST_AE"
    workitem_uid = "1.2.3.4.5"

    # Configure mock response
    response = response_factory(
        status_code=201,
        json_data={"status": "Success"},
        headers={"Content-Location": "http://example.com/dicom-web/subscribers/TEST_AE"},
    )
    mock_ups_rs_client.session.add_response(
        "POST", f"http://example.com/dicom-web/workitems/{workitem_uid}/subscribers/TEST_AE", response
    )

    # Call the method
    success, result = mock_ups_rs_client.subscribe_to_workitem(workitem_uid)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == f"http://example.com/dicom-web/workitems/{workitem_uid}/subscribers/TEST_AE"


def test_subscribe_to_workitem_with_deletion_lock(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test subscribing to a specific workitem with deletion lock.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Set AE title for subscription
    mock_ups_rs_client.aetitle = "TEST_AE"
    workitem_uid = "1.2.3.4.5"

    # Configure mock response
    response = response_factory(
        status_code=201,
        json_data={"status": "Success"},
        headers={"Content-Location": "http://example.com/dicom-web/subscribers/TEST_AE"},
    )
    mock_ups_rs_client.session.add_response(
        "POST", f"http://example.com/dicom-web/workitems/{workitem_uid}/subscribers/TEST_AE\\?deletionlock=true", response
    )

    # Call the method with deletion_lock=True
    success, result = mock_ups_rs_client.subscribe_to_workitem(workitem_uid, deletion_lock=True)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert "deletionlock=true" in request["url"]


def test_subscribe_to_workitem_invalid_uid(mock_ups_rs_client: UPSRSClient) -> None:
    """
    Test subscribing to a workitem with an invalid UID.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client

    """
    # Set AE title for subscription
    mock_ups_rs_client.aetitle = "TEST_AE"

    # Call the method with invalid UID
    success, result = mock_ups_rs_client.subscribe_to_workitem("invalid.uid")

    # Should fail validation without making a request
    assert success is False
    assert "Invalid DICOM UID format" in result
    assert len(mock_ups_rs_client.session.requests) == 0


def test_unsubscribe_from_worklist(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test unsubscribing from the entire worklist.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Set AE title for subscription
    mock_ups_rs_client.aetitle = "TEST_AE"

    # Configure mock response
    response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response(
        "DELETE", r"http://example.com/dicom-web/workitems/1\.2\.840\.10008\.5\.1\.4\.34\.5/subscribers/TEST_AE", response
    )

    # Call the method
    success, result = mock_ups_rs_client.unsubscribe_from_worklist()

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "DELETE"
    assert request["url"] == "http://example.com/dicom-web/workitems/1.2.840.10008.5.1.4.34.5/subscribers/TEST_AE"


def test_unsubscribe_from_filtered_worklist(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test unsubscribing from a filtered worklist.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Set AE title for subscription
    mock_ups_rs_client.aetitle = "TEST_AE"

    # Configure mock response
    response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response(
        "DELETE",
        r"http://example.com/dicom-web/workitems/1\.2\.840\.10008\.5\.1\.4\.34\.5\.1/subscribers/TEST_AE\?filter=",
        response,
    )

    # Call the method with filter parameters
    filter_params = {
        "00741000": "SCHEDULED",  # Procedure Step State
        "00741200": "HIGH",  # Scheduled Procedure Step Priority
    }
    success, result = mock_ups_rs_client.unsubscribe_from_filtered_worklist(filter_params)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "DELETE"
    assert "1.2.840.10008.5.1.4.34.5.1" in request["url"]  # Filtered worklist UID
    assert "filter=" in request["url"]

    # Check that filter parameters are in the URL
    filter_parts = request["url"].split("filter=")[1].split("&")[0].split(",")
    assert "00741000=SCHEDULED" in filter_parts
    assert "00741200=HIGH" in filter_parts


def test_unsubscribe_from_workitem(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test unsubscribing from a specific workitem.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Set AE title for subscription
    mock_ups_rs_client.aetitle = "TEST_AE"
    workitem_uid = "1.2.3.4.5"

    # Configure mock response
    response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response(
        "DELETE", f"http://example.com/dicom-web/workitems/{workitem_uid}/subscribers/TEST_AE", response
    )

    # Call the method
    success, result = mock_ups_rs_client.unsubscribe_from_workitem(workitem_uid)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "DELETE"
    assert request["url"] == f"http://example.com/dicom-web/workitems/{workitem_uid}/subscribers/TEST_AE"


def test_unsubscribe_error_handling(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test error handling during unsubscription.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Set AE title for subscription
    mock_ups_rs_client.aetitle = "TEST_AE"
    workitem_uid = "1.2.3.4.5"

    # Configure mock response with 404 Not Found
    response = response_factory(status_code=404, text="Subscription not found", reason="Not Found")
    mock_ups_rs_client.session.add_response(
        "DELETE", f"http://example.com/dicom-web/workitems/{workitem_uid}/subscribers/TEST_AE", response
    )

    # Call the method
    success, result = mock_ups_rs_client.unsubscribe_from_workitem(workitem_uid)

    # Should fail
    assert success is False
    assert "Subscription not found" in result


def test_subscription_missing_aetitle(mock_ups_rs_client: UPSRSClient) -> None:
    """
    Test that all subscription methods properly check for AE title.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client

    """
    # Ensure no AE title is set
    mock_ups_rs_client.aetitle = None

    # Test all subscription methods
    workitem_uid = "1.2.3.4.5"
    filter_params = {"00741000": "SCHEDULED"}

    # Subscribe methods
    success, result = mock_ups_rs_client.subscribe_to_worklist()
    assert success is False
    assert "AE Title is required" in result

    success, result = mock_ups_rs_client.subscribe_to_filtered_worklist(filter_params)
    assert success is False
    assert "AE Title is required" in result

    success, result = mock_ups_rs_client.subscribe_to_workitem(workitem_uid)
    assert success is False
    assert "AE Title is required" in result

    # Unsubscribe methods
    success, result = mock_ups_rs_client.unsubscribe_from_worklist()
    assert success is False
    assert "AE Title is required" in result

    success, result = mock_ups_rs_client.unsubscribe_from_filtered_worklist(filter_params)
    assert success is False
    assert "AE Title is required" in result

    success, result = mock_ups_rs_client.unsubscribe_from_workitem(workitem_uid)
    assert success is False
    assert "AE Title is required" in result
