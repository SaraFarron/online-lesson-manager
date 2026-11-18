# Telegram Polish, Vacation Simplification and Time-Slot Picker — Plan

**Status:** Implemented and verified by automated tests (2026-11-18), Tasks
1–11; see the execution record at the end. No live Telegram or PostgreSQL
verification was performed.

## Goal
Remove redundant text and confirmations from the break, lesson and vacation
screens, simplify vacations to whole days, stop command messages from
cluttering the chat, and let users pick lesson/break start times from buttons
of available slots instead of typing them.

## Scope (README.md sections covered)
- Telegram bot (AGENTS.md Feature Status: **Partial**), lessons / breaks /
  vacations management presentation (**Done** backend), plus one new read-only
  backend endpoint for available start times (Task 8) and one list endpoint for
  upcoming vacations (Task 6).
- Source: `todo.txt` (repository root). It is not part of any shipped contract
  and may be deleted once this plan is implemented.
- Tasks 1–7 are `telegram/` presentation/flow changes plus their tests. Only
  Tasks 6 (vacation list filter) and 8 (slot availability) touch `backend/`.
  No task changes the schema, so **no Alembic migration is needed**.
- This plan **supersedes** these presentation contracts (update the linked docs
  in Task 11): the open-ended-series note and delete confirmations in
  [telegram-event-editor-consistency](./telegram-event-editor-consistency.md),
  the vacation start-time prompt/review in
  [telegram-scheduling-and-settings-ui](./telegram-scheduling-and-settings-ui.md),
  and the typed start-time prompts described in
  [telegram-interface-streamlining](./telegram-interface-streamlining.md).

## Findings from the code (read before implementing)
- `OPEN_ENDED_BREAK_NOTE` in [breaks.py](../../telegram/dialogs/breaks.py) is
  appended to the `Last break date (inclusive): No end` line in **two** places:
  the break review (`review_data`) and the break detail (`detail_data`). The
  detail screen is the one with the `Delete break` button.
- Break delete goes `begin_delete` → `BreakScreens.confirm_delete` →
  `confirm_delete`. `confirm_delete` already owns the unknown-outcome/stale-
  revision handling (`needs_reload`, `outcome_unknown`, `operation="delete"`)
  and the detail window already renders `feedback`, `notice` and the `Reload
  break` button, so the detail screen can host the retry/recovery states.
  One-time and recurring breaks share this path. Lesson delete
  (`LessonManagementScreens.confirm`, shared with `cancel_occurrence`) and
  vacation delete (`VacationScreens.confirm_delete`) are separate and are **not**
  requested to change.
- `Personal lesson` in the lessons list comes from the backend
  (`_PERSONAL_SUBJECT_LABELS` in
  [reads.py](../../backend/events/management/reads.py), also
  `PERSONAL_LESSON` in [schedule/service.py](../../backend/schedule/service.py)).
  Existing tests assert it in the **schedule** (`test_schedule_service.py`,
  `test_bot_schedule_*`) and in lesson creation, so the API value must stay.
  The list label is built in `_list_label`
  ([lessons/management.py](../../telegram/dialogs/lessons/management.py)); the
  `· N cancelled` suffix is appended there too.
- Vacation wrong prompt: creation goes start date → start time → end date.
  `_accept_date` stores `date_field="start_date"` and nothing resets it, while
  the `end_date` window reuses `date_data`, which picks the prompt from
  `date_field`. Fix by giving the end-date window its own prompt (state-based,
  not `date_field`-based).
- Vacation storage is already whole-day: `_local_interval` builds the interval
  from `start_time` to local midnight after the last date (exclusive end,
  i.e. 23:59 of the last day). So "start 0:00, end 23:59" needs no backend or
  contract change — only dropping `start_time` from the flow. Existing
  vacations may have a non-midnight start (created through the old flow); they
  are not migrated (see Task 4).
- Vacation list uses `read_management_page(..., "vacations")`
  (`GET /api/v1/vacations/`, all vacations by start, including finished ones).
  Lessons/breaks already have `GET /…/upcoming` backed by
  `list_upcoming_managed_events`, which filters one-offs by `end > now` and
  works for any `EventType`; vacations have no such route. Vacations are always
  one-off (`is_recurrent=False`).
