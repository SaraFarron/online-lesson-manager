import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import service as auth_service
from backend.auth.models import TeacherProfile
from backend.database import SessionFactory

_MAX_ALLOCATION_ATTEMPTS = 100


async def _profiles_to_rotate(
    session: AsyncSession, *, lock: bool = False
) -> list[TeacherProfile]:
    statement = select(TeacherProfile).order_by(TeacherProfile.id)
    if lock:
        statement = statement.with_for_update()
    profiles = list((await session.scalars(statement)).all())
    return [
        profile
        for profile in profiles
        if not auth_service.is_generated_teacher_code(profile.code)
    ]


async def count_teacher_codes_to_rotate(session: AsyncSession) -> int:
    return len(await _profiles_to_rotate(session))


async def rotate_teacher_codes(session: AsyncSession) -> int:
    profiles = await _profiles_to_rotate(session, lock=True)
    if not profiles:
        return 0

    occupied_codes = set((await session.scalars(select(TeacherProfile.code))).all())
    replacements: list[tuple[TeacherProfile, str]] = []
    for profile in profiles:
        for _ in range(_MAX_ALLOCATION_ATTEMPTS):
            code = auth_service.generate_teacher_code()
            if code not in occupied_codes:
                occupied_codes.add(code)
                replacements.append((profile, code))
                break
        else:
            raise RuntimeError(
                "Could not allocate a unique teacher code; no codes were changed."
            )

    for profile, code in replacements:
        profile.code = code
    await session.flush()
    return len(replacements)


async def _preview_count() -> int:
    async with SessionFactory() as session:
        return await count_teacher_codes_to_rotate(session)


async def _apply_rotation(expected_count: int) -> int:
    async with SessionFactory() as session, session.begin():
        actual_count = await rotate_teacher_codes(session)
        if actual_count != expected_count:
            raise RuntimeError(
                "The affected teacher-code count changed after confirmation. "
                "The transaction was rolled back; run the command again."
            )
        return actual_count


def _write(message: str) -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replace nonconforming teacher invitation codes."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    count = asyncio.run(_preview_count())
    if args.dry_run:
        _write(f"Dry run: {count} teacher code(s) need replacement; no writes made.")
        return
    if count == 0:
        _write("No teacher codes need replacement.")
        return

    _write(f"Preview: {count} teacher code(s) will be replaced.")
    _write(
        "Existing shared invitation links using those codes will stop working. "
        "No old-code aliases will be created."
    )
    confirmation = input(f"Type ROTATE {count} to apply this change: ")
    if confirmation != f"ROTATE {count}":
        _write("Cancelled; no teacher codes were changed.")
        sys.exit(1)

    changed = asyncio.run(_apply_rotation(count))
    _write(f"Replaced {changed} teacher code(s).")


if __name__ == "__main__":
    main()
