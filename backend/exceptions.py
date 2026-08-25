"""Global exception types.

Domain-specific exceptions live inside their own domain package.
Only cross-cutting concerns belong here.
"""

from fastapi import HTTPException, status


class NotFound(HTTPException):
    def __init__(self, detail: str = "Resource not found") -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class BadRequest(HTTPException):
    def __init__(self, detail: str = "Bad request") -> None:
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


class Conflict(HTTPException):
    def __init__(self, detail: str = "Conflict") -> None:
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)
