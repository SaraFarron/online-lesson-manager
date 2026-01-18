import json
from collections.abc import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class ResponseWrapperMiddleware(BaseHTTPMiddleware):
    """Middleware that wraps all responses in {success, data/error} format."""

    # Paths to exclude from wrapping (OpenAPI, docs, etc.)
    EXCLUDED_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

    def _should_wrap(self, request: Request) -> bool:
        """Check if the request path should be wrapped."""
        path = request.url.path
        # Check exact match or if path ends with excluded paths
        for excluded in self.EXCLUDED_PATHS:
            if path.endswith(excluded):
                return False
        return True

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # Skip excluded paths (OpenAPI, docs)
        if not self._should_wrap(request):
            return response

        # Skip non-JSON responses (like file downloads, redirects)
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        # Read and parse the response body
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        # Handle empty responses (204 No Content)
        if not body:
            wrapped = {"success": True, "data": None}
            return JSONResponse(
                content=wrapped,
                status_code=response.status_code,
            )

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return Response(
                content=body,
                status_code=response.status_code,
                media_type=response.media_type,
            )

        # Wrap based on status code
        if 200 <= response.status_code < 400:
            wrapped = {"success": True, "data": data}
        else:
            wrapped = {"success": False, "error": data}

        return JSONResponse(
            content=wrapped,
            status_code=response.status_code,
        )
