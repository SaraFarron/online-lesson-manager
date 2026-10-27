# Telegram interface streamlining — Plan

## Goal

Make Telegram lesson booking default-first and make all bot-generated dates,
times, and schedule screens concise and human-readable without changing stored
instants, booking rules, or authorization.

**Status:** Implemented and verified. All nine tasks are complete; see the
execution record below.

## Scope (README.md sections covered)

- [Bot interface](../../README.md#интерфейс-бота): edit-in-place navigation,
  day-first date entry, and removal of successfully consumed date messages.
- [Settings](../../README.md#старт): use the saved teacher lesson duration
  without adding a mandatory duration step.
- [Schedule changes](../../README.md#изменение-расписания): teacher personal
  lesson defaults, optional assignee/duration changes, and compact event
  creation/management summaries.
- [Schedule viewing](../../README.md#просмотр-расписания): abbreviated labels,
  timezone footer, clean empty Today view, no page-count text or Refresh button.
- [Homework](../../README.md#домашние-задания): apply the same presentation
  rules to the existing upcoming-lesson picker, not new homework capabilities.

This request explicitly supersedes the README's month-first input grammar and
the offset-heavy review contract in the earlier
[Telegram scheduling plan](./telegram-scheduling-and-settings-ui.md). Update
the current handoff when shipping; do not rewrite historical verification as
though it tested this new interface.

## Baseline and reusable building blocks

- [AGENTS.md Feature Status](../../AGENTS.md#feature-status-vs-product-spec)
  records implemented booking, management, settings, and live schedules.
  These are presentation/navigation changes, not missing domain features.
  The current file has no dedicated “Known Issues / Tech Debt” section.
- [Lesson creation](../../telegram/lesson_dialogs.py) currently opens the
  teacher student picker, then lesson kind, then duration. Students already
  bypass the subject and duration steps. `submit_lesson` omits `end` for
  default-duration writes so the backend remains authoritative.
- [Input helpers](../../telegram/inputs.py) own `parse_schedule_date`,
  `ParsedScheduleDate`, `format_schedule_date_confirmation`,
  `combine_schedule_date_time`, and `format_local_interval_review`. Extend
  these shared boundaries instead of creating per-dialog date grammars.
- [Management](../../telegram/event_management_dialogs.py),
  [breaks](../../telegram/break_dialogs.py), and
  [vacations](../../telegram/vacation_dialogs.py) also contain local datetime
  formatters, date prompts, inclusive cutoffs, and date-input handlers.
- [Homework creation](../../telegram/homework_teacher_dialogs.py)
  `_load_lesson_options` formats schedule-linked dates independently, including
  the error-path week label. Internal `starts_at` strings identify exact
  occurrences and must remain machine-readable.
- [Schedule formatting](../../telegram/schedule_format.py)
  `format_schedule_pages`, `_day_chunks`, `_render_item`, and `_render_page`
  already handle HTML escaping, whole-row packing, and a 3,500 UTF-16-unit
  budget. Preserve these safeguards.
- [Schedule dialogs](../../telegram/schedule_dialogs.py) already separate
  page navigation from previous/next-week navigation. The Refresh button is
  also used for outage recovery, so its removal requires updated recovery copy
  and tests, not just removing a widget.
- [Runtime](../../telegram/runtime.py) `EditInputMiddleware` selects
  `ShowMode.EDIT`; it does not explicitly delete accepted date messages.
  There is no shared date-message deletion helper in the inspected handlers.
- Preserve `BackendClient.resolve_local_interval` in
  [the typed client](../../telegram/api.py) and the existing backend
  `resolve_local_datetime` in
  [event time utilities](../../backend/events/time_utils.py). Keep
  `prepare_lesson_write` in
  [booking policy](../../backend/events/booking_policy.py) authoritative.
  Do not add bot-side conflict/recurrence validators or wire the generic
  `validated_event_data` dependency as a shortcut.
- Existing plan/status prose contains historical implementation assumptions.
  Use the live modules above as the implementation baseline; do not recreate
  APIs or dialogs that earlier plans describe as pending.

## Approved presentation and behavior contract

Decisions supplied in the request or confirmed during planning:

- Teacher **Add lesson** opens **One time / Weekly** immediately, with the
  authenticated teacher as the personal-lesson assignee. Explain the default
  and provide **Change assignee** on that screen.
- Choosing either kind opens the start-date prompt directly. Show the saved
  default duration numerically, for example “Uses the default duration:
  60 minutes”, and a teacher-only **Change duration** action. Do not hard-code
  60: it is only an example. Show the actual selected duration after an override.
- Optional picker/duration screens remain reachable. Preserve the draft when
  entering, cancelling, or returning from them; selecting an override must
  not silently reset the lesson kind or other confirmed fields.
- Dates accept `DD MM`, `DD:MM`, or `DD/MM`, with an optional four-digit year
  using the same separator. Do not retain ambiguous month-first fallback:
  `05/11` means November 5. Preserve next-year inference and explicit
  confirmation, calendar validation, and viewer-local “today”.
- After a date is successfully parsed and accepted into the draft, delete
  that input message, including when the next step is inferred-year
  confirmation. Invalid/rejected dates are not consumed by this requirement.
  This does not broaden cleanup to arbitrary times, homework text, or files.
- Ordinary event summaries use full English month names, unpadded days, and
  24-hour clocks: `New lesson at November 5 15:00 - 16:00`.
  Autobreak copy is `Autobreak after new lesson: 5 minutes`; do not repeat
  the automatic-break interval. Preserve weekly context with “after each
  lesson” where applicable, and represent zero minutes without implying a
  reservation exists.
- A standalone datetime reads `November 5 at 9:00`; outside the viewer's
  current local year it reads `November 5 at 9:00 (2027)`. The request's
  November 11 example for `2026-11-05` is treated as an illustrative typo:
  formatting must preserve the actual date, not change November 5 to 11.
- Same-local-date intervals show the date once. Crossing local midnight
  shows both dates, e.g. `November 5 23:30 - November 6 0:30`.
  Include the year on endpoints outside the current local year; make
  December/January boundaries unambiguous. Never pad a day as `05`.
- No bot-generated visible ISO datetimes, raw UTC intervals, or per-time
  `(UTC+07:00)` annotations on any screen. A single IANA timezone label per
  message is sufficient where context is needed; schedule timezone labels
  belong at the end. API payloads, callback values, sorting keys, and draft
  serialization retain their existing formats.
- For repeated DST hours only, use **first occurrence / second occurrence**
  labels to distinguish instants without restoring UTC-offset clutter.
  Local-time resolution behavior itself does not change.
- Schedule headings use lowercase short weekdays (`mon`, `tue`, `wed`,
  `thu`, `fri`, `sat`, `sun`) and short month names (`Jan`, `Feb`, `Mar`,
  `Apr`, `May`, `Jun`, `Jul`, `Aug`, `Sep`, `Oct`, `Nov`, `Dec`).
- Empty **Today** shows its title/date and “No events”, with neither a
  timezone nor a NOW divider. Nonempty Today retains NOW; weekly empty-day
  visibility remains intact.
- Remove page-count text such as `Page 1/1` from schedule-related displays,
  but retain Previous/Next controls when content genuinely overflows.
  This overflow safeguard was explicitly approved during planning. Do not
  truncate or drop events to force a single message.
- Remove **Refresh** from Today and This week, including when opened after
  a write. Reopening from home, switching period, or changing week still
  loads current data. Do not remove unrelated management refresh actions or
  unknown-write-outcome **Reload schedule** safeguards.

## Dependencies and common verification

| Task | Prerequisites |
|---|---|
| 01 Shared human date/time formatting | None |
| 02 Day-first date grammar and prompts | 01 |
| 03 Accepted date-message cleanup | 02 |
| 04 Default-first teacher lesson creation | None |
| 05 Compact event summaries | 01, 04 |
| 06 Homework lesson-picker presentation | 01 |
| 07 Compact schedule rendering | 01 |
| 08 Schedule controls and recovery | 07 |
| 09 Cross-flow verification and handoff | 02, 03, 04, 05, 06, 07, 08 |

Every task includes its focused tests; Task 09 is integration verification,
not permission to defer testing. Use existing real SQLite/backend fixtures and
`BotDriver` / `RecordingSession` in [bot support](../../tests/bot_support.py).
Mock only external Telegram transport. No live bot or developer database is
needed for automated verification.

**Migrations:** None expected in any task. No schema, API contract, new setting,
or dependency changes are required. If implementation reveals a real schema
need, stop and amend the plan rather than quietly adding one; any approved
migration must be static, reversible, and named `YYYY-MM-DD_slug.py`.

Run the smallest relevant pytest selection for each task. At milestone
completion, run `uv run ruff check telegram tests`,
`uv run ruff format --check telegram tests`, and `uv run mypy telegram`.
Run backend lint/type checks only if backend code actually changes, which is
not planned. Documentation-only planning requires no application test run.

## Task 01: Establish shared human date/time formatting

**Domain:** [Telegram inputs](../../telegram/inputs.py) (existing).
**Type:** bot. **Dependencies:** None.

### Context

The existing interval review exposes raw UTC timestamps and repeated offsets.
Other dialogs duplicate local formatters. See the approved contract above and
[bot-interface requirements](../../README.md#интерфейс-бота).

### What to do

- Add small shared date, datetime, and interval presentation helpers in the
  existing input module, with a compact schedule-label variant rather than
  parallel implementations in each dialog.
- Take an explicit reference date/current year at the formatting boundary.
  Derive it once per screen in the saved viewer timezone. Schedule rendering
  uses the response's `generated_at` for deterministic snapshot context.
- Convert instants before comparing dates/years. Format interval endpoints
  with one date on the same local day and two dates across midnight.
- Implement minimal first/second-occurrence annotations for ambiguous local
  hours. An actual elapsed hour across a fold must not appear as an unexplained
  zero-length interval.
- Update `format_schedule_date_confirmation` and
  `format_local_interval_review` to delegate to the helpers, retaining
  positive-duration/overflow checks. Update directly affected assertions now.

### Reuse

`ParsedScheduleDate`, `LocalIntervalResolution`, `ZoneInfo`, and the existing
`format_local_interval_review` duration-to-UTC-end calculation. Do not
reimplement backend DST resolution.

### Acceptance Criteria

- [x] Exact examples cover November 5, day 5 rather than 05, optional years,
  same-day intervals, midnight, year boundary, and different viewer timezones.
- [x] DST gap/fold examples remain correct, with no ISO timestamp or UTC-offset
  display; repeated instants are distinguishable in prose.
- [x] Existing duration validation remains covered in
  [input tests](../../tests/test_scheduling_inputs.py).
- [x] Focused tests and applicable common checks pass; no migration.

### Out of scope

Navigation, parser grammar, backend temporal rules, new localization machinery.

### Suitable for

AI agent.

## Task 02: Switch every date prompt to day-first input

**Domain:** [Telegram inputs](../../telegram/inputs.py) and existing date-prompt
callers. **Type:** bot. **Dependencies:** 01.

### Context

The README and old tests currently document month-first input; the new request
intentionally replaces that convention across all date-entry screens.

### What to do

- Change `_DATE_PATTERNS` and parser error guidance to day/month/optional year.
- Update lesson start/cutoff, management edit/move/cutoff, break start/cutoff,
  and vacation start/end prompts, including prompts constructed dynamically
  when returning from edits.
- Keep time grammar separate and unchanged. Preserve inclusive UI dates and
  exclusive API cutoffs, leap-day behavior, inferred-year confirmation, and
  rejection of invalid/unrepresentable dates.
- Update date-entry fixtures in affected bot tests deliberately; do not
  globally swap digits in API timestamps or ISO serialization.

### Reuse

`parse_schedule_date`, `_calendar_date`, `ParsedScheduleDate`, existing
`_parse_local_date` wrappers, and shared confirmation formatting from Task 01.

### Acceptance Criteria

- [x] `05 11`, `05:11`, and `05/11` all mean November 5; explicit-year forms
  and unpadded input work.
- [x] Ambiguous `04/03` means March 4; `11/25` fails rather than falling back.
- [x] Leap dates, explicit past years, year rollover confirmation, and all
  date-entry flows have focused regression coverage.
- [x] No user-facing month-first prompt remains in the Telegram source.
- [x] Focused tests and applicable common checks pass; no migration.

### Out of scope

ISO API query/payload grammar, time entry changes, new date separators.

### Suitable for

AI agent.

## Task 03: Delete successfully consumed date messages

**Domain:** Existing Telegram date handlers and
[bot transport tests](../../tests/bot_support.py).
**Type:** bot. **Dependencies:** 02.

### Context

Editing the bot screen does not delete the user's date message. The current
`EditInputMiddleware` must not become a blanket deletion policy for all inputs.

### What to do

- Introduce one narrowly scoped async cleanup helper following existing
  Telegram patterns, and call it from successful date handlers in lesson,
  management, break, and vacation flows.
- Retain/pass the incoming `Message` through wrappers such as vacation
  `_date_input`; delete that exact message only after parsing and relevant
  field-level validation have succeeded and the draft has accepted the date.
- Delete accepted inferred-next-year input before showing its confirmation,
  but preserve the confirmation itself. Invalid dates and rejected cutoffs
  retain the prompt/error and do not advance.
- Handle only expected Telegram deletion failures explicitly, with
  repository-standard logging and an actionable inline notice when cleanup
  cannot happen. Preserve the accepted draft and avoid duplicate messages or
  backend writes. Do not broadly catch errors or silently report success.
- Extend `RecordingSession` with targeted deletion-failure injection; normal
  recorded deletion calls can already be observed via `session.calls`.

### Reuse

`Message.delete`, `ShowMode.EDIT`, existing date handlers, per-user isolation,
`BotDriver`, and `RecordingSession`.

### Acceptance Criteria

- [x] Successful date entry in each flow records exactly the intended
  `deleteMessage` target and edits the existing bot screen.
- [x] Inferred-year confirmation still occurs; invalid/rejected input does
  not advance or trigger accepted-input cleanup.
- [x] Expected deletion failures are visible/logged without losing the draft;
  unrelated Telegram errors are not swallowed.
- [x] Homework submissions, files, commands, and unrelated text are untouched.
- [x] Focused tests and applicable common checks pass; no migration.

### Out of scope

Deleting chat history or all text/time inputs.

### Suitable for

AI agent.

## Task 04: Make teacher lesson creation default-first

**Domain:** [Lesson creation dialog](../../telegram/lesson_dialogs.py)
(existing). **Type:** bot. **Dependencies:** None.

### Context

Teacher personal assignment and saved duration already have backend support;
the change is to default them and make overrides optional.

### What to do

- In `open_lesson_creation`, initialize personal `subject_id` to the teacher's
  account ID, label it clearly, copy the saved duration, and start at `kind`.
- Do not fetch the student picker until **Change assignee** is pressed.
  Failure of an optional picker read must not prevent personal booking.
- Keep the authorized paginated student picker and personal-lesson choice.
  Track the return screen as needed so changing an assignee from kind or review
  returns there instead of unexpectedly restarting the form.
- Change `_choose_kind` to proceed directly to `start_date` using the selected
  duration, initially the teacher default. Add the actual duration/default
  explanation and teacher-only **Change duration** button to that prompt.
- Reuse optional default/custom duration screens. Preserve kind, assignee,
  entered fields, and previously selected custom duration when navigating;
  **Use default** explicitly resets only the override.
- Ensure changes made from review invalidate/recompute derived resolution
  and cutoff state only when required. Never submit stale preview endpoints.
- Preserve student self-booking and teacher-only override permissions.
  Keep default-duration writes omitting `end`, and custom writes sending it.

### Reuse

`edit_subject`, `_store_picker_page`, `select_student`,
`choose_personal_lesson`, `use_default_duration`, `duration_input`,
`submit_lesson`, and existing typed payloads.

### Acceptance Criteria

- [x] Teacher Add lesson -> kind -> start date, without mandatory subject or
  duration screens and without an eager picker request.
- [x] Kind explains personal assignment and exposes an assignee override.
  Start date shows the saved numeric default, not a hard-coded value.
- [x] One-off and weekly personal/student lessons support override, Back,
  Cancel, and review edits without erasing unrelated draft values.
- [x] Student screens expose neither assignee nor duration overrides; forged
  requests remain subject to unchanged backend authorization.
- [x] [Lesson creation tests](../../tests/test_bot_lesson_creation.py) verify
  screen sequence and actual request bodies, including omitted default `end`.
- [x] Focused tests and applicable common checks pass; no migration.

### Out of scope

Changing profile defaults, booking policies, or assignment authorization.

### Suitable for

AI agent.

## Task 05: Compact event creation and management summaries

**Domain:** Existing lesson, event-management, break, and vacation dialogs.
**Type:** bot. **Dependencies:** 01, 04.

### Context

The request covers creation, update, and other user-visible datetime displays,
not only the initial lesson review.

### What to do

- Replace local timestamp duplication in `lesson_data`,
  `_format_datetime`, `_automatic_break_text`, `_format_detail`,
  `_review_text`, list/occurrence labels, cancellation details, displacement
  previews, vacation summaries, and confirmation/success text with Task 01's
  helpers. Inspect the complete visible-data paths, not only `.isoformat()`.
- Render the ordinary lesson review as the approved compact lesson interval
  and duration-only autobreak text. Do not repeat start/end dates on one day.
- Keep necessary assignee, weekly cutoff, hidden/cancelled state, move
  From/To information, and destructive-action warnings. Simplification must
  not hide which exact occurrence will be changed or displaced.
- Format inclusive weekly/vacation dates human-readably without changing
  exclusive API boundaries. Keep teacher recurrence timezone context only
  where it explains behavior distinct from the viewer timezone.
- Audit bot-generated error/notice interpolation for datetime leakage; do
  not rewrite arbitrary user-authored lesson/homework text.

### Reuse

Task 01 helpers; existing typed management details and previews; existing
revision, cancellation, displacement-confirmation, and unknown-outcome logic.

### Acceptance Criteria

- [x] The normal example renders `New lesson at November 5 15:00 - 16:00`
  and `Autobreak after new lesson: 5 minutes`, without raw UTC intervals or
  repeated offset/date annotations.
- [x] Other-year, midnight, DST fold, weekly cutoff, zero-break, and moved
  occurrence examples stay correct and concise.
- [x] Create/edit/delete/cancel/displacement screens have no bot-generated
  ISO datetime or numeric-only calendar date leakage.
- [x] Existing lesson-management, break-management, and vacation-management
  tests retain backend-effect and confirmation assertions, updated only for
  intentional input/presentation changes.
- [x] Focused tests and applicable common checks pass; no migration.

### Out of scope

Changing mutation semantics, removing safety warnings, altering recurrence.

### Suitable for

AI agent.

## Task 06: Humanize homework lesson-picker dates

**Domain:** [Teacher homework creation](../../telegram/homework_teacher_dialogs.py)
(existing). **Type:** bot. **Dependencies:** 01.

### Context

The existing exact-upcoming-lesson picker is another schedule-related surface.
Its visible labels currently contain numeric dates and timezone abbreviations.

### What to do

- Use compact weekday/month/date helpers for upcoming lesson options and
  week-range labels, including the backend-error fallback label.
- Keep timezone context once at the end where needed. Remove schedule
  lesson-picker page-count text while preserving bounded option navigation.
- Audit the existing student and teacher homework detail getters for any
  bot-generated visible timestamps; format any exposed ones consistently,
  without introducing new timestamp fields or screens.
- Preserve internal `starts_at`, exact source IDs/kinds, chronological sorting,
  selected-occurrence payloads, and authorized student filtering.

### Reuse

`_load_lesson_options`, existing week/option navigation, Task 01 helpers,
and `BackendClient.read_schedule_week`.

### Acceptance Criteria

- [x] Successful and failed picker loads have human-readable date labels
  without ISO dates, padded days, or repeated timezone suffixes.
- [x] Next/previous week and overflow option controls still work; no page
  count is displayed for the schedule-linked picker.
- [x] [Homework creation tests](../../tests/test_bot_homework_creation.py)
  still verify the exact original UTC occurrence in the request body.
- [x] Unrelated homework/student-picker pagination and user text are unchanged.
- [x] Focused tests and applicable common checks pass; no migration.

### Out of scope

Homework workflow changes or new notification/date-input features.

### Suitable for

AI agent.

## Task 07: Simplify schedule text without losing overflow safety

**Domain:** [Schedule formatter](../../telegram/schedule_format.py) (existing).
**Type:** bot. **Dependencies:** 01.

### Context

The formatter currently repeats timezone context, spells out dates, includes
NOW on an empty current day, and appends a page counter to every page.

### What to do

- Replace headings and displayed snapshot time with human-readable compact
  labels. Retain the useful snapshot timestamp concisely without repeating
  timezone; place the single timezone label last on each applicable page.
- Remove `_render_page`'s page-count footer and its dedicated reserve;
  calculate the actual header/footer budget instead.
- Special-case empty Today to omit both timezone and NOW. Keep all seven
  weekly headings and existing status/visibility semantics.
- Remove repeated UTC-offset annotations, using the approved first/second
  labels only for repeated DST times. Use explicit dates when a row crosses
  local midnight, rather than only `(+1 day)`.
- Audit schedule-related management list counters such as “Showing ... of ...”
  and remove pagination-status copy there as well, retaining true event
  facts such as cancellation counts. Student/assignee and homework-assignment
  pickers are not schedules and retain their pagination.
- Preserve whole rows, safe HTML escaping, teacher/student labels, bounded
  label length, and navigation-accessible overflow pages.

### Reuse

`format_schedule_pages`, `_day_chunks`, `_pack_chunks`, `_safe_label`,
UTF-16 measurement, and Task 01 compact helpers.

### Acceptance Criteria

- [x] Headers contain `mon, Nov 5`-style labels with correct real weekdays,
  unpadded days, and years only when required.
- [x] A nonempty page ends in its single timezone label. Empty Today has
  “No events”, no timezone, and no NOW divider.
- [x] No schedule page-count text remains, including multi-page output.
- [x] Long schedules still expose every event in order; every rendered page,
  including its footer and DST labels, stays within 3,500 UTF-16 units.
- [x] [Formatter tests](../../tests/test_schedule_format.py) cover exact
  output, HTML/Unicode boundaries, empty day/week, midnight, year and DST.
- [x] Focused tests and applicable common checks pass; no migration.

### Out of scope

Removing pagination internals, truncating events, backend schedule filtering.

### Suitable for

AI agent.

## Task 08: Remove schedule Refresh and retain explicit recovery

**Domain:** [Schedule dialog](../../telegram/schedule_dialogs.py) (existing).
**Type:** bot. **Dependencies:** 07.

### Context

Refresh currently drives both normal reloads and error recovery. Normal
schedule views must lose this button without leaving dead-end outage screens.

### What to do

- Remove the Refresh widget from Today/This week and remove its now-unused
  handler if it has no live callers. Keep `_refresh` as the shared loader for
  period/week transitions, or rename it coherently if needed.
- Keep Today/This week switching, previous/next week, Back to home, and
  conditional overflow-page controls. Reopening from home must fetch again.
- Replace “Refresh” recovery guidance with the available route: return home
  and reopen the desired schedule, or use an available period/week transition.
  Do not add a substitute Refresh/Retry control to the successful views.
- Preserve unknown-write **Reload schedule** actions elsewhere. Verify they
  open the new schedule layout and still reconcile uncertain writes safely.
- Adapt schedule freshness tests to mutate backend state and then reopen or
  navigate; do not weaken live-data assertions just because Refresh is gone.

### Reuse

`_load_schedule`, `_open_schedule`, `_refresh`, period/week navigation,
`ScheduleDialogState`, and existing backend-unavailable handling.

### Acceptance Criteria

- [x] No Refresh button appears on Today or This week for either role.
- [x] Home -> reopen, period switching, and week navigation fetch current
  data and edit the active message; page navigation preserves all rows.
- [x] Backend/invalid-response errors show no stale rows and give a working
  recovery route. Reopening after recovery loads valid data.
- [x] Midnight rollover, timezone/profile changes, inactive accounts, and
  selected-week navigation retain their existing tested semantics.
- [x] [Schedule dialog tests](../../tests/test_bot_schedule_dialogs.py) and
  [integration tests](../../tests/test_bot_schedule_integration.py) pass.
- [x] Applicable common checks pass; no migration.

### Out of scope

Background refresh, offline cache, unrelated management Refresh buttons.

### Suitable for

AI agent.

## Task 09: Verify cross-flow behavior and update the current handoff

**Domain:** Existing Telegram integration tests and project documentation.
**Type:** bot. **Dependencies:** 02–08.

### Context

Shared date grammar and formatting affect multiple existing dispatcher flows.
The current README and feature-status text must not retain the old contract.

### What to do

- Exercise teacher default-personal booking, optional student/custom-duration
  booking, student self-booking, weekly cutoff confirmation, edit/move,
  manual break displacement, vacation dates, and homework lesson selection.
- Verify accepted date cleanup and edit-in-place screen identity across flow
  changes; retain stale-callback/restart and unknown-write recovery coverage.
- Assert final rendered bot text/keyboards, not just helper output: no raw
  timestamps, repeated offsets, schedule page counters, or schedule Refresh.
  Exclude internal payloads and user-provided content from presentation bans.
- Update the relevant [README sections](../../README.md) and
  [AGENTS.md feature status](../../AGENTS.md#feature-status-vs-product-spec).
  Link this plan from the current scheduling handoff; mark its contract as
  superseding old month-first/offset/Refresh presentation only.
- Record verified task status and actual commands/results in this plan
  during implementation. Do not claim manual live-Telegram verification
  unless it was performed.

### Reuse

[Bot cross-flow tests](../../tests/test_bot_cross_flow.py), existing domain
test files, `BotDriver`, `RecordingSession`, and isolated real-backend fixtures.

### Acceptance Criteria

- [x] The combined affected input, creation, management, schedule, homework,
  runtime, and cross-flow test selection passes.
- [x] Tests check API payload identity and persistent backend results, proving
  that presentation changes did not move events or change their duration.
- [x] Common Ruff/format/mypy checks pass; unrelated baseline failures, if any,
  are reported explicitly rather than fixed as incidental scope.
- [x] Current documentation describes day-first entry, optional overrides,
  cleanup, compact dates, overflow-only navigation, and no schedule Refresh.
- [x] No schema/dependency change or migration was introduced.

### Out of scope

Reimplementing existing features or reconciling all historical plans.

### Suitable for

AI agent, with optional human live-chat UX verification.

## Execution record

| Task | Status | Commit | Focused verification |
|---|---|---|---|
| 01 Shared date/time formatting | Done | `05e01b2` | 29 focused input tests; Ruff, format, and mypy passed. |
| 02 Day-first date grammar | Done | `3756aad` | 71 affected flow tests; Ruff, format, and mypy passed. |
| 03 Accepted date cleanup | Done | `eb9ab68` | 44 affected bot tests; Ruff, format, and mypy passed. |
| 04 Default-first lesson creation | Done | `ece1f28` | 13 focused bot tests; Ruff, format, and mypy passed. |
| 05 Compact event summaries | Done | `d5bcd2c` | 74 date/management tests; Ruff, format, and mypy passed. |
| 06 Homework lesson picker | Done | `b7c86d2` | 17 picker/student tests; Ruff, format, and mypy passed. |
| 07 Compact schedule rendering | Done | `a98192b` | 51 schedule/management tests; Ruff, format, and mypy passed. |
| 08 Schedule recovery without Refresh | Done | `a56934f` | 22 schedule/integration/write-recovery tests; Ruff, format, and mypy passed. |
| 09 Cross-flow and handoff | Done | See implementation commit | 146 combined tests; required common checks passed; no live Telegram session used. |

Task 09 combined verification:

```shell
uv run pytest -q \
  tests/test_scheduling_inputs.py \
  tests/test_bot_onboarding.py \
  tests/test_bot_flows.py \
  tests/test_bot_lesson_creation.py \
  tests/test_bot_lesson_management.py \
  tests/test_bot_break_management.py \
  tests/test_bot_vacation_management.py \
  tests/test_bot_settings_dialogs.py \
  tests/test_bot_schedule_dialogs.py \
  tests/test_bot_schedule_integration.py \
  tests/test_schedule_format.py \
  tests/test_bot_homework_creation.py \
  tests/test_bot_homework_student.py \
  tests/test_bot_homework_management.py \
  tests/test_bot_runtime.py \
  tests/test_bot_cross_flow.py
# 146 passed

uv run ruff check telegram tests
# All checks passed
uv run ruff format --check telegram tests
# 66 files already formatted
uv run mypy telegram
# Success: no issues found in 21 source files
```

No schema, dependency, or migration changes were made. Live Telegram UX was
not manually verified; the cross-flow evidence uses the repository's isolated
backend fixtures and recording bot transport.

## Out of scope for this release

- Backend schema, routes, scheduling admission rules, timezone storage,
  recurrence expansion, and authorization changes.
- Notification delivery, HTMX UI, free-slot selection, offline caching.
- Removing overflow safety or pagination from unrelated student/homework lists.
- Blanket deletion of user text, files, or chat history.
- Unrelated stale documentation or dead-code cleanup.

## Open questions

- No blocking product decision remains. Pagination overflow and DST repeated
  hours were explicitly clarified during planning.
- Expected deletion-failure classification must be checked against the pinned
  aiogram/Telegram behavior during Task 03; do not assume every
  `TelegramBadRequest` means a harmless missing message.
- If an additional live date-entry or bot-generated datetime surface is found,
  include it under the same approved contract and focused tests, without
  inventing new screens or widening feature scope.