- The vacation list, like all `Select` widgets without a wrapper, renders its
  buttons in **one row**; the break list wraps `Select` in `Column`.
- Command messages are never deleted. `/start`, `/settings`, `/vacations` are
  registered in `build_dispatcher` ([runtime.py](../../telegram/runtime.py));
  `delete_accepted_input_message` in
  [date_cleanup.py](../../telegram/shared/date_cleanup.py) only supports
  `date|time|text` and adds a user warning on failure, which is wrong for
  commands (no warning wanted).
- No code in `telegram/` creates a `ReplyKeyboardMarkup` today (git history:
  `81de3d3` added reply keyboards, `55c5f6e` removed them). The keyboard the
  user still sees under the input field is therefore a **stale persistent reply
  keyboard** from an old bot version stored in the client. Telegram only removes
  it when the bot sends a message with `ReplyKeyboardRemove`; it cannot be
  attached to an edit, and aiogram-dialog edits the active message.
- **There is no availability concept in the backend.** Start times are typed
  and then validated on write (`validate_schedule_change` in
  [conflicts.py](../../backend/events/conflicts.py), `prepare_lesson_write` /
  `validate_lesson_work_window` / `validate_lesson_limits` in
  [booking_policy.py](../../backend/events/booking_policy.py)). The bot only has
  `POST /api/v1/events/resolve-local-time`. A slot picker needs a new read-only
  computation; doing it in the bot would duplicate scheduling rules, which AGENTS.md
  forbids. Time is typed in: lesson creation (`start_time_input`), lesson
  management edit and move (`time_input`, `inspect` excluded: it targets an
  existing cancelled occurrence), break creation/edit (`start_time_input`,
  `end_time_input`). Vacations lose their time prompt in Task 4.
- Dead-code note: after Task 3 `BreakScreens.confirm_delete`, `begin_delete`'s
  `confirm_text` and its window become unused and must be removed, not left.
  After Task 4 `VacationScreens.start_time`, `start_time_input`,
  `start_time_error`, `edit_start_time`, `edit_subject_from_start_time`, and the
  `"start_time"` branches of `back_from_field_editor` and the subject-return map
  become dead.

## Tasks

### 1. Break screen text cleanup
**Domain:** `telegram/dialogs/breaks.py` (existing) · **Type:** bot

**Context:** Recurring break detail and review screens with no end date.

**What to do**
- In `detail_data` and `review_data`, replace the whole
  `Last break date (inclusive): No end` + note with the single line
  `No end date` when `interval_end is None`. Finite cutoffs keep
  `Last break date (inclusive): <date>`.
- Delete `OPEN_ENDED_BREAK_NOTE` and its uses.

**Acceptance Criteria**
- [ ] Neither screen contains `No end.`/`one calendar year`/`later cancellations`.
- [ ] Open-ended detail and review show `No end date`; finite series unchanged.
- [ ] Existing break tests updated; `uv run ruff check telegram tests`,
      `ruff format --check telegram tests`, `uv run mypy telegram` pass.

**Out of scope:** the one-year validation rule itself and the `No end` button.
**Suitable for:** AI agent.

### 2. Lesson list and review text cleanup
**Domain:** `telegram/dialogs/lessons/management.py` (existing) · **Type:** bot

**What to do**
- In `_list_label`, drop the `· N cancelled` suffix (keep `hidden by vacation`).
- Show `Lesson` instead of `Personal lesson` in the list label when the item
  belongs to the viewing teacher (`item.user_id == UUID(account_id)` and the
  viewer is a teacher), rather than matching the English API string. Students
  and other assignees keep the backend `subject_label`.
- In the weekly-lesson change review (`_review_text` sentence that begins
  `Changing recurrence dates or time clears…`), remove that sentence with no
  replacement. The behavior (cancellations cleared on date/time change) is
  unchanged.

**Reuse:** `EventManagementItem.user_id`; no backend change.

**Acceptance Criteria**
- [ ] Teacher list buttons read `Weekly · Lesson · <date>` / `One time · Lesson · <date>`
      with no cancellation count; detail still shows `Cancelled occurrences: N`.
