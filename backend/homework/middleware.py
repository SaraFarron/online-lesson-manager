from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.homework.constants import (
    MAX_ATTACHMENT_BYTES,
    MAX_MULTIPART_OVERHEAD_BYTES,
)

_RESPONSE_BODY = b'{"detail":"Upload exceeds the maximum request size."}'


class HomeworkUploadLimitMiddleware:
    def __init__(self, app: ASGIApp, upload_path: str) -> None:
        self.app = app
        self.upload_path = upload_path
        self.max_request_bytes = MAX_ATTACHMENT_BYTES + MAX_MULTIPART_OVERHEAD_BYTES

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] != self.upload_path
        ):
            await self.app(scope, receive, send)
            return

        content_length = next(
            (
                value
                for name, value in scope.get("headers", [])
                if name.lower() == b"content-length"
            ),
            None,
        )
        if content_length is not None:
            try:
                if int(content_length) > self.max_request_bytes:
                    await self._send_too_large(send)
                    return
            except ValueError:
                pass

        received_bytes = 0
        response_started = False
        suppress_downstream_response = False

        async def limited_receive() -> Message:
            nonlocal received_bytes, response_started, suppress_downstream_response
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_request_bytes:
                    if not response_started:
                        response_started = True
                        suppress_downstream_response = True
                        await self._send_too_large(send)
                    return {"type": "http.disconnect"}
            return message

        async def track_send(message: Message) -> None:
            nonlocal response_started
            if suppress_downstream_response:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, track_send)
        except ClientDisconnect:
            if not suppress_downstream_response:
                raise

    async def _send_too_large(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_RESPONSE_BODY)).encode("ascii")),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": _RESPONSE_BODY,
            }
        )
