# Task: Add previous/next week navigation to the Telegram schedule

## Execution status

Implemented and covered by the recorded passing `uv run pytest -q tests`
schedule suite, bot Ruff/format checks, and `uv run mypy telegram` documented
in [SCHEDULE-VIEWING-PLAN.md](./SCHEDULE-VIEWING-PLAN.md). No migration was
required.

**Domain:** `telegram/` (existing)
**Type:** bot

## Context

The [schedule-viewing section](../../README.md#просмотр-расписания) and [Feature Status](../../AGENTS.md#feature-status-vs-product-spec) describe live Telegram views for Today and This week. At the time this task was written, the week view always loaded the viewer's current Monday–Sunday week. Navigating to other weeks was outside the completed [schedule-viewing plan](./SCHEDULE-VIEWING-PLAN.md).

Both teachers and students should be able to browse earlier and later weeks. The authenticated backend week endpoint already accepts an optional viewer-local `date` and returns the Monday–Sunday week containing it; no backend schema or route change is needed.

## What to do

- Extend `ScheduleDialogState` in `telegram/schedule_dialogs.py` to retain the requested local date for the displayed week, and validate it along with the existing dialog state.
- Add previous-week and next-week actions to the week schedule view. Each action should move exactly seven calendar days from the displayed week, request that week from the backend, and edit the active Telegram message. Keep the existing within-week page controls distinct from week navigation.
- Pass the requested date to `read_schedule_week` in `telegram/api.py` through the existing `requested_date` parameter. Use the backend response's first day as the authoritative week start, so navigation remains Monday-aligned in the viewer's timezone.
- Keep Refresh on the currently displayed week, reload its latest data, and return to the first message page. Switching to Today or reopening This week should continue to load the current period.
- Show previous/next week controls to both `TEACHER` and `STUDENT` users; retain existing role-specific formatting and authorization. Preserve same-message editing, explicit backend error handling, and page-size behavior.
- Add focused unit/integration coverage in `tests/test_bot_schedule_dialogs.py` and/or `tests/test_bot_schedule_integration.py` for both roles, week stepping, refresh behavior, and API date parameters.
- Update the schedule-viewing description in `README.md` and the status notes in `AGENTS.md` to reflect navigation across weeks.

## Reuse

- `telegram.api.BackendClient.read_schedule_week(..., requested_date=...)` already serializes the date as the backend query parameter.
- `ScheduleResponse.days[0].date` is the validated Monday of the returned week; use it instead of deriving week bounds from the system clock.
- Reuse the existing `ScheduleDialogState`, `_load_schedule`, `_refresh`, and `ShowMode.EDIT` patterns in `telegram/schedule_dialogs.py`.
- Reuse current role-scoped backend schedule reads and `format_schedule_pages`; do not add alternate teacher/student query paths or an offline cache.

## Acceptance Criteria

- [x] Teachers and students can navigate backward and forward by one full Monday–Sunday week.
- [x] The viewed week is loaded using the existing authenticated week endpoint with the requested date; existing role authorization and formatting remain unchanged.
- [x] Week navigation and refresh edit the current message. Refresh keeps the selected week, fetches fresh data, and returns to its first page.
- [x] Today and This week entry points retain their existing behavior; within-week pagination remains separate and works.
- [x] Backend errors remain visible through the existing explicit error UI; a failed week read is not rendered as an empty schedule.
- [x] Focused bot tests cover teacher and student navigation, both directions, correct requested dates, and refresh on a non-current week.
- [x] `uv run ruff check telegram tests`, `uv run ruff format --check telegram tests`, and `uv run mypy telegram` pass; relevant bot schedule tests pass.
- [x] README and Feature Status documentation describe the new behavior. No migration is required.

## Out of scope

- Arbitrary date-picker or free-form date entry.
- Changes to backend schedule authorization, response shape, event visibility, or date-range limits.
- Offline schedule caching, schedule mutations, or changes to Today behavior.

## Suitable for

AI agent; the behavior is bounded to the existing Telegram schedule dialog and its API client support.
