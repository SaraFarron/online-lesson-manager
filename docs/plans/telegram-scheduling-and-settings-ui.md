# Telegram scheduling and settings UI — Plan

## Goal

Teachers and students can manage their authorized lessons and vacations, teachers can manage manual breaks, and both roles can edit saved preferences from Telegram without using Swagger.

**Status:** Tasks 00–08 approved/verified; Tasks 09–11 are pending.

## Scope (README.md sections covered)

- [Bot interface](../../README.md#интерфейс-бота): flexible local date/time input and edit-in-place navigation.
- [Settings](../../README.md#старт): post-onboarding editing of existing teacher/student preferences.
- [Schedule changes](../../README.md#изменение-расписания): create, reschedule, cancel, and delete; teacher student selection; explicit manual-break displacement confirmation.
- [Schedule viewing](../../README.md#просмотр-расписания): enter management screens from the existing live day/week views.

## Baseline and reusable pieces

- [Booking-policy plan](./scheduling-policy-and-plan-reconciliation.md) and [AGENTS.md](../../AGENTS.md#feature-status-vs-product-spec) describe implemented work windows, duration authorization, daily/consecutive limits, and database-backed mutation serialization. Do not reimplement these in the bot.
- [Lesson routes](../../backend/lessons/router.py), [break routes](../../backend/breaks/router.py), and [vacation routes](../../backend/vacations/router.py) provide mutations. They do not currently provide the authorized detail/list reads needed for editing. Recurring lesson/break updates accept full recurrence data; vacations have one-off routes only.
- [Schedule reads](../../backend/schedule/service.py) intentionally hide vacation-suppressed occurrences. Their response is a display snapshot, not an editable series representation: it lacks subject IDs, recurrence metadata, and cancellation lists. Do not repurpose it as an unrestricted management API.
- [Auth routes](../../backend/auth/router.py) expose owner-only profile reads/updates and self PATCH, plus a bounded active-student picker for the authenticated teacher; the general users list remains superuser-only. Do not relax owner-only profile access to populate the picker.
- [BackendClient](../../telegram/api.py) supports onboarding, account/profile reads, and schedules. Its generic response parsing expects JSON and its error mapping discards structured backend details; add explicit mutation/204/error support rather than displaying arbitrary response bodies.
- Reuse [dialogs](../../telegram/dialogs.py), [onboarding parsers/widgets](../../telegram/onboarding.py), [clock/timezone parsers](../../telegram/inputs.py), [schedule dialogs](../../telegram/schedule_dialogs.py), and [runtime isolation/edit middleware](../../telegram/runtime.py).
- Backend authorization remains in `resolve_teacher_id`, `assert_can_assign_user`, `assert_owns_event`, and `assert_teacher_actor` in [event utilities](../../backend/events/utils.py). The existing generic event dependency is not a substitute for domain-specific dependencies on live routes.

## Dependencies and execution order

| Task | Prerequisites |
|---|---|
| 00 Approve UI/API contract | None |
| 01 Teacher student-picker read | 00 |
| 02 Authorized event-management reads | 00 |
| 03 Atomic occurrence cancel/reschedule operations | 00, 02 |
| 04 Typed bot reads/writes/errors | 01, 02, 03 |
| 05 Local date/time input | 00 |
| 06 Lesson creation screens | 04, 05 |
| 07 Existing-event management screens | 04, 05, 06 |
| 08 Manual break screens and confirmation | 04, 05 |
| 09 Vacation screens | 04, 05 |
| 10 Saved-settings screens | 00, 04 |
| 11 Cross-flow verification and handoff | 06, 07, 08, 09, 10 |

Tasks are deliberately backend-contract-first, then bot UI. Coordinate shared client/dialog changes; do not assign the entire milestone as one task.

## Common acceptance criteria

Apply these in addition to each task's checks:

- Use existing async domain patterns and typed HTTP boundaries. Never import backend models/services/settings into the bot. Backend alone decides admission and ownership; another student's busy slot must never disclose their identity to a student.
- New API reads/mutations use user JWTs, not the trusted registration service key. Authenticate and authorize every referenced ID, including stale or forged callbacks; buttons are not authorization.
- Keep normal inline/text transitions in the active message using `ShowMode.EDIT`; acknowledge callbacks, preserve drafts on correctable errors, provide Back/Cancel, and confirm destructive operations.
- Use existing isolated real-DB fixtures and `BotDriver`/`RecordingSession` in [bot support](../../tests/bot_support.py). Test the real backend through the client/dispatcher, mocking only external Telegram transport. No developer DB or live Telegram messages in automated tests.
- Run smallest relevant tests per task, including existing onboarding/schedule regressions when shared surfaces change. Run `uv run ruff check backend telegram tests`, format checks for touched files, `uv run mypy backend`, and `uv run mypy telegram` as applicable. Record unrelated baseline failures honestly.
- No migration is expected for the initial UI/API bridge. If actual model changes are required, stop and amend the plan; use static reversible `YYYY-MM-DD_slug.py` migrations and disposable SQLite/PostgreSQL checks.

## Task 00: Approve navigation, input, and mutation contracts

**Domain:** product/API contract. **Type:** planning. **Dependencies:** None.

**Context:** The README specifies capabilities, but not the exact bot screens, date grammar, or single-occurrence move contract.

**What to do:** Approve and record the role-home, recurrence, occurrence-move, date/time, timezone, conflict-feedback, stale-write, and ambiguous-write contracts before implementation.

**Approved contract:** The product owner approved these recommendations on 2026-10-24:

- Role homes expose Schedule, Add lesson, Manage events, and Settings; teachers also get Breaks, and both roles get Vacations.
- Lesson creation supports one-off and weekly events. New weekly series use `interval=7` and an explicit end date; edits preserve the existing interval.
- Occurrence cancellation/move is distinct from whole-series edit/delete. A move atomically cancels the original occurrence and creates a one-off replacement.
- Date and time use separate, bounded prompts: dates accept `MM DD`, `MM:DD`, or `MM/DD`, with an optional year; times accept `HH MM` or `HH:MM`. If an omitted year would make the date past, infer the next year and require confirmation. Displayed recurrence/vacation end dates are inclusive and map to exclusive backend cutoffs.
- Resolve local times through the backend temporal helpers. A DST gap shifts forward by the gap; an ambiguous time chooses the earlier instant. Show the timezone and resolved UTC offset in the review.
- Initial booking uses manual date/time entry and explicit conflict feedback; a free-slot picker is not required.
- Edits use optimistic stale-write protection. Never blindly replay a write with an unknown outcome; show an unknown-outcome state with a safe reload/reconciliation path. No operation-key or idempotency schema change is required.
- API design uses a bounded `skip`/`limit` teacher-student picker returning `{items, total}` with only ID/display label; the bot represents a personal lesson using the authenticated teacher identity.
- Event management uses domain-scoped, JWT-authenticated list/detail reads with bounded filters and pagination. Detail results include the authoritative subject, recurrence data and cancellations, hidden/canceled status, and a revision precondition. Authorization is applied before shaping results.
- Occurrence actions identify the source and exact UTC start/end. A move submits the replacement interval and atomically cancels the selected occurrence while creating a one-off replacement. Edits submit the revision precondition; stale writes make no changes.
- Break displacement confirmation is bound to the submitted event request and exact affected-occurrence preview. Confirmation revalidates both; a changed affected set returns a new preview without applying the write.
- User-entered local datetimes are resolved by the backend temporal helper using the explicit IANA timezone; the response/review includes the UTC instant and resolved offset. Exact route paths follow the existing domain routers.

**Reuse:** Existing schedule/week UI, UTC/recurrence contract, current role permissions.

**Acceptance:** [x] Screen map, backend request shapes, parser examples, DST/cutoff semantics, stale-edit and ambiguous-write behavior are recorded as approved. [x] Required availability/idempotency schema additions have explicit tasks/migration decisions before work starts; none were approved.

**Out of scope:** Implementing unresolved choices. **Suitable for:** Human/product owner with agent preparation.

## Task 01: Expose a teacher-scoped student picker

**Domain:** existing `backend/auth/`. **Type:** service/router. **Dependencies:** 00.

**Context:** Teachers need to select their own students or themselves, but `/auth/users` is admin-only.

**What to do:** Add one bounded, paginated teacher-only student read returning the minimum approved picker fields (ID/display label), filtered by authenticated teacher linkage. Represent "Personal lesson" using the current teacher identity. Keep private profiles, emails, notification settings, and Telegram routing IDs out of picker results.

**Reuse:** Auth role dependencies and `assert_can_assign_user`; do not call the admin user list or broaden `OwnedProfileUser`.

**Acceptance:** [x] Multiple pages and empty rosters work. [x] Students/unrelated teachers cannot enumerate another roster; inactive/de-linked subjects cannot bypass mutation authorization. [x] Ruff checks and formatting pass; changed backend modules type-check. `mypy backend` still reports existing errors in `backend/auth/config.py`, `backend/database.py`, and `backend/events/dependencies.py`.

**Out of scope:** Full student profile/history/deletion. **Suitable for:** AI agent.

## Task 02: Add authorized editable event reads

**Domain:** existing event-domain routers and `backend/events/`. **Type:** service/router/schemas. **Dependencies:** 00.

**Context:** Vacation-hidden events must remain manageable, and edits need more than a schedule row.

**What to do:** Add minimal paginated/filterable management lists and domain-specific detail reads for one-off lessons/breaks/vacations and recurring lessons/breaks. Include authoritative subject, full series metadata, exact cancellations, and approved stale-edit token. Show canceled/hidden status explicitly without changing display visibility. Use actor scope before shaping results. Expose only the minimal linked-teacher booking context needed by a student to review defaults/work windows, without granting access to the teacher's owner-only profile. Implement the approved backend-assisted local-time resolution boundary if selected in Task 00, reusing `resolve_local_datetime` rather than copying DST logic.

**Reuse:** `assert_owns_event`, domain existence validators, UTC schemas and [time helpers](../../backend/events/time_utils.py).

**Acceptance:** [x] Owners can find hidden vacations and canceled series. [x] Type-confused IDs and cross-student/teacher IDs fail. [x] Pagination, legacy recurrence metadata, DST resolution and stale-token contract have real-DB tests. [x] Ruff, formatting, changed-module mypy, and focused real-DB regression tests pass. `uv run mypy backend` still reports the existing errors in `backend/auth/config.py`, `backend/database.py`, and `backend/events/dependencies.py`.

**Out of scope:** Returning teacher-wide raw schedules to students or new recurrence types. **Suitable for:** AI agent.

## Task 03: Add atomic single-occurrence actions and stale-edit protection

**Domain:** existing `backend/events/` and recurrent lesson router. **Type:** service/router. **Dependencies:** 00, 02.

**Context:** Sending a fetched cancellation list back through full-series PUT can overwrite another cancellation. A bot-side cancel-then-create move can lose a lesson when creation fails.

**What to do:** Add the approved exact-occurrence cancellation and single-occurrence move operations under the existing teacher mutation guard. Re-read authorized state after acquiring the guard, validate the generated UTC start/end identity, and apply cancellation plus replacement atomically for moves. Implement Task 00's stale-write checks for management edits and apply them to all new UI write paths. Bind manual-break UI confirmation to the reviewed request and affected-occurrence set: if revalidation finds different affected lessons, return a fresh preview rather than treating an old boolean confirmation as permission to cancel unseen lessons. Preserve existing API compatibility while adding this guarded UI contract. Refactor only the needed transaction-owning internals so there are no intermediate commits. Do not invent a future-only series split.

**Reuse:** `_run_serialized_schedule_mutation`, cancellation validation, existing lesson admission and create/update services.

**Acceptance:** [x] Guarded concurrent exact cancellations preserve both identities. [x] A failed move rolls back its cancellation and creates no replacement. [x] Repeated/off-grid actions are explicit; updates/deletes accept `If-Match` revisions and reject stale writes. [x] Break-displacement previews return a token bound to the submitted request and affected set; the bot flow must send it in `X-Displacement-Token`, while tokenless legacy confirmation remains compatible. [x] Focused real-DB tests, Ruff, formatting, changed-module mypy, and editor diagnostics pass. `uv run mypy backend` still reports the existing errors in `backend/auth/config.py`, `backend/database.py`, and `backend/events/dependencies.py`.

**Out of scope:** Changing existing cancellation-clearing rules without approval. **Suitable for:** AI agent.

## Task 04: Extend the typed bot client for management

**Domain:** existing `telegram/api.py`. **Type:** bot/client. **Dependencies:** 01, 02, 03.

**Context:** Existing safe-read renewal and JSON-only response handling are insufficient for writes and 204 deletes.

**What to do:** Add typed reads/writes for picker, event management, occurrence actions, and existing self/profile updates; handle 204 explicitly. Map known policy failures to safe useful UI messages (work hours/days, duration, limits, temporal conflict, contention). Parse and validate the known displacement-preview shape separately from a generic 409; do not show confirmation for contention. Renew auth only on a definitive unauthorized response; never automatically retry an ambiguous write. Implement the approved reconciliation mechanism or display "outcome unknown" with a safe reload path.

**Reuse:** Existing HTTP client, circuit breaker, tokens, `BackendError`, safe-read auth renewal.

**Acceptance:** [x] 204, structured 409, validation, stale writes, 401, transport failure and lost response tests pass without duplicate creates or raw payload disclosure. [x] Common bot checks pass (`199 passed, 4 skipped`; Ruff check, Telegram/test formatting, and `mypy telegram` pass).

**Out of scope:** Offline mutation queues and backend imports. **Suitable for:** AI agent.

## Task 05: Add user-local scheduling date/time input

**Domain:** existing `telegram/inputs.py` and dialog widgets. **Type:** bot. **Dependencies:** 00.

**Context:** Clock parsing exists; calendar dates, rollover, and event-local conversion do not.

**What to do:** Implement the approved bounded grammar with date and time prompts separated where needed. Reuse existing clock/timezone parsing; resolve instants through the approved boundary. Display timezone, explicit year, resolved DST offset, and final interval in review. Warn/confirm any inferred next-year date; reject invalid calendar dates, overflow, and invalid ranges without discarding drafts.

For an omitted-year February 29 that cannot be represented in the next calendar year, require an explicit year rather than skipping ahead multiple years.

**Reuse:** `parse_clock`, `parse_timezone`, onboarding input-error/edit patterns.

**Acceptance:** [x] Exact examples cover separators, explicit years, Dec/Jan, leap dates, past dates, DST gaps/folds, and viewer/teacher timezone differences. [x] Common checks pass (`225 passed, 4 skipped`; Ruff, Telegram/test formatting, and `mypy telegram` pass).

**Out of scope:** Natural-language parsing or a second recurrence engine. **Suitable for:** AI agent.

## Task 06: Build lesson-creation dialogs

**Domain:** `telegram/` scheduling dialogs and role home. **Type:** bot. **Dependencies:** 04, 05.

**Context:** Backend admission exists; users cannot yet book from Telegram.

**What to do:** Wire Add lesson from home: student is self; teacher selects own student or Personal lesson. Collect approved one-off/weekly choice, local start, series cutoff, and teacher-only custom duration; omit `end` on default-duration creation so backend remains authoritative. Review then submit. Explain policy rejection and return to the relevant editable field. Refresh live schedule after confirmed success.

**Reuse:** Picker/client/input tasks, `ShowMode.EDIT`, existing schedule navigation and draft patterns.

**Acceptance:** [x] Teacher/student/personal creations persist and appear in the live schedule. [x] Students cannot forge duration/subject; concurrent slot loss requires a schedule reload and cannot be mistaken for success. [x] Duplicate submits and unknown write outcomes cannot replay creation. Focused tests passed in three consecutive runs (42 each); Ruff check, Telegram/test formatting, and `mypy telegram` pass.

**Out of scope:** Auto breaks (next plan). **Suitable for:** AI agent.

## Task 07: Build lesson selection, move, cancellation, and deletion dialogs

**Domain:** `telegram/` event-management and schedule dialogs. **Type:** bot. **Dependencies:** 04, 05, 06.

**Context:** Users need to manage existing events without losing recurrence or cancellation information.

**What to do:** Add bounded event selection from schedule/management lists and fresh detail reads. Offer authorized one-off edit/delete and recurring occurrence cancel/move versus entire-series edit/delete. Label consequences, particularly recurrence edits clearing cancellations under the existing contract. Confirm destructive operations, reload on stale state, and show success only after an authoritative response/reconciliation.

**Reuse:** Management details, occurrence API, client errors, existing pagination and edit modes.

**Acceptance:** [x] Both roles manage only authorized lessons; hidden lessons remain manageable and cancelled occurrences are discoverable through paginated selectors. [x] Whole-series versus one-occurrence choices produce the expected persisted results. [x] Stale callbacks, lost responses and failed moves preserve data; the full suite passes (245 passed, 4 skipped), Ruff/format checks and `mypy telegram` pass. `mypy backend` retains three unrelated baseline errors in `backend/auth/config.py`, `backend/database.py`, and `backend/events/dependencies.py`.

**Out of scope:** Future-only series splitting. **Suitable for:** AI agent.

## Task 08: Build manual-break dialogs with explicit displacement confirmation

**Domain:** `telegram/` teacher dialogs. **Type:** bot. **Dependencies:** 04, 05.

**Context:** Teacher manual-break creation can permanently remove lesson occurrences.

**Approved UI contract:** A break is recorded on the teacher's calendar and blocks availability for all of that teacher's students. Students do not see break events or their details. The teacher enters start and end times on the same local date.

**What to do:** Wire teacher-only create/edit/delete for one-off and weekly breaks. First submit without confirmation; render authorized affected occurrences from the typed preview, with bounded pagination and a clear permanent-cancellation warning. Cancel performs no write. Explicit Confirm retries only the reviewed request; revalidation may produce a new conflict requiring fresh review. Do not convert every 409 into confirmation.

**Reuse:** Existing break routes and `confirm_displacement=true` contract; typed preview and local date/time input.

**Acceptance:** [x] Preview/Cancel do not mutate; affected occurrences are shown in bounded pages and a changed preview requires a new confirmation. [x] Confirm deletes exact one-offs/cancels exact recurrences; confirmed one-off and recurring break edits work, and deleting a break does not restore displaced lessons. [x] Breaks are teacher-only and teacher-owned; student schedules and management reads hide them, forged student writes are forbidden, and booking conflicts disclose no break details. Full suite: 256 passed, 4 skipped; Ruff/format and `mypy telegram` pass. `mypy backend` retains three unrelated baseline errors in `backend/auth/config.py`, `backend/database.py`, and `backend/events/dependencies.py`.

**Out of scope:** Automatic breaks or changed displacement priority. **Suitable for:** AI agent.

## Task 09: Build vacation-management dialogs

**Domain:** `telegram/` role dialogs. **Type:** bot. **Dependencies:** 04, 05.

**Context:** Vacations hide events but remain stored and need a management entry outside visible schedules.

**What to do:** Add authorized create/edit/delete for the current one-off vacation API, including teacher/own-student scope where existing authorization permits. Use explicit local date/time and approved end-date semantics; explain that vacations hide rather than erase lessons. List vacations independently of visible schedule; confirm deletion.

**Reuse:** Vacation CRUD, management reads, existing visibility contract, date input.

**Acceptance:** [x] Teacher/student vacations hide the correct scope and deleting restores display without restoring break-displaced lessons. [x] Inclusive end-date boundaries, authorization, independent management listing, edit-in-place, and ambiguous-write recovery pass real-dispatcher/backend tests. `uv run pytest -q tests` passes (259 passed, 4 skipped); Ruff check/format and `mypy telegram` pass.

**Execution:** Added teacher and student vacation management in Telegram, including role-scoped subject selection, bounded lists, local date/time entry, deletion confirmation, revision-aware updates, and no-replay recovery for ambiguous writes.

**Out of scope:** A recurring-vacation API or new destructive vacation semantics. **Suitable for:** AI agent.

## Task 10: Build post-onboarding preference screens

**Domain:** `telegram/` settings dialogs and existing auth API. **Type:** bot. **Dependencies:** 00, 04.

**Context:** Backend preferences are editable, but the bot only collects them during registration.

**What to do:** Add Settings for both roles, loaded fresh. Edit timezone/digest; teacher work times/days, duration, break minutes, and limits; student reminder minutes (explicit hours/minutes choice) and homework days, with null toggles. Reuse parsers/widgets without rerunning registration or resetting invitation codes. Save changed fields only, use one owning endpoint per save, and reload authoritative results. Explain that timezone does not move stored instants and notification preferences do not imply delivery. Show automatic breaks as pending until the next milestone ships. Do not claim a multi-endpoint update is atomic.

**Reuse:** Existing self PATCH, owner profile PUT, merged validation, onboarding setting parsers.

**Acceptance:** [x] Omitted values and invitations stay unchanged; null only disables allowed notifications. [x] Back/Cancel, invalid merged limits, expired auth and partial failures never report a false all-saved result. [x] Existing onboarding regressions pass; full suite: 264 passed, 4 skipped; Ruff check/format and `mypy telegram` pass.

**Out of scope:** New settings, account linking, teacher transfers. **Suitable for:** AI agent.

**Execution:** Added fresh-loaded Telegram settings for both roles. Account and role-profile preferences use separate changed-fields-only writes and authoritative reloads; settings remain preferences-only, timezone changes preserve stored instants, and automatic breaks remain explicitly pending. Unknown writes require reload/reconciliation without replay.

## Task 11: Verify full Telegram scheduling and document handoff

**Domain:** tests and project docs. **Type:** integration/documentation. **Dependencies:** 06, 07, 08, 09, 10.

**Context:** Backend completeness is not evidence of a working Telegram feature.

**What to do:** Exercise teacher + two students + unrelated teacher through the real dispatcher/client/backend: settings, creation, whole-series and occurrence edits, vacation hide/unhide, break preview/cancel/confirm, limits, lost response, restart recovery and stale callbacks. Assert stored rows/cancellations, exact UTC/local dates, role filtering, and measured send/edit IDs. Update README and AGENTS feature status only for shipped capabilities; link this plan and the next two plans in the handoff.

**Reuse:** Existing bot integration fixtures and booking/concurrency tests.

**Acceptance:** [x] Every new action is reachable from the role home and has tested success/error/cancel paths. [x] Common checks pass and evidence is recorded: full suite 265 passed, 4 skipped; Ruff check/format and `mypy telegram` pass. [x] Optional real-bot readability smoke test is explicitly reported separately; it was not run.

**Out of scope:** Automatic breaks, homework, notification delivery, HTMX, offline cache. **Suitable for:** AI agent; human for live UX acceptance.

**Execution:** Added a real-dispatcher cross-flow test with one teacher, two students, and an unrelated teacher. It exercises settings, booking and daily-limit rejection, role-scoped schedules, vacation hide/unhide, break displacement preview/cancel/confirm, UTC/local boundaries, restart recovery, stale callbacks, and measured message edits. Existing focused integration tests cover whole-series/occurrence edits and ambiguous writes. Verification used the simulated Bot API transport and real ASGI backend; no live Telegram readability smoke test was run.

## Open questions / implementation gates

- Task 00 requires owner approval; recommendations above are not approved behavior.
- API gaps are prerequisites, not permission to weaken profile authorization or expose raw teacher data.
- This plan intentionally includes real Telegram functionality; it is not complete when APIs alone pass tests.
- Next: [automatic breaks](./automatic-between-lesson-breaks.md), then [homework](./homework-backend-and-telegram-ui.md). No backend code is implemented by writing these plans.
