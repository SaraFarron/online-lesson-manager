# Schedule viewing: backend → Telegram — Implementation plan

## Goal

A registered teacher or student can open today's schedule or the current Monday–Sunday week in Telegram, see correctly localized and authorized events with a “now” divider, and navigate by editing the active bot message.

This is an agent-ready backlog, not an implementation. Assign one numbered task and its completed prerequisites at a time.

## Scope (README sections covered)

- [Schedule viewing](./README.md#просмотр-расписания): today/week, chronological events, teacher-visible student names, student-only events, and the timeline divider.
- [Bot interface](./README.md#интерфейс-бота): aiogram-dialog edit-in-place navigation.
- [Scheduling foundation handoff](./README.md#scheduling-foundation-handoff): reuse established recurrence, cancellation, timezone, and vacation rules.
- [Architecture](./README.md#архитектура): backend computes the schedule; Telegram formats it. This milestone does **not** complete the separate offline-cache requirement.

No booking, schedule mutation, settings UI, notifications, or new infrastructure is included.

## Current implementation and reuse

Verified against the repository on 2026-10-17:

- [Schedule router](./backend/schedule/router.py) is empty; [backend app](./backend/main.py) does not mount a schedule router.
- [Range service](./backend/events/range_service.py) already provides `load_raw_occurrences(session, teacher_id, range_start, range_end)`. It returns teacher-scoped `Occurrence` objects with cancellation rows applied, uses bulk DB loading, and does not commit.
- [Time helpers](./backend/events/time_utils.py) already provide `local_day_bounds`, `local_week_bounds`, `get_timezone`, and UTC normalization. Day/week boundaries must use the **viewer's saved timezone**, not the teacher's recurrence timezone.
- [Occurrence types](./backend/events/occurrences.py) expose source kind/ID, teacher ID, subject ID, event type, UTC start/end, and stable `(source kind, source ID, UTC start)` identity.
- [Vacation visibility](./backend/events/visibility.py) already provides `filter_vacation_visibility`. Teacher vacations hide all overlapping occurrences, including breaks and vacation rows themselves; student vacations hide only that subject's occurrences. Whole occurrences are hidden on any overlap. Do not replace this with older proposed rules.
- Read ranges are limited to 31 elapsed days and expansion to 10,000 occurrences. Day/week requests fit the range limit; budget/data failures must remain explicit, not become an empty schedule.
- [Approved foundation plan](./SCHEDULING-FOUNDATION-PLAN.md) and the README record completed decisions: naive DB timestamps mean UTC, new recurrence uses the teacher's timezone snapshot, legacy UTC-step series remain unchanged, and cancellation identity is exact. Do not reopen these decisions.
- [Auth dependencies](./backend/auth/dependencies.py) provide `CurrentUser` and `SessionDep`; current-user authentication checks active status. Schedule reads must use **user JWTs**, not the bot service credential.
- [Bot client](./telegram/api.py) already manages a shared httpx client, typed Pydantic models, JWT renewal through `read`, and a circuit breaker. Its `request`/`read` helpers currently have no query-parameter argument, and error wording is registration-oriented.
- [Home dialog](./telegram/dialogs.py) has no schedule buttons. [Runtime](./telegram/runtime.py) registers home/onboarding dialogs, private-chat filters, per-user isolation, stale callback recovery, and edit-input middleware. Reuse them.
- [Bot test support](./tests/bot_support.py) records Bot API methods/message IDs and drives real dispatcher updates. [Combined bot tests](./tests/test_bot_flows.py) already connect that driver to the actual ASGI backend.
- [Visibility tests](./tests/test_event_visibility.py) and [range-service tests](./tests/test_event_range_service.py) are existing regression coverage. Extend/compose them rather than implementing a second engine.

## Proposed viewing contract — Task 00 approval required

The recommendations below fill viewing-specific gaps; they do not alter approved foundation semantics.

### Backend API

Proposed endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/schedule/day?date=YYYY-MM-DD` | One day in the authenticated viewer's timezone; omitted date means today in that timezone. |
| `GET /api/v1/schedule/week?date=YYYY-MM-DD` | Monday–Sunday week containing the supplied local date; omitted date means the current local week. |

- User JWT required; never accept a caller-supplied teacher/user scope. The explicit date is useful to other clients and repeatable tests; it does not require arbitrary-date Telegram navigation.
- Teacher: teacher scope = own account ID. Student: scope = linked teacher ID, output restricted to own subject ID. No implicit admin “view everyone” behavior.
- Proposed unsupported state handling: unlinked student → explicit 400, unsupported role → 403. If the linked teacher is absent or no longer a teacher, fail explicitly. Task 00 decides inactive-teacher behavior; do not silently return another scope or success-shaped empty data.
- Read saved viewer timezone from the current backend account on every request. A dialog's cached onboarding `SessionView` is not authoritative.
- Determine one aware-UTC `generated_at` per request using an injectable/testable clock. Use it both for default local-date selection and event status, avoiding midnight inconsistencies.
- Return canonical UTC instants plus an IANA `timezone` for presentation. Do not send HTML or create separate localized-string truth in the backend.
- A shared response shape should support both periods:
  - `period`: `day` or `week`.
  - `timezone`, `generated_at`, `range_start`, `range_end`.
  - `days`: exactly one or seven ascending local dates, including empty days.
  - Each day contains ordered `items`, with stable source kind/ID/start identity, `event_type`, full UTC `start`/`end`, and status `past` / `ongoing` / `upcoming`.
  - Teacher-facing lesson items include only the approved minimal subject label. Student-facing output excludes other subjects and teacher-only naming metadata.
- Status against the single `generated_at`: `end <= now` = past; `start <= now < end` = ongoing; otherwise upcoming.
- Never expose hidden occurrences, hidden counts/reasons, raw teacher calendars, emails, Telegram IDs, or auth/profile objects in this API.

### Query and grouping order

1. Authenticate active viewer and resolve permitted teacher scope.
2. Compute requested day/week bounds using existing timezone helpers.
3. Load raw teacher occurrences once for the whole range.
4. Apply existing vacation visibility to that teacher-wide input.
5. Apply viewer authorization to visible occurrences.
6. Bulk-load only names needed for the authorized teacher-facing output.
7. Include occurrences only on their viewer-local start date and serialize safe response models.

Do not filter teacher-wide policy input down to the student's rows before applying vacations. Do not perform one DB query per student, occurrence, or displayed day.

Approved cross-midnight presentation: include an occurrence only on its viewer-local start date, retaining its full identity and UTC start/end. Do not repeat or clip it into another day. Half-open local-day boundaries include a start at midnight and exclude a start at the following midnight.

Teacher names: use non-empty `User.full_name`; otherwise display “Unnamed student,” without inventing email/Telegram fallbacks. A teacher's own lesson is labeled “Personal lesson.” The Telegram schedule UI is English.

### Telegram screens

- Add Today and This week buttons to registered TEACHER/STUDENT home screens.
- Each screen has Refresh, switching between Today/This week, and Back to home.
- Recompute “today/current week” on each explicit read; no previous/next date navigation, date picker, or background timers are required here.
- Refresh re-fetches the backend, so names, timezone, cancellations, and vacations update without `/start`.
- Display heading, local date(s), timezone, event type, local interval, teacher-visible lesson subject, and empty-day text.
- Use consistent local time formatting. During a DST repeated hour, include sufficient offset/zone context to distinguish equal-looking local times.
- “Now” divider appears only on the viewer's current day and is based on response `generated_at`; ongoing events must be marked explicitly. A suggested placement is after all ended events and before ongoing/upcoming items. No auto-updating countdown is promised.
- Past/future dates need no “now” divider. A current empty day can still show the current-local-time divider with an explicit empty state.
- Render user names as escaped text. Callback payloads contain only bounded navigation state, never JWTs or raw private event data.
- Normal navigation and refresh edit the active message with `ShowMode.EDIT`; initial render and specifically classified missing/uneditable-message replacement retain existing framework behavior.

### Oversized weekly schedules: approval gate

The README asks for a week in one message, but Telegram text messages have a 4,096-character limit after entity parsing. Long names or dense schedules can exceed it.

Recommended policy:

- A week that fits is shown in one edited message.
- If it cannot fit, paginate **within that same message** using inline page controls, maintaining chronological day headings and clearly displaying page X/Y.
- Split at complete event rows when possible; safely shorten an individual overlong display label if required, while retaining the full name in the backend response.
- Do not truncate whole events, silently drop days, exceed Telegram limits, or send a burst of messages.
- Task 00 must approve this exception to the literal one-message week requirement and settle the display-label cap. If rejected, stop and request an alternative, not a hidden fallback.
- Apply the same safety to unexpectedly large daily views.

### Errors, freshness, and cache boundary

- Unknown/invalid date → normal validation error, not today's schedule. Existing foundation errors remain explicit non-2xx results.
- Token expiry uses existing one-time safe-read renewal; permission/inactive responses never become empty schedules.
- Backend/circuit failure renders a schedule-specific unavailable screen with Retry and Back.
- Do not keep showing old schedule content as though freshly loaded. A remembered page/response inside a dialog is transient UI state, not an offline cache; after failed refresh, show unavailability rather than an apparently current calendar.
- The “now” divider is a snapshot as of `generated_at`. Include a discreet update-time label and Refresh so users are not promised live state.
- Durable/TTL schedule caching and serving stale data during outages remain separate follow-on work.

## Tasks and dependencies

| ID | Task | Prerequisites |
|---|---|---|
| 00 | Approve viewing contract and overflow UX | None |
| 01 | Define typed schedule response and local-day grouping | 00 |
| 02 | Compose authorized schedule reads and bulk subject labels | 01 |
| 03 | Expose authenticated day/week API routes | 02 |
| 04 | Extend Telegram client with typed schedule reads | 03 |
| 05 | Implement safe localized schedule formatting and pagination | 00, 01 |
| 06 | Wire Today/Week dialogs and refresh navigation | 04, 05 |
| 07 | Verify complete backend-to-Telegram flows and document status | 03, 06 |

Task 05 can run alongside backend Tasks 02–03 after the response contract is settled. Separate agents must coordinate shared client/dialog changes; no parallel agent should reinvent backend recurrence or visibility rules.

### Common acceptance criteria for Tasks 01–07

- Reuse foundation helpers and domain composition; no new recurrence, vacation, or cancellation implementation.
- Add focused tests in each task. Final integration tests supplement rather than defer local validation.
- Backend integration tests use real isolated DBs and [existing fixtures](./tests/conftest.py), not mocked scheduling services/databases.
- Bot integration tests reuse [BotDriver/RecordingSession](./tests/bot_support.py); mock only external Telegram transport. Do not call the live Bot API in automated tests.
- Run relevant existing foundation and bot/auth regression tests when shared code changes.
- Run `uv run ruff check --fix backend`, `uv run ruff format backend`, and `uv run mypy backend`. For bot changes, also lint/format/type-check `telegram`; include touched tests in Ruff checks.
- Report unrelated baseline failures honestly; do not repair unrelated booking/infrastructure code.
- No schema/migration is expected. If implementation reveals a genuine required schema change, stop, explain it, and add an approved static `YYYY-MM-DD_slug.py` migration with disposable SQLite/PostgreSQL verification.
- Reads never commit or mutate event/cancellation rows. Never silently omit events, convert engine failure to empty success, expose credentials, or log complete schedules/names.
- Tests fix the clock, assert exact UTC/local dates and response shapes, and measure actual message sizes/edit calls where relevant.

## Task 00: Approve viewing semantics and the Telegram overflow policy

**Domain:** product/API contract
**Type:** planning
**Dependencies:** none

### Context

[README viewing](./README.md#просмотр-расписания) specifies the primary flows but not response shape, multi-day grouping, overflow, or unsupported account states.

### What to do

- Approve endpoint names/query date semantics, exact response fields, unsupported-role/unlinked-student handling, and inactive-linked-teacher behavior.
- Approve start-date-only grouping, status semantics, subject-label fallback, UI language, and per-day empty states.
- Approve the current-day divider placement and snapshot/update-time wording.
- Approve same-message pagination when a whole week/day exceeds Telegram limits, including bounded display-label length.
- Record concrete example responses for teacher/student views of the same date and one DST/midnight case. Mark decisions here before dependent implementation.
- Preserve all approved foundation semantics. Do not reopen vacation self-hiding or recurrence/DST behavior for UI convenience.

### Reuse

README viewing requirements, [foundation handoff](./README.md#scheduling-foundation-handoff), existing bot navigation conventions.

### Acceptance Criteria

- [x] All viewing-specific open decisions have owner approval and exact examples.
- [x] Backend/client agents can agree on one concrete typed response contract.
- [x] The oversized-week exception is explicitly approved or replaced with another approved viable policy.

### Owner-approved contract (2026-10-17)

- Routes are `GET /api/v1/schedule/day` and `GET /api/v1/schedule/week`.
  Optional `date=YYYY-MM-DD` is interpreted in the authenticated viewer's saved
  timezone; omitted dates use one `generated_at` clock instant.
- The response envelope contains `period`, `timezone`, `generated_at`,
  `range_start`, `range_end`, and ascending `days`. A day has `date` and
  `items`. Each item carries `source_kind`, `source_id`, full UTC `start` and
  `end`, `event_type`, and `status` (`past`, `ongoing`, or `upcoming`). A
  teacher-facing lesson may include only the minimal `subject_label`; student
  responses contain only that student's visible schedule and no subject or
  student naming metadata.
- Include an occurrence only on the viewer-local calendar date of its start.
  Day ranges are `[00:00, next-day 00:00)` and week ranges are
  `[Monday 00:00, next Monday 00:00)`. A cross-midnight event appears once,
  on its start date, with its full UTC start/end; do not duplicate or clip it
  into another day. Event status uses the full interval:
  `end <= generated_at` is `past`, `start <= generated_at < end` is
  `ongoing`, otherwise `upcoming`.
- `ADMIN` receives 403; an unlinked `STUDENT` receives 400; a linked teacher
  that is missing, inactive, or no longer has the `TEACHER` role causes an
  explicit 409. These conditions never become an empty successful schedule.
- Telegram schedule UI is English. Empty/missing student names display as
  `Unnamed student`; a teacher's own lesson displays as `Personal lesson`.
  An empty date displays `No events`. Show the `NOW` divider only on the
  viewer's current local date, after ended events and before ongoing/upcoming
  start-date items. Include `Updated at <local time> (<timezone>)` based on
  the response snapshot.
- Same-message pagination is approved for an oversized day/week. Split only
  between complete event rows, keep all dates/events, and shorten only a
  display label longer than 80 Unicode code points. Keep each message within a
  conservative 3,500 UTF-16-unit budget; never drop a row/day or send a burst
  of messages.

Concrete examples (illustrative UUIDs; timestamps are offset-bearing UTC):

Teacher and student views of the same Bangkok day, 2026-10-19:

```json
{
  "period": "day",
  "timezone": "Asia/Bangkok",
  "generated_at": "2026-10-19T02:30:00Z",
  "range_start": "2026-10-18T17:00:00Z",
  "range_end": "2026-10-19T17:00:00Z",
  "days": [{
    "date": "2026-10-19",
    "items": [{
      "source_kind": "event",
      "source_id": "00000000-0000-0000-0000-000000000101",
      "event_type": "LESSON",
      "start": "2026-10-19T01:00:00Z",
      "end": "2026-10-19T02:00:00Z",
      "status": "past",
      "subject_label": "Alice Example"
    }, {
      "source_kind": "event",
      "source_id": "00000000-0000-0000-0000-000000000102",
      "event_type": "LESSON",
      "start": "2026-10-19T02:30:00Z",
      "end": "2026-10-19T03:30:00Z",
      "status": "ongoing",
      "subject_label": "Bob Example"
    }]
  }]
}
```

Alice's student response for that same day has the same envelope and only the
first item; `subject_label` is absent. It contains neither Bob's lesson nor
student/teacher profile metadata.

On the 2026-03-08 spring-forward day in `America/New_York`, the local day is
the 23-hour UTC range `[2026-03-08T05:00:00Z, 2026-03-09T04:00:00Z)`. A lesson
starting at `2026-03-08T06:30:00Z` (01:30 EST) and ending at
`2026-03-08T07:30:00Z` (03:30 EDT) appears on March 8 with those full UTC
timestamps; at `generated_at=2026-03-08T07:00:00Z` its status is `ongoing`.
An event that started on March 7 and crosses midnight into March 8 is not
included in March 8 because its local start date is March 7.

### Out of scope

Implementation, changing foundation policy, expanding into booking or caching.

### Suitable for

Human owner with AI assistance. Stop dependent work if a required decision remains unresolved.

## Task 01: Define schedule response models and viewer-local grouping

**Domain:** `backend/schedule/` (existing stub)
**Type:** schemas/service
**Dependencies:** 00

### Context

The foundation returns UTC occurrences; [README viewing](./README.md#просмотр-расписания) needs ordered local days, event status, and a stable client contract.

### What to do

- Add minimal response models in `backend/schedule/schemas.py` for the approved envelope/day/item/subject shapes, using concrete enums/types.
- Add pure presentation-shaping helpers for one/seven local days, local start-date grouping, status at an explicit `generated_at`, and deterministic ordering.
- Preserve full UTC source identity/start/end while including each occurrence only on the viewer-local date of its start.
- Explicitly shape teacher versus student item metadata so student responses cannot accidentally serialize teacher/raw ORM fields.
- Do not embed escaped HTML, localized dates, or Telegram pagination state in backend models.

### Reuse

`Occurrence`, `OccurrenceSourceKind`, `EventType`, `local_day_bounds`, `local_week_bounds`, `get_timezone`, half-open overlap helpers.

### Acceptance Criteria

- [x] Day result has exactly one date; week has seven dates from Monday, including empty dates.
- [x] Events are included only on their local start date: a midnight-ending event is not shown on the next date, and a cross-midnight event is shown once with its full UTC start/end.
- [x] `end == now` is past; `start == now < end` is ongoing; all dates/statuses use the same clock instant.
- [x] Offset-bearing UTC serialization and role-specific output shapes are tested.
- [x] Task-scoped lint, format, and type checks pass; unrelated full-repository baseline failures are recorded below.

Verification: `uv run pytest -q tests/test_schedule_presentation.py tests/test_event_range_service.py tests/test_event_visibility.py` passed (18 tests); Ruff and mypy passed for `backend/schedule` and the new test.
Repository-wide Ruff lint passed. `uv run ruff format --check backend` still
reports pre-existing formatting in `backend/notifications/models.py`, and
`uv run mypy backend` still reports pre-existing errors in
`backend/auth/config.py`, `backend/database.py`, and
`backend/events/dependencies.py`; those unrelated files were not changed.

### Out of scope

DB queries, authentication, bot text, migrations.

### Suitable for

AI agent.

## Task 02: Compose authorized schedule reads and bulk subject labels

**Domain:** `backend/schedule/` (stub), `backend/events/` (reuse only)
**Type:** service/dependency
**Dependencies:** 01

### Context

`load_raw_occurrences` is teacher-scoped, not viewer-authorized. A student must not receive another student's calendar details.

### What to do

- Add a minimal schedule service accepting the authenticated viewer, requested period/date, and explicit clock instant.
- Resolve allowed scope by role, apply approved unsupported/linkage states, and read the viewer's saved timezone.
- Call `load_raw_occurrences` once for the whole day/week, then `filter_vacation_visibility`, then enforce subject filtering for students.
- Bulk-select minimal subject labels for authorized teacher-facing lesson rows; no per-event relationship loads or full profile hydration.
- Feed approved visible items to Task 01 grouping. Preserve foundation errors/budgets and stable ordering.
- Keep hidden events/reasons and other subjects' metadata internal; no result cache or writes.

### Reuse

`CurrentUser` semantics, `resolve_teacher_id` where compatible with explicit role guards, `User`, shared range/visibility/time helpers.

### Acceptance Criteria

- [x] Teacher sees their visible lessons/breaks and lesson labels, including personal lessons; another teacher's data never appears.
- [x] Student sees only their own visible subjects; teacher-wide vacation still suppresses appropriately.
- [x] Student response contains no other subjects, hidden counts/reasons, emails, Telegram IDs, or full profiles.
- [x] Missing names and approved invalid-linkage states are tested.
- [x] Query count is bounded independently of displayed days/students/events; read does not dirty/commit rows.
- [x] Task-scoped lint, format, and type checks pass; full-repository baseline failures are recorded under Task 01.

### Out of scope

Rewriting event range/visibility logic, enforcing working weekdays, admin schedule browser.

### Suitable for

AI agent.

## Task 03: Expose authenticated day/week schedule routes

**Domain:** `backend/schedule/` (stub), app wiring
**Type:** router
**Dependencies:** 02

### Context

[Schedule router](./backend/schedule/router.py) is empty and unmounted.

### What to do

- Add the approved `/schedule/day` and `/schedule/week` GET routes using `CurrentUser`, `SessionDep`, response models, and validated optional local `date`.
- Obtain one clock instant per request through a small overridable dependency; pass it to the service.
- Mount the router under the existing API prefix in [backend app](./backend/main.py).
- Fully document query semantics, timezone, role scope, snapshot fields, and validation/auth errors in OpenAPI.
- Return existing validation errors for range/data/budget issues, never a partial or empty schedule fallback.

### Reuse

Task 02 service, existing auth/exception patterns, FastAPI `Annotated` dependencies.

### Acceptance Criteria

- [x] Both routes work with existing user JWTs; missing/invalid JWT and inactive user requests fail before calendar disclosure.
- [x] Omitted date selects the correct local day/week near UTC midnight; explicit date is stable under an overridden clock.
- [x] Invalid dates fail validation; week anchor on Sunday still returns the preceding Monday through Sunday.
- [x] DST-transition day/week and engine-error paths have real-DB HTTP coverage.
- [x] Foundation, auth, and existing bot behavior remain intact; task-scoped checks pass and full-repository baseline failures are recorded under Task 01.

Verification: the schedule, foundation, auth, smoke, and bot regression
selection passed (43 tests). Route OpenAPI, authentication, date boundaries,
DST day/week bounds, and invalid recurrence data were exercised through the
real ASGI app and isolated database.

### Out of scope

Arbitrary teacher/user query parameters, free-slot routes, schedule mutation, new migrations.

### Suitable for

AI agent.

## Task 04: Add typed Telegram schedule reads and specific failure messages

**Domain:** `telegram/` (existing)
**Type:** client
**Dependencies:** 03

### Context

[BackendClient](./telegram/api.py) already supports safe read/token renewal but lacks schedule types and structured query parameters.

### What to do

- Add frontend-owned typed schedule response models matching the API; do not import backend models/settings/helpers across the service boundary.
- Extend request/read helpers narrowly to pass typed httpx query parameters and preserve them on the single 401 retry.
- Add day/week read methods using user JWTs and existing token/circuit lifecycle. Bot default views use current-period reads; no new service-key schedule endpoint.
- Add safe schedule-specific error wording so 404/409/422 are not presented as “invitation not found,” “already registered,” or invalid registration settings.
- Preserve existing onboarding error behavior; use minimal call-site context or a bounded mapping rather than broad error-framework refactoring.
- Treat malformed schedule response/availability errors explicitly; never return an empty typed schedule on failure.

### Reuse

`BackendClient.request`, `read`, `BackendError`, `BackendUnavailable`, `CircuitBreaker`, existing httpx lifecycle.

### Acceptance Criteria

- [x] Day/week payloads validate, explicit query dates encode correctly, and query parameters survive JWT renewal.
- [x] Schedule calls use bearer auth, not `X-Bot-Service-Key`; tokens never enter callbacks or rendered text.
- [x] Transport/5xx/circuit failures remain availability errors; 400/403/422 use meaningful non-registration messages.
- [x] Existing auth/onboarding client tests still pass; common bot checks pass.

Verification: `uv run pytest -q tests/test_bot_client.py tests/test_bot_flows.py`
passed (15 tests). Ruff lint/format for `telegram` and the touched test, and
`uv run mypy telegram`, passed.

### Out of scope

Offline response cache, refresh tokens, backend imports in the bot.

### Suitable for

AI agent.

## Task 05: Format localized schedules safely within Telegram limits

**Domain:** `telegram/` (existing)
**Type:** bot/presentation
**Dependencies:** 00, 01

### Context

The README wants a weekly view in one message, but Telegram limits and long labels require explicit tested behavior.

### What to do

- Add a pure formatter over the agreed bot-side schedule shape, with local headings/times, event-type labels, teacher-only lesson labels, empty days, and timezone/update-time context.
- Derive viewer-local presentation exclusively from response timezone and UTC instants; no occurrence expansion or additional vacation filtering in Telegram.
- Place the approved now divider using `generated_at`; distinguish ongoing events and repeated-hour offsets.
- Render a cross-midnight event only on its local start date, showing its full interval and indicating that its end is on the following date.
- Implement approved same-message pagination, preserving order, complete event rows/day context, empty-day coverage, and visible page indicators.
- Bound/escape display labels safely; handle Telegram's parsed-text/UTF-16 length semantics, including emoji and HTML entities, using a conservative verified size budget. Never cut HTML entities/tags/surrogate pairs or drop events to fit.
- Keep callback/navigation data small and independent of full schedules.

### Reuse

Existing escaping conventions in [dialogs](./telegram/dialogs.py), Python `zoneinfo`, aiogram/Telegram formatting helpers where appropriate.

### Acceptance Criteria

- [x] Ordinary day/week fits one message; week includes Monday–Sunday even when some/all days are empty.
- [x] Oversized day/week pages remain within Telegram limits with valid formatting and complete authorized event coverage.
- [x] Long names, `<>&`, emoji, repeated-hour times, start-date-only cross-midnight rendering, and zero events have exact rendering tests.
- [x] Divider appears only on the response's current local date; start/end equality and ongoing events render correctly.
- [x] Student text never includes teacher-only subject labels; common checks pass.

### Out of scope

Telegram handlers, DB access, recurrence logic, automatic clock refresh.

### Suitable for

AI agent after Task 00 approval.

## Task 06: Wire Today/Week screens, refresh, and recovery

**Domain:** `telegram/` (existing)
**Type:** bot/dialog
**Dependencies:** 04, 05

### Context

[Registered home screen](./telegram/dialogs.py) has no schedule actions. Existing runtime already supplies private-chat and stale-state handling.

### What to do

- Add Today and This week entry buttons for registered TEACHER/STUDENT accounts without exposing unsupported admin actions.
- Add a focused schedule dialog/module using aiogram-dialog; register it through [runtime](./telegram/runtime.py), preserving home/onboarding stack behavior.
- Fetch on open, period switch, and Refresh; page navigation may use the currently fetched snapshot but must retain its update-time label.
- Store only necessary period/page/snapshot UI state. Do not store JWTs in dialog data or trust callback-supplied account IDs.
- Retry/back controls survive schedule-specific failures. On failed fresh read, replace calendar content with an unavailable/error screen, not silently stale rows.
- Reconcile page bounds after refresh shrinks the result, and re-render timezone/date from the new response.
- Use `ShowMode.EDIT`, existing callback acknowledgement/isolation, and specific framework missing-message recovery. No per-navigation message stream.
- Verify returning home does not falsely claim schedule viewing is unavailable; keep honest statements about still-unimplemented booking rules/notifications.

### Reuse

Task 04 reads, Task 05 formatter, `Screens.home`, existing `build_dialog`/dispatcher wiring, `SimpleEventIsolation`, stale-callback recovery.

### Acceptance Criteria

- [x] Teacher/student can open Today → This week → pages → Refresh → Back without losing the home/auth context.
- [x] Normal navigation edits the same message ID with zero extra sends; callbacks are acknowledged.
- [x] Refresh after cancellation/vacation/name/timezone changes displays new backend state; midnight rollover selects the new current period.
- [x] Backend outage, expired JWT, inactive account, invalid response, double-click, stale callbacks, and shrinking page counts have tested outcomes.
- [x] Onboarding/invitations and restart `/start` behavior remain valid; common bot checks pass.

### Out of scope

Previous/next date navigation, calendar picker, mutation buttons, durable UI state, offline cache.

### Suitable for

AI agent.

## Task 07: Verify backend → Telegram viewing and update feature status

**Domain:** `tests/`, project documentation
**Type:** integration/documentation
**Dependencies:** 03, 06

### Context

Completion requires the real authorized backend result to reach the edit-in-place UI with correct dates, names, visibility, and limits.

### What to do

- Add end-to-end tests with actual ASGI backend/isolated DB, typed bot client, real dispatcher, and RecordingSession; do not mock schedule services.
- Cover teacher + two students + unrelated teacher, one-offs, later recurring occurrences, exact cancellation, teacher/student vacation visibility, breaks, and personal lessons.
- Test viewer zones on opposite sides of UTC midnight, DST weeks, cross-midnight events, empty days, now divider, long-name overflow, and current-period refresh.
- Exercise API authorization and inspect both serialized responses and bot text for leakage, not only visible button labels.
- Measure request/query counts and actual Bot API send/edit/message-ID behavior. Assert all authorized event rows survive pagination.
- Update [README](./README.md) with endpoint schemas/query semantics, bot navigation, snapshot/refresh behavior, and approved overflow policy without deleting the Russian spec.
- Update [AGENTS.md](./AGENTS.md) feature status to match shipped views, preserving pending booking limits, automatic breaks, concurrency protection, cache, notifications, and HTMX status.
- Provide a concise handoff for future notification/cache consumers: authorization remains required; do not cache teacher-wide raw data as a student's result.

### Reuse

[Existing DB fixtures](./tests/conftest.py), [BotDriver](./tests/bot_support.py), [combined-flow patterns](./tests/test_bot_flows.py), foundation range/visibility tests.

### Acceptance Criteria

- [ ] Exact backend results and rendered teacher/student schedules match approved examples and foundation visibility.
- [ ] No cross-student/teacher data leaks through API, pages, errors, or callbacks.
- [ ] DST/day/week bounds, one clock snapshot, refresh recovery, and complete length-safe pagination are verified.
- [ ] Auth/onboarding and relevant foundation regressions pass; completion report lists commands/results and unrelated baseline failures.
- [ ] Documentation distinguishes completed live reads from unimplemented offline caching and booking.

### Out of scope

Adding downstream features to inflate milestone completeness.

### Suitable for

AI agent for automation/docs; human for final real-Telegram readability smoke test with credentials kept outside committed files.

## Out of scope for this milestone

- Offline schedule cache, stale schedule serving, Redis, durable snapshots.
- Booking/free slots, create/move/cancel Telegram workflows, displacement confirmation UI.
- Teacher work hours/day limits, duration enforcement, automatic breaks, concurrency-safe booking.
- Previous/next arbitrary-date Telegram navigation and flexible date parsing.
- Settings editing, homework, notification workers/digests/reminders, broadcasts.
- HTMX/frontend pages, exports/calendar integrations, admin schedule browser.
- Changing the approved foundation's recurrence, cancellation, vacation, or break-displacement policies.

## Open questions / execution gates

1. Approve same-message pagination for oversized weeks/days and the bounded display-label policy.
2. Approve cross-midnight duplication by overlapping day, continuation labels, and exact now-divider placement.
3. Approve UI language and missing-name/personal-lesson labels.
4. Approve unsupported admin/unlinked-student/invalid or inactive-linked-teacher behavior.
5. Approve endpoint names, safe subject metadata, and exact response shape in Task 00.

After this milestone, the bot has live schedule viewing, not offline viewing. Read-only cache and booking-policy/concurrency work remain separate next milestones.
