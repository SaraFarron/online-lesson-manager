import re

from aiogram import Bot
from aiogram.utils.deep_linking import create_start_link

_TEACHER_CODE_PATTERN = re.compile(r"t_[A-Z0-9]{6}")
_LEGACY_CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,56}")
_LEGACY_PAYLOAD_PATTERN = re.compile(r"teacher_([A-Za-z0-9_-]{1,56})")


def parse_teacher_start_payload(payload: str | None) -> str | None:
    if payload is None:
        return None
    if _TEACHER_CODE_PATTERN.fullmatch(payload) is not None:
        return payload
    legacy_match = _LEGACY_PAYLOAD_PATTERN.fullmatch(payload)
    if legacy_match is None:
        return None
    return legacy_match.group(1)


def format_teacher_start_payload(code: str) -> str:
    if _TEACHER_CODE_PATTERN.fullmatch(code) is not None:
        return code
    if _LEGACY_CODE_PATTERN.fullmatch(code) is not None:
        return f"teacher_{code}"
    raise ValueError("Teacher invitation code contains unsupported characters")


async def create_teacher_invitation(bot: Bot, code: str) -> str:
    return await create_start_link(bot, format_teacher_start_payload(code))