- [ ] Review text no longer contains the removed sentence in any changed-series case.
- [ ] Schedule screens and lesson creation still show `Personal lesson`
      (explicitly unchanged).
- [ ] Tests, ruff, mypy pass.

**Suitable for:** AI agent.

### 3. Delete breaks without a confirmation screen
**Domain:** `telegram/dialogs/breaks.py` (existing) · **Type:** bot

**What to do**
- Make the detail window's `Delete break` button call the deletion directly
  (the body of `confirm_delete`), for both one-time and recurring breaks.
- Remove `BreakScreens.confirm_delete`, its `Window`, `begin_delete` and the
  `confirm_text` data. Keep the post-delete notice
  `Break deleted. Displaced lessons were not restored.`
- Preserve recovery: while `submitting` ignore repeat taps (double-tap must not
  send two deletes); on `BackendOutcomeUnknown` and `stale_event_revision`
  stay on the detail screen with the warning/error and the existing
  `Reload break` button; `reload_state`'s `operation == "delete"` handling
  (404/422 → list with "no longer available") must keep working.
- Hide `Edit break` / `Delete break` while `needs_reload`/`outcome_unknown`
  (already via `can_manage`).

**Acceptance Criteria**
- [ ] One tap on `Delete break` deletes a one-time and a recurring break and
      lands on the list with the notice; no confirm screen exists.
- [ ] Unknown outcome, stale revision, 404 and backend outage keep their existing
      behavior (no repeated write, drafts/buttons preserved).
- [ ] Double-tap sends one `DELETE`.
- [ ] Tests that clicked `Confirm delete` for breaks updated; ruff/mypy pass.

**Out of scope:** lesson and vacation delete confirmations (decision: they stay).
**Suitable for:** AI agent.

### 4. Whole-day vacations
**Domain:** `telegram/dialogs/vacations.py` (existing) · **Type:** bot

**What to do**
- Remove the start-time step from creation and editing: start date → end date →
  review (create), and no `Change start time` button in review. The interval is
  always local `00:00` of the start date to local midnight after the last date
  (unchanged contract; `_local_interval` stops reading `start_time`).
- Fix the end-date prompt: the `end_date` window must say `Enter the last
  vacation date (inclusive) as DD MM, DD:MM or DD/MM, optionally with a year.`
  regardless of `date_field`. Also drop `The vacation ends at local midnight
  after this date.` from that window (internal detail).
- Review screen: show `First vacation date` and `Last vacation date` using
  `format_human_date(..., short_month=True)` (e.g. `Dec 5`); no `Starts:` time,
  no `The backend end is midnight…`, no `Overlapping events are hidden, not
  erased.`
- Apply the same date-only, short-month format to the detail screen and the list
  label (`Personal vacation · Dec 1 – Dec 5`) so screens agree; detail loses its
  `Starts:` time.
- Remove the dead start-time code listed in Findings, including the
  `start_time` keys in `start_vacation_management`, `begin_create`, `begin_edit`.
- Existing vacations are not migrated or backfilled. List and detail show only
  dates for them too (a legacy non-midnight start is simply not displayed).
  Saving an edit re-saves the vacation as whole days (Open question 2).

**Reuse:** `parse_schedule_date`, `format_human_date`, `resolve_local_interval`
(for the DST-safe local midnight bounds), `delete_accepted_date_message`.

**Acceptance Criteria**
- [ ] Creation after the start date goes straight to the end-date prompt with the
      correct text; invalid/before-start dates keep their errors.
- [ ] Review text is exactly the date-only form above; saved payload starts at
      local midnight and ends at the midnight after the last date (test in a
      non-UTC timezone and across a DST change).
- [ ] No time prompt or `Change start time` button anywhere in the flow.
- [ ] Vacation tests migrated; ruff/mypy pass.

**Suitable for:** AI agent.

### 5. Vacation list layout
**Domain:** `telegram/dialogs/vacations.py` (existing) · **Type:** bot

**What to do**
- Wrap the vacation `Select` in `Column` (one vacation per row, the same as the
  break list); decision: single column.
