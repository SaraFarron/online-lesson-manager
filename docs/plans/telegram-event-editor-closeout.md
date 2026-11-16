# Telegram event-editor consistency — Closeout plan

## Goal

Finish and verify the already approved event-editor consistency work without reimplementing completed tasks.

**Status:** Planned continuation of [telegram-event-editor-consistency](./telegram-event-editor-consistency.md), not a new UX contract.

## Scope (README.md sections covered)

[Telegram interface](../../README.md#интерфейс-бота) and [schedule changes](../../README.md#изменение-расписания): weekly breaks, default-first forms, focused edits, accepted-input cleanup and rollback/recovery.

## Baseline and execution rules

- Original Task 01 records schema/SQLite work complete but PostgreSQL unavailable. A recent open-ended-break commit does not establish that Tasks 02–09 are all implemented.
- First use the inventory from [completion reconciliation](./completion-record-reconciliation.md), Task 00. Skip verified implementation; retain missing verification.
- The original owner-approved contract remains authoritative, including the one-year open-ended write-validation window, no implicit later cancellation, finite vacation ranges and unchanged role permissions.
- Old file references have moved: use [break dialogs](../../telegram/dialogs/breaks.py), [vacation dialogs](../../telegram/dialogs/vacations.py), [shared input cleanup](../../telegram/shared/date_cleanup.py), [shared inputs](../../telegram/shared/inputs.py), [typed API](../../telegram/api/) and [event mutations](../../backend/events/mutations/).
- Common checks: focused real-DB/bot tests, applicable `uv run ruff check backend telegram tests`, touched-file format checks, `uv run mypy backend` and `uv run mypy telegram`. Use isolated storage/DBs and recording transport; no live deletion of chat history. Changes to schema require static reversible `YYYY-MM-DD_slug.py` migrations and both DB checks.

## Task 00: Map original tasks to current implementation

**Domain:** Existing plan/code/tests. **Type:** verification. **Dependencies:** Reconciliation inventory.

**Context:** Original paths/status are older than the refactor and latest commits.

**What to do:** Map original Tasks 01–09 to current files/tests and distinguish implemented, verification-only and defective criteria. Record the mapping in the original plan. Preserve its approved examples and original task IDs.

**Reuse:** Reconciliation evidence, [original plan](./telegram-event-editor-consistency.md), package refactoring record.

**Acceptance Criteria:** [ ] Every original task has a current owner/check. [ ] No duplicate backlog or changed policy is introduced.

**Out of scope:** New UI redesign. **Suitable for:** AI agent.

## Task 01: Close weekly-break backend and typed-client contract

**Domain:** Existing event/break backend and bot API models. **Type:** service/schema/client. **Dependencies:** 00.

**Context:** Original Tasks 01–03 must agree on null cutoffs, bounded validation and destructive confirmation.

**What to do:** Verify/finish genuine null weekly-break cutoffs across DB, schemas, create/update services, management responses and typed client. Preserve nonweekly restrictions and finite intervals. Test before/at/after the exclusive anniversary, leap-day/DST and changed preview tokens; no displacement outside the admitted window. Complete PostgreSQL migration verification on an approved disposable DB or report blocked.

**Reuse:** `open_ended_validation_cutoff`, existing bounded expansion, mutation serialization and displacement previews.

**Acceptance Criteria:** [ ] Original Tasks 01–03 meet exact acceptance criteria on supported DBs/client, or named external blockers remain explicit. [ ] No sentinel dates or infinite-horizon safety claim.

**Out of scope:** General recurrence changes or restoring canceled lessons. **Suitable for:** AI agent.

## Task 02: Close break creation and focused-edit UI

**Domain:** Existing Telegram break dialogs/shared helpers. **Type:** bot. **Dependencies:** 01.

**Context:** Original Tasks 04–06 cover cleanup, No end default and draft-preserving updates.

**What to do:** Finish only missing behavior: delete exactly accepted time/date messages, default weekly creation to No end, seed updates from fresh detail, preserve cutoff/interval through unrelated edits, and restore field values on Back. Invalidate stale interval resolutions/displacement previews after relevant changes. Retain safe unknown-write reconciliation and leading escaped feedback.

**Reuse:** Current break dialogs, `delete_accepted_input_message`, lesson field-editor patterns.

**Acceptance Criteria:** [ ] Finite/open weekly and legacy interval regression flows persist correct payloads. [ ] Invalid input stays; cleanup failure shows attention without another mutation. [ ] Common bot checks pass.

**Out of scope:** Assignee transfer or automatic-break editing. **Suitable for:** AI agent.

## Task 03: Close vacation default/edit consistency

**Domain:** Existing Telegram vacation dialogs. **Type:** bot. **Dependencies:** 00.

**Context:** Original Tasks 07–08 specify personal-first teacher defaults and focused review edits.

**What to do:** Verify/finish personal default with optional own-student picker, reset transient state between drafts, return focused changes to review, restore field edits on Back, and clean accepted time input. Preserve mandatory finite vacation endpoints, student self-only scope and inclusive UI end dates.

**Reuse:** Existing vacation interval resolution, picker, input cleanup and draft patterns.

**Acceptance Criteria:** [ ] Teacher creation avoids roster fetch unless requested. [ ] Repeated creation and start/end edits preserve unrelated fields; DST/cross-year, permission and cancel tests pass.

**Out of scope:** Recurring vacations or new event permissions. **Suitable for:** AI agent.

## Task 04: Verify original Task 09 and close both records

**Domain:** Integration tests/docs. **Type:** verification/documentation. **Dependencies:** 02, 03.

**Context:** Individual form checks do not prove cross-flow/restart consistency.

**What to do:** Execute the original verification matrix through real dispatcher/backend for lessons, breaks, vacations and occurrence moves. Assert exact UTC payloads, persisted rows, message cleanup/edit IDs, stale callbacks and no out-of-window displacement. Update the original plan and reconciliation handoff; report unavailable PostgreSQL/live checks separately.

**Reuse:** Existing bot break/vacation/lesson integration tests and original Task 09.

**Acceptance Criteria:** [ ] Original Tasks 01–09 have evidence-backed final status. [ ] Current README/AGENTS describe the verified recurrence/UI contract consistently.

**Out of scope:** Notifications, profiles, cache and HTMX. **Suitable for:** AI agent; human for optional live UX.

## Open questions

No new product decision is needed unless current behavior conflicts with the original approved contract. Stop for approval rather than selecting a different contract.
