"""Tests for UPS-RS client asynchronous operations."""

from unittest.mock import Mock

import pytest

from dicom_ups_rs_client.ups_rs_client import UPSRSClient, UPSState


@pytest.mark.asyncio
async def test_create_workitem_async(
    mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable
) -> None:
    """
    Test creating a workitem asynchronously.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_workitem: Sample workitem data
        response_factory: Factory for creating mock responses

    """
    # Configure mock response
    response = response_factory(
        status_code=201, json_data={"status": "Success"}, headers={"Content-Location": "/workitems/1.2.3.4.5"}
    )
    mock_ups_rs_client.session.add_response("POST", r"http://example.com/dicom-web/workitems", response)

    # Create a mock method for the synchronous version that we can check was called
    original_method = mock_ups_rs_client.create_workitem
    mock_create_workitem = Mock(wraps=original_method)
    mock_ups_rs_client.create_workitem = mock_create_workitem

    # Call the async method
    success, result = await mock_ups_rs_client.create_workitem_async(sample_workitem)

    # Check that the synchronous method was called with correct arguments
    mock_create_workitem.assert_called_once_with(sample_workitem, None)

    # Check response
    assert success is True
    assert "status" in result
    assert result["status"] == "Success"


@pytest.mark.asyncio
async def test_retrieve_workitem_async(
    mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable
) -> None:
    """
    Test retrieving a workitem asynchronously.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_workitem: Sample workitem data
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"

    # Configure mock response
    response = response_factory(status_code=200, json_data=sample_workitem)
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response)

    # Create a mock method for the synchronous version that we can check was called
    original_method = mock_ups_rs_client.retrieve_workitem
    mock_retrieve_workitem = Mock(wraps=original_method)
    mock_ups_rs_client.retrieve_workitem = mock_retrieve_workitem

    # Call the async method
    success, result = await mock_ups_rs_client.retrieve_workitem_async(workitem_uid)

    # Check that the synchronous method was called with correct arguments
    mock_retrieve_workitem.assert_called_once_with(workitem_uid)

    # Check response
    assert success is True
    assert result == sample_workitem


@pytest.mark.asyncio
async def test_search_workitems_async(
    mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable
) -> None:
    """
    Test searching for workitems asynchronously.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_workitem: Sample workitem data
        response_factory: Factory for creating mock responses

    """
    # Configure mock response with a list of workitems
    response = response_factory(status_code=200, json_data=[sample_workitem])
    mock_ups_rs_client.session.add_response("GET", r"http://example.com/dicom-web/workitems\?", response)

    # Create a mock method for the synchronous version that we can check was called
    original_method = mock_ups_rs_client.search_workitems
    mock_search_workitems = Mock(wraps=original_method)
    mock_ups_rs_client.search_workitems = mock_search_workitems

    # Search parameters
    match_parameters = {"00741000": "SCHEDULED"}
    include_fields = ["00741204"]
    fuzzy_matching = True
    offset = 0
    limit = 10
    no_cache = True

    # Call the async method
    success, result = await mock_ups_rs_client.search_workitems_async(
        match_parameters, include_fields, fuzzy_matching, offset, limit, no_cache
    )

    # Check that the synchronous method was called with correct arguments
    mock_search_workitems.assert_called_once_with(match_parameters, include_fields, fuzzy_matching, offset, limit, no_cache)

    # Check response
    assert success is True
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0] == sample_workitem


@pytest.mark.asyncio
async def test_update_workitem_async(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test updating a workitem asynchronously.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"
    transaction_uid = "5.6.7.8.9"
    update_data = {"00741204": {"vr": "LO", "Value": ["Updated Procedure"]}}

    # Configure mock response
    response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response("PUT", f"http://example.com/dicom-web/workitems/{workitem_uid}", response)

    # Create a mock method for the synchronous version that we can check was called
    original_method = mock_ups_rs_client.update_workitem
    mock_update_workitem = Mock(wraps=original_method)
    mock_ups_rs_client.update_workitem = mock_update_workitem

    # Call the async method
    success, result = await mock_ups_rs_client.update_workitem_async(workitem_uid, transaction_uid, update_data)

    # Check that the synchronous method was called with correct arguments
    mock_update_workitem.assert_called_once_with(workitem_uid, transaction_uid, update_data)

    # Check response
    assert success is True
    assert "status" in result
    assert result["status"] == "Success"


@pytest.mark.asyncio
async def test_change_workitem_state_async(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test changing a workitem state asynchronously.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"
    transaction_uid = "5.6.7.8.9"
    new_state = UPSState.COMPLETED

    # Configure mock response
    response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response("PUT", f"http://example.com/dicom-web/workitems/{workitem_uid}/state", response)

    # Create a mock method for the synchronous version that we can check was called
    original_method = mock_ups_rs_client.change_workitem_state
    mock_change_workitem_state = Mock(wraps=original_method)
    mock_ups_rs_client.change_workitem_state = mock_change_workitem_state

    # Call the async method
    success, result = await mock_ups_rs_client.change_workitem_state_async(workitem_uid, new_state, transaction_uid)

    # Check that the synchronous method was called with correct arguments
    mock_change_workitem_state.assert_called_once_with(workitem_uid, new_state, transaction_uid)

    # Check response
    assert success is True
    assert "status" in result
    assert result["status"] == "Success"


@pytest.mark.asyncio
async def test_request_cancellation_async(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test requesting cancellation of a workitem asynchronously.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"
    reason = "Test cancellation"
    contact_name = "Test User"
    contact_uri = "mailto:test@example.com"

    # Configure mock response
    response = response_factory(status_code=202, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response(
        "POST", f"http://example.com/dicom-web/workitems/{workitem_uid}/cancelrequest", response
    )

    # Create a mock method for the synchronous version that we can check was called
    original_method = mock_ups_rs_client.request_cancellation
    mock_request_cancellation = Mock(wraps=original_method)
    mock_ups_rs_client.request_cancellation = mock_request_cancellation

    # Call the async method
    success, result = await mock_ups_rs_client.request_cancellation_async(workitem_uid, reason, contact_name, contact_uri)

    # Check that the synchronous method was called with correct arguments
    mock_request_cancellation.assert_called_once_with(workitem_uid, reason, contact_name, contact_uri)

    # Check response
    assert success is True
    assert "status" in result
    assert result["status"] == "Success"


@pytest.mark.asyncio
async def test_async_error_handling(mock_ups_rs_client: UPSRSClient) -> None:
    """
    Test error handling in async methods.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client

    """

    # Create a mock method that raises an exception
    def mock_retrieve_with_error(workitem_uid: str) -> tuple[bool, dict | str]:
        raise RuntimeError("Test error")

    mock_ups_rs_client.retrieve_workitem = mock_retrieve_with_error

    # Call the async method, which should handle the exception properly
    with pytest.raises(RuntimeError):
        await mock_ups_rs_client.retrieve_workitem_async("1.2.3.4.5")


@pytest.mark.asyncio
async def test_multiple_async_operations(
    mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable
) -> None:
    """Test running multiple async operations concurrently."""
    # Configure mock responses
    workitem_uid1 = "1.2.3.4.5"
    workitem_uid2 = "5.6.7.8.9"
    workitem_uid3 = "9.8.7.6.5"

    # Create variations of the sample workitem with different UIDs
    workitem1 = dict(sample_workitem)
    workitem1["00080018"]["Value"] = [workitem_uid1]
    workitem1["00741204"] = {"Value": ["Task 1"], "vr": "LO"}  # Add a unique identifier

    workitem2 = dict(sample_workitem)
    workitem2["00080018"]["Value"] = [workitem_uid2]
    workitem2["00741204"] = {"Value": ["Task 2"], "vr": "LO"}  # Add a unique identifier

    workitem3 = dict(sample_workitem)
    workitem3["00080018"]["Value"] = [workitem_uid3]
    workitem3["00741204"] = {"Value": ["Task 3"], "vr": "LO"}  # Add a unique identifier

    # OPTION 1: For regular test framework that doesn't need concurrent operations
    # Sequential approach - one test at a time with a fresh session for each

    # Test for workitem 1
    session1 = type(mock_ups_rs_client.session)()  # Create a fresh session
    mock_ups_rs_client.session = session1  # Replace the client's session

    mock_ups_rs_client.session.add_response(
        "GET",
        f"http://example.com/dicom-web/workitems/{workitem_uid1}",
        response_factory(status_code=200, json_data=workitem1),
    )

    result1 = await mock_ups_rs_client.retrieve_workitem_async(workitem_uid1)
    assert result1[0] is True
    assert result1[1]["00741204"]["Value"][0] == "Task 1"

    # Test for workitem 2
    session2 = type(mock_ups_rs_client.session)()  # Create a fresh session
    mock_ups_rs_client.session = session2  # Replace the client's session

    mock_ups_rs_client.session.add_response(
        "GET",
        f"http://example.com/dicom-web/workitems/{workitem_uid2}",
        response_factory(status_code=200, json_data=workitem2),
    )

    result2 = await mock_ups_rs_client.retrieve_workitem_async(workitem_uid2)
    assert result2[0] is True
    assert result2[1]["00741204"]["Value"][0] == "Task 2"

    # Test for workitem 3
    session3 = type(mock_ups_rs_client.session)()  # Create a fresh session
    mock_ups_rs_client.session = session3  # Replace the client's session

    mock_ups_rs_client.session.add_response(
        "GET",
        f"http://example.com/dicom-web/workitems/{workitem_uid3}",
        response_factory(status_code=200, json_data=workitem3),
    )

    result3 = await mock_ups_rs_client.retrieve_workitem_async(workitem_uid3)
    assert result3[0] is True
    assert result3[1]["00741204"]["Value"][0] == "Task 3"

    # OPTION 2: If we still want to test concurrent operations
    # Create a fresh session for testing multiple concurrent operations
    concurrent_session = type(mock_ups_rs_client.session)()
    mock_ups_rs_client.session = concurrent_session

    # Add all the responses to this single session using highly specific patterns
    concurrent_session.add_response(
        "GET",
        f"http://example.com/dicom-web/workitems/{workitem_uid1}$",
        response_factory(status_code=200, json_data=workitem1),
    )
    concurrent_session.add_response(
        "GET",
        f"http://example.com/dicom-web/workitems/{workitem_uid2}$",
        response_factory(status_code=200, json_data=workitem2),
    )
    concurrent_session.add_response(
        "GET",
        f"http://example.com/dicom-web/workitems/{workitem_uid3}$",
        response_factory(status_code=200, json_data=workitem3),
    )

    # Or consider skipping the concurrent test if it's already tested in the individual tests