- Apply the same wrapper to the vacation assignee picker `Select` only if it
  currently renders in one row (check first; not requested).

**Acceptance Criteria**
- [ ] The keyboard has one vacation button per row with Previous/Next unchanged.
- [ ] Test asserts the layout via `reply_markup.inline_keyboard`.

**Suitable for:** AI agent (depends on Task 4 for label length).

### 6. Hide finished vacations
**Domain:** `backend/vacations/` + `telegram/api/client.py` + `telegram/dialogs/vacations.py` · **Type:** router + bot

**What to do**
- Add `GET /api/v1/vacations/upcoming` in
  [backend/vacations/router.py](../../backend/vacations/router.py), declared
  **before** `/{vacation_id}`, calling the existing
  `management_service.list_upcoming_managed_events(session, user,
  EventType.VACATION, …)`, mirroring `/lessons/upcoming` and `/breaks/upcoming`
  (same `skip`/`limit`/`subject_id`, same `EventManagementPage`).
  A vacation counts as finished once its exclusive end is `<= now`
  (`Event.end > now`); an ongoing vacation stays.
- Add `"vacations"` to `read_unified_management_page`'s resource literal in
  `telegram/api/client.py` (the path is already `/api/v1/{resource}/upcoming`)
  and use it in the vacation `_load_page`. Keep `read_management_page(...,
  "vacations")` for `homework/teacher_creation.py` if it needs the full list
  (check before changing it).
- Verify vacation pagination totals after hiding past rows (the existing skip
  logic must not show an empty page when `total` shrinks).

**Reuse:** `list_upcoming_managed_events` (no new query logic), existing
`EventManagementPage`.

**Acceptance Criteria**
- [ ] A finished vacation is absent from the bot list; an ongoing and a future
      one are shown, ordered by start; authorization/scoping unchanged
      (teacher: personal/students, student: own).
- [ ] Backend test for `/vacations/upcoming` (past hidden, ongoing visible,
      student scope, route not shadowed by `/{vacation_id}`).
- [ ] `ruff check --fix backend && ruff format backend && mypy backend` pass.
- [ ] No migration.

**Out of scope:** deleting or archiving old vacations; changing `/vacations/`.
**Suitable for:** AI agent.

### 7. Remove command messages and the stale reply keyboard
**Domain:** `telegram/runtime.py`, `telegram/shared/` (existing) · **Type:** bot

**What to do**
- Delete the user's command message (`/start`, `/settings`, `/vacations`, and any
  other `/command` in a private chat) so commands do not clutter the chat. Prefer
  one shared, silent helper (no user-visible warning if Telegram refuses; log at
  warning level) over adding `warning` text as `delete_accepted_input_message`
  does. Choose placement carefully: deleting before the handler runs is simplest
  (an outer message middleware for messages whose first entity is `bot_command`),
  but it must not break `CommandObject` parsing (`/start t_ABC123` payloads) and
  must not run for non-private chats.
- Unknown `/commands` must also be deleted and may receive no reply (no new
  behavior beyond deletion unless currently handled elsewhere).
- On `/start` only, remove the stale reply keyboard: send a short message with
  `ReplyKeyboardRemove()` and delete it immediately, before showing the dialog.
  Do it every `/start` (cheap, idempotent); failure to delete must not abort
  `/start`.
- Keep existing behavior: dialog screens still edit in place; `/start` does not
  create a second visible message.

**Acceptance Criteria**
- [ ] After `/start`, `/settings`, `/vacations` and an unknown command the
      recording transport shows a `deleteMessage` for the command message.
- [ ] `/start` with an invitation payload still works.
- [ ] `/start` sends one `ReplyKeyboardRemove` message and deletes it; the final
      visible state is only the dialog message.
- [ ] A refusal to delete (already deleted / too old) does not surface to users.
- [ ] Group chats are not affected. `tests/test_bot_commands.py` and the others
      that assert on message counts updated; ruff/mypy pass.

**Out of scope:** normal text input deletion (already done elsewhere).
**Suitable for:** AI agent. Needs a manual check in real Telegram clients
(keyboard removal cannot be proven by the recording transport).

### 8. Backend: available start-time slots
**Domain:** `backend/events/` (existing) · **Type:** service + router

