"""Custom exceptions for the DICOM UPS-RS client."""


class UPSRSError(Exception):
    """Base exception class for UPS-RS client errors."""

    pass


class UPSRSResponseError(UPSRSError):
    """Exception raised for errors in the response from the UPS-RS server."""

    def __init__(self, message: str, status_code: int, response_text: str | None = None) -> None:
        """
        Initialize the exception.

        Args:
            message: Human-readable error message.
            status_code: HTTP status code received from the server.
            response_text: Optional raw response body for debugging.

        """
        self.status_code = status_code
        self.response_text = response_text
        super().__init__(message)


class UPSRSRequestError(UPSRSError):
    """Exception raised for errors in making requests to the UPS-RS server."""

    pass


class UPSRSValidationError(UPSRSError):
    """Exception raised for validation errors in client inputs."""

    pass
