from collections.abc import AsyncIterable, AsyncIterator, Callable

from .errors import HomeworkAttachmentTooLarge
from .models.homework import MAX_HOMEWORK_ATTACHMENT_BYTES, MAX_HOMEWORK_ATTACHMENTS

__all__ = [
    "MAX_HOMEWORK_ATTACHMENT_BYTES",
    "MAX_HOMEWORK_ATTACHMENTS",
    "MAX_HOMEWORK_ATTACHMENT_TOTAL_BYTES",
    "HOMEWORK_TRANSFER_TIMEOUT_SECONDS",
    "MAX_HOMEWORK_ERROR_BODY_BYTES",
    "_safe_multipart_filename",
    "_multipart_file_stream",
]


MAX_HOMEWORK_ATTACHMENT_TOTAL_BYTES = 50 * 1024 * 1024


HOMEWORK_TRANSFER_TIMEOUT_SECONDS = 120


MAX_HOMEWORK_ERROR_BODY_BYTES = 64 * 1024


def _safe_multipart_filename(value: str) -> str:
    basename = value.replace("\\", "/").rsplit("/", 1)[-1]
    safe = "".join(
        character
        for character in basename
        if character.isprintable() and character not in {'"', "\\"}
    )
    return safe[:128] or "attachment"


async def _multipart_file_stream(
    source_factory: Callable[[], AsyncIterable[bytes]],
    file_name: str,
    boundary: str,
) -> AsyncIterator[bytes]:
    safe_name = _safe_multipart_filename(file_name)
    yield (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    byte_count = 0
    async for chunk in source_factory():
        if not isinstance(chunk, bytes):
            raise TypeError("Homework attachment streams must yield bytes")
        if not chunk:
            continue
        byte_count += len(chunk)
        if byte_count > MAX_HOMEWORK_ATTACHMENT_BYTES:
            raise HomeworkAttachmentTooLarge()
        yield chunk
    yield f"\r\n--{boundary}--\r\n".encode("ascii")