**Context:** README.md scheduling rules; currently only write-time validation.

**What to do**
- Add a read-only endpoint, e.g. `GET /api/v1/events/available-start-times`
  (authenticated TEACHER/STUDENT, like `resolve-local-time`), accepting the
  viewer-local `date`, `event_type` (`LESSON`/`BREAK`), `duration_minutes`
  (lessons), `step_minutes` (only `15` default or `5`, anything else is a 422),
  `subject_id` (teacher only, as in management reads), `recurrence`
  (`once`/`weekly`) and an optional `exclude_event_id` (+ occurrence start) so
  editing/moving an event does not conflict with itself.
- Return an ordered list of local start times (with UTC instants and DST fold
  info so the bot can rebuild the exact instant) for that date.
- Compute candidates on the requested grid, aligned to local clock multiples of
  `step_minutes`, then keep only those that pass
  the same checks a write would: work days/hours and lesson duration
  (`validate_lesson_work_window`), daily/consecutive limits
  (`validate_lesson_limits`), conflict/overlap and vacation rules
  (`validate_schedule_change`, `load_raw_occurrences_for_validation`) with the
  snapshotted automatic post-lesson break. Refactor those to a shared
  non-raising "is this interval acceptable" predicate **rather than
  re-implementing the rules**; the write path must keep using the same code.
- Weekly lessons/breaks: availability is computed for the first occurrence only;
  later conflicts are still reported by the existing write validation
  (recurrence horizon rules unchanged).
- Breaks (decision): start slots are limited to the teacher's work hours and
  exclude any start from which not even the minimum break (one grid step) fits
  without overlapping an existing lesson, break or blocking event. Add a
  second read (or a `break_start` parameter on the same endpoint) returning valid
  **end** times for a chosen start: every grid step after the start up to the
  first overlap or the end of the work hours, never crossing local midnight.
  Breaks are not currently validated against work hours, so this filter lives
  only in the availability computation; write validation is unchanged.
  The displacement confirmation in the write path is unchanged.
- Results are advisory: concurrent writes can still conflict; the write path
  stays authoritative and the bot already handles its errors. Do not take the
  per-teacher mutation guard for this read.
- Cap and document the response size (one local day) and authorization:
  a student gets only slots for their own booking context, never other students'
  event details.

**Reuse:** `backend.events.booking_policy`, `backend.events.conflicts`,
`backend.events.range_service.load_raw_occurrences_for_validation`,
`backend.events.visibility`, `time_utils.resolve_local_datetime`.

**Acceptance Criteria**
- [ ] Slots exclude: non-working days/hours, times where the lesson plus its
      automatic break would overlap an existing event, vacation-hidden/blocked
      times, times exceeding daily/consecutive limits, and past times for today.
- [ ] Both `step_minutes=15` and `5` return clock-aligned slots; other values rejected.
- [ ] Break start and end options stay inside work hours and never overlap an
      existing lesson/break; a break submitted from an offered start/end pair
      produces no displacement preview.
- [ ] A slot returned for a lesson is accepted by the real create endpoint in
      the same state (parity test over a representative set), and a rejected
      time is not returned.
- [ ] DST gap/fold days handled (no nonexistent local times; ambiguous time
      represented once with the fold fixed).
- [ ] `exclude_event_id` allows the current time on edit.
- [ ] Student cannot read teacher-wide details; unauthorized/foreign
      `subject_id` rejected like management reads.
- [ ] `uv run pytest -q tests`, `ruff check --fix backend`, `ruff format
      backend`, `mypy backend` pass. No migration.

**Out of scope:** reserving slots, push notifications, HTMX UI.
**Suitable for:** Human or strong AI agent; touches core scheduling rules.

### 9. Bot: slot picker for lesson creation and lesson editing
**Domain:** `telegram/api/`, `telegram/dialogs/lessons/` (existing) · **Type:** bot

**What to do**
- Add a typed client method and models in `telegram/api/` for Task 8.
- Replace the typed start-time step in `LessonScreens.start_time`
  ([creation.py](../../telegram/dialogs/lessons/creation.py)) and the
  `time_input` step for `edit` and `move` in
  [management.py](../../telegram/dialogs/lessons/management.py) with a paginated
  grid of time buttons (`Group(width=4)`, 15-minute grid, page size consistent with
  `shared/pagination.py`). A `Show 5-minute times` / `Show 15-minute times`
  toggle button switches the grid step (default 15; the choice resets to 15 when
  the flow starts and persists across date/duration changes inside one flow).
  After a date is accepted, load slots for that date and
  show them; selecting a slot reuses the existing `resolve_local_interval` and
  review path.
- Keep a `Enter time manually` fallback button that leads to the current typed
  prompt (unchanged validation), so unusual times or an unavailable slot service
  do not block the user; show a clear empty state (`No free times on <date>. Choose another date.`)
  with `Change date`.
- Preserve `Change date`, `Cancel`/`Back`, `Change duration` (duration change must
  reload slots because availability depends on it), and Back restoration after
  date/time edits from review.
- Backend outage while loading slots uses the existing error feedback with retry;
  do not cache slot lists in the offline schedule cache.

**Acceptance Criteria**
- [ ] After entering a date the user sees available times as buttons; tapping one
      reaches the review screen with the right local/UTC interval.
- [ ] Empty days, pagination, 15/5-minute toggle, manual fallback, duration
      change, DST fold labels and outage behavior tested through the recording
      transport.
- [ ] Edit and move of existing weekly lessons offer slots (excluding the event
      itself).
- [ ] ruff/mypy pass.

**Suitable for:** AI agent after Task 8.

### 10. Bot: slot picker for breaks
**Domain:** `telegram/dialogs/breaks.py` (existing) · **Type:** bot

**What to do**
- Use the same slot-grid component (including the 15/5-minute toggle) as Task 9
  for `BreakScreens.start_time` (break creation and update). After a start is
  picked, `BreakScreens.end_time` shows the valid end buttons from Task 8 instead
  of a typed end time. Keep the manual fallback (Open question 1).
- Break update starts from a populated review; keep the finite/null cutoff,
  legacy interval and recurrence mode/timezone preservation from the
  event-editor consistency plan.
- Extract the grid builder into `telegram/shared/` rather than copying it between
  `lessons/` and `breaks/`.

**Acceptance Criteria**
- [ ] Break create/update offers start and end buttons inside work hours with no
      overlapping options; the displacement preview/confirmation still works for
      the manual fallback and is unchanged.
- [ ] Shared component covered by unit tests; ruff/mypy pass.

**Suitable for:** AI agent after Tasks 8–9.

### 11. Verification and documentation
**Domain:** docs, tests · **Type:** documentation

- Run `uv run pytest -q tests`, `uv run ruff check telegram tests`,
  `uv run ruff format --check telegram tests`, `uv run mypy telegram`, plus
  `ruff check backend`, `ruff format --check backend`, `mypy backend`. Do not
  claim PostgreSQL or live-Telegram verification unless it was performed. The
  concurrency check (`tests/test_schedule_concurrency.py`) needs PostgreSQL.
- Update AGENTS.md (Telegram bot and Schedule rows, "Scheduling and Telegram
  handoff": whole-day vacations, no break-delete confirmation, slot picker,
  command-message deletion) and the superseded plans listed above. Check
  README.md for matching bot-flow text and update it only if present.
- Add a short execution record to this plan when done and delete `todo.txt`.

## Out of scope for this release
- Schema changes, offline schedule cache changes, HTMX frontend.
- Lesson and vacation delete confirmations (decision: they stay).
- Changing `Personal lesson` in the API, schedule screens or lesson creation.
- Migrating or backfilling existing vacations.
- Reserving/holding slots or notifying about freed slots.
- Any change to the one-year open-ended validation, break-displacement
  confirmation API or vacation display-only semantics.

## Decisions recorded (2026-11-18, from the open questions)
1. Only breaks lose the delete confirmation; lesson and vacation deletes keep it.
2. The "recurrence change clears cancellations" sentence is removed with no
   replacement.
3. Existing vacations are not changed or migrated; list and detail show only
   dates for old and new vacations alike.
4. The vacation list is a single column.
5. Slot grid is 15 minutes by default, with a button to switch to a 5-minute
   grid (no other steps).
6. Break start and end are both picked from buttons; break slots are limited to
   the teacher's work hours and never offer a time that would overlap an
   existing event.
7. Students see only slots allowed by the same write rules (teacher work hours,
   limits, conflicts) and never the teacher's other events.

## Open questions
1. Task 10: with non-overlapping break slots the break-displacement
   confirmation is reachable only through the manual-entry fallback (and open-ended
   weekly breaks whose later occurrences overlap lessons). Keep the
   `Enter time manually` fallback on breaks (plan default; it keeps the
   displacement feature reachable) or remove it from breaks?
2. Task 4: saving an edit to an old vacation re-saves it as whole days
   (the flow has no time field), which moves a non-midnight start to 00:00 of its
   date. Acceptable, or should editing be blocked for such vacations? Plan
   default: acceptable.


## Execution record (2026-11-18)

Tasks 1–10 were implemented in order, one commit each, then verified together
(Task 11). Open questions used the plan defaults: breaks keep `Enter time
manually` (it is the only way to reach displacement from the bot), and saving an
edit of an old vacation re-saves it as whole days.

| Task | Result |
|---|---|
| 1 | Open-ended break detail/review/displacement text is `No end date`; the note is deleted. |
| 2 | Teacher list shows `Lesson` for own events and no cancellation suffix; the clears-cancellations sentence is removed. |
| 3 | `Delete break` deletes in one tap; confirm screen, `begin_delete` and `confirm_text` removed. Unknown outcome, stale revision, 404 and double-tap tests added. |
| 4 | Vacations are date-only (start date → end date → review, local midnight to the following midnight); the end-date prompt and review/detail/list labels use short months; the dead start-time code is removed. |
| 5 | Vacation list and assignee picker use `Column(Select(...))` (the picker previously rendered in one row). |
| 6 | `GET /api/v1/vacations/upcoming` (declared before `/{vacation_id}`) reuses `list_upcoming_managed_events`; the bot uses it with a last-page fallback. `/vacations/` is unchanged. |
| 7 | An outer message middleware deletes private-chat command messages silently; `/start` sends and deletes a `ReplyKeyboardRemove` message. The recording transport keeps that transient pair out of `calls` (see `keyboard_removal_calls`). Removal of a real client's stale keyboard needs a manual Telegram check. |
| 8 | `GET /api/v1/events/available-start-times` and `/available-break-end-times`, built on a shared `evaluate_schedule_candidates` extracted from `validate_schedule_change` (writes use the same code). |
| 9 | Slot grid (`telegram/shared/time_slots.py`) for lesson creation and for edit/move in lesson management, with a typed fallback state. |
| 10 | The same grid for break start and end in create and update; legacy intervals with seconds or a different end date keep typed entry. |

Deviations from the plan text:
- The `recurrence` query parameter was not added: availability always evaluates
  the first occurrence, so the parameter would have been ignored. Weekly forms
  call the same endpoint.
- Break end options are clock-aligned grid times after the start (not
  start-relative), so an off-grid 5-minute start still gets 15-minute ends.
- `step_minutes` is validated by the service (422 for anything but 5 or 15)
  because a `Literal[5, 15]` query parameter rejects the string `"5"`.
- Student requests omit `duration_minutes`; a mismatching duration is a 422, as
  on write.
- The `todo.txt` request to add a blank line before the timezone footer was
  already satisfied by earlier work and is not part of this plan.

Verification (automated only): `uv run pytest -q tests` passes except
`test_notification_delivery.py::test_notification_service_fails_closed_when_not_configured`
(fails identically on the previous commit: 401 instead of 503 in this
environment) and an intermittent `test_bot_schedule_dialogs.py` registration
failure that also occurs on the pre-change baseline (4 of 25 runs).
`ruff check`/`ruff format --check` on `backend telegram tests`, `mypy backend`
and `mypy telegram` pass. `tests/test_schedule_concurrency.py` needs PostgreSQL
and was not run. The availability parity tests post every probed time to the
real lesson/break create endpoints and compare acceptance with the offered
slots. No migration was needed.
