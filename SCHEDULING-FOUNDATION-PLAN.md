# Scheduling foundation — Implementation plan

## Goal

Establish one backend-owned interpretation of event times, recurring occurrences, cancellations, vacation visibility, and break displacement, so schedule viewing and booking can build on the same tested foundation.

This is an implementation backlog, not shipped behavior. Give an agent one numbered task with its completed prerequisites. Do not assign the whole milestone as one task.

## Scope (README sections covered)

- [Start/settings](./README.md#старт): consistent storage timezone and individual display timezone. Working-hour/day/limit enforcement is a follow-on milestone.
- [Schedule viewing](./README.md#просмотр-расписания): the underlying occurrence calculation and vacation visibility, **not** day/week HTTP endpoints or Telegram screens.
- [Schedule changes](./README.md#изменение-расписания): shared time/recurrence semantics, cancellation integrity, ownership, and correct temporal conflict checks for existing CRUD.
- [Architecture](./README.md#архитектура): backend remains the only scheduling source of truth; bot and later clients do not implement a second recurrence engine.

Deliverable: a tested internal range-query/occurrence service, wired into temporal validation on existing event routes. No new public schedule-viewing API is required here.

## Current repository evidence

Read against the repository on 2026-10-15:

- [Event models](./backend/events/models.py) contain `Event`, `RecurrentEvent`, and `RecurrentCancels`. All scheduling timestamps are naive `DateTime`. Recurrence stores a first start/end, integer interval in days, and `interval_end`; it stores no recurrence timezone.
- [Shared schemas](./backend/events/schemas.py) accept arbitrary `datetime` values without a stated naive/aware contract. `interval` has no positive bound. Cancellation input is a start/end pair.
- [Shared service](./backend/events/service.py) already owns CRUD and commits. Reuse it. `update_recurrent_event` updates start/end and cancellation rows, but does **not** persist `interval` or `interval_end`; both one-off and recurring updates accept a `user_id` but do not assign it.
- [Conflict validators](./backend/events/validators.py) compare recurring **anchor** timestamps and retrieve a single candidate, rather than correctly expanding all potentially overlapping occurrences. Cancellation rows are not considered. Recurring validation does not fully check one-off events. These are foundation blockers, not features to preserve.
- [Lesson routes](./backend/lessons/router.py), [break routes](./backend/breaks/router.py), and [vacation routes](./backend/vacations/router.py) now use domain dependencies. Vacation calls now pass `teacher_id`. Do not re-plan the obsolete missing-argument/dead-import fixes described in [AGENTS.md](./AGENTS.md).
- [Current validator definitions](./backend/events/validators.py) have `EventSubValidator` plus the composite `EventValidator`, not two shadowing `EventValidator` definitions. That known-issue note is also stale.
- Domain validators presently compose the same shared `EventValidator`; event-type display names do not enforce that a loaded row is actually a lesson/break/vacation.
- [Ownership helpers](./backend/events/utils.py) resolve teacher scope and assignment access. `assert_owns_event` only checks teacher scope; by itself it lets a linked student access another student's event under the same teacher. The README requires students to manage only their own events.
- `teacher_id` identifies schedule ownership; `user_id` identifies the event subject. Neither records who created an event. Approved conflict behavior is event-type-based and does not infer priority from creator role.
- [Schedule package](./backend/schedule/) exists but its router is empty. There is no reusable occurrence-expansion service today.
- [Existing integration fixtures](./tests/conftest.py) use real isolated SQLite, `AsyncClient`, `ASGITransport`, and dependency overrides. [Smoke tests](./tests/test_smoke.py) use naive UTC-like timestamps and cover the lesson lifecycle; preserve their intended behavior under an explicit compatibility contract.
- Auth/onboarding is implemented. User IANA timezones and teacher preferences already exist; do not recreate settings fields, Telegram identity, login, deployment, or circuit breakers.
- The approved read-only local `dev.db` inventory found 14 one-off events, 3 recurring series with a longest horizon of 91 days, no cancellations, no invalid interval ordering, no duplicate cancellation identities, and no overlapping expanded occurrences. Production data was not inspected.

## Approved contract (Task 00)

Approved by the product owner on 2026-10-15. Update downstream tasks if these decisions change; do not introduce different semantics during implementation.

### 1. Instants and timezone boundaries

- Canonical internal occurrence timestamps: **aware UTC**.
- Persistence: retain naive database columns, but store **UTC only**; normalize before storage and reattach UTC on loading. This supports both SQLite and PostgreSQL without pretending SQLite preserves timezone metadata.
- Existing naive event and cancellation rows represent **UTC**. API input accepts offset-bearing timestamps and converts them to UTC; naive API timestamps mean UTC, never server-local or caller-local time. Responses return offset-bearing UTC timestamps (`Z` or `+00:00`) consistently.
- User-entered local times must be converted with an explicit IANA timezone by the eventual client/input boundary. Never infer timezone from server settings or Telegram language.
- Construct a local day's next midnight independently, then convert both boundaries to UTC; do not assume every local day is 24 elapsed hours. Week boundaries are local Monday midnight.
- User timezone changes alter presentation only; they do not move stored event instants or change the timezone snapshot of an existing recurrence.

### 2. Recurrence

- New series repeat at the same **teacher-local wall-clock start time**, using an immutable snapshot of the teacher's IANA timezone taken when the series is created. Do not recalculate that series timezone when the teacher later changes their display timezone.
- Existing series retain their current UTC-stepping behavior as an explicit legacy mode. Do not convert them using the teacher's current timezone.
- Keep the existing positive interval-in-days representation. README weekly UI can later submit 7; do not silently force all existing intervals to 7.
- UTC anchor start + immutable recurrence timezone reconstructs the local start anchor; no need for an unrelated general RRULE framework.
- Preserve elapsed duration from the original start/end for every recurrence.
- `interval_end` is an **exclusive occurrence-start cutoff**. An occurrence starting before it is eligible even if its end extends beyond it. An occurrence starting exactly at the cutoff is excluded.
- For future teacher-local generated starts, shift nonexistent local times forward by the DST gap (02:30 becomes 03:30); choose the earlier instant when a local time occurs twice. An offset-bearing input anchor identifies its instant and fold; do not re-resolve that anchor.
- Explicitly entered offset-bearing anchor timestamps identify an instant, including a chosen fold. Future occurrences follow the approved policy.
### 3. Intervals, occurrences, and cancellations

- Use half-open intervals `[start, end)` throughout: overlap means `a.start < b.end and a.end > b.start`; adjacent events do not overlap.
- A query also uses `[range_start, range_end)` and includes events crossing either boundary. Return full occurrence start/end; presentation clipping is a later concern.
- Stable identity: `(source kind, source UUID, occurrence start in UTC)`. A one-off occurrence uses its stored start; recurring identity is never just the series UUID or its position in a result list.
- Retain `RecurrentCancels` rather than mutating/deleting a recurring series to skip a date. Each row identifies **one full generated occurrence** using matching UTC start/end, not an arbitrary partial blackout interval.
- Reject invalid, duplicate, off-grid, or out-of-series cancellation rows. Preserve stored cancellation identities through viewing.
- For series edits, omitted cancellation lists preserve cancellations for non-recurrence edits. Changing a recurrence-defining field (start, end, interval, cutoff, or recurrence timezone) clears existing cancellations unless a valid replacement list is supplied. An explicit empty list clears; a supplied list replaces after validation. Invalid, duplicate, partial, off-grid, or out-of-series entries reject the whole update.
- On series deletion, remove its cancellation rows within the same transaction; do not rely on database-specific cascade behavior.

### 4. Vacation visibility and break displacement

Vacations are presentation/notification filters, not mutations to the event set or conflict calendar.

| Input | Schedule/notification effect | Admission/conflict effect |
|---|---|---|
| Teacher vacation | Hide every event in the teacher's displayed schedule, including teacher-owned breaks; hide all student events for that teacher | Does not suppress candidates or alter recurring conflict checks |
| Student vacation | Hide all events for that student only | Does not suppress candidates or alter recurring conflict checks |
| One-off event create or time-changing update | Existing events remain unchanged | Reject any half-open overlap with an applicable teacher or student vacation, for every one-off event type |
| Recurring event create/update | Vacation does not hide candidates from validation | Run normal complete overlap checks as if vacations did not exist |
| Teacher break overlapping a lesson | Break remains teacher-wide | Require explicit confirmation; after confirmation, permanently delete the one-off lesson or record a cancellation for the exact recurring occurrence. Removing the break does not restore it. |
| Cancellation | Removes the matching series occurrence | Cancelled occurrence is not a busy interval |

- Any vacation overlap hides the **whole event occurrence** from schedule display and notifications. Preserve the underlying row/series and calculate visibility without mutating it.
- Teacher vacations hide all event types in the teacher's displayed schedule. Student vacations hide only that student's events; other students' schedules remain visible.
- Vacation changes do not reactivate events into the conflict calendar because vacations never remove events from that calendar.
- Break create/update first returns a conflict response with affected lesson occurrences and makes no changes. A client must explicitly confirm and retry; the backend rechecks and applies the break plus cancellations atomically. A lesson hidden by the affected student's vacation still follows the same cancellation rule, but need not produce a warning.
- Only lesson events are displaced by teacher breaks. Do not infer creator priority or add creator/provenance fields.

### 5. Service shape and bounds

- Place shared temporal/occurrence primitives in `backend/events/`; schedule read orchestration can later compose them from `backend/schedule/`.
- Separate: normalized event rows → raw occurrence expansion → cancellation filtering → approved effective/availability policy.
- Typed occurrences carry source kind/ID, teacher ID, subject ID, event type, UTC start/end. Visibility results retain hidden occurrence identity and reason, not a success-shaped empty list.
- Bounded range loading must query only candidates in teacher scope and load cancellations in bulk. No per-occurrence DB queries or persistent occurrence materialization.
- Expansion seeks to the first potentially overlapping recurrence rather than walking from the original anchor, and yields deterministically sorted results.
- Limits must fail explicitly, never truncate a list and imply completeness. Maximum range query: 31 days. Maximum expansion: 10,000 occurrences per query.
- New or updated recurring series may span at most 10 years and must be validated over the complete accepted horizon. Longer existing series remain stored and are never silently truncated; reject any operation that cannot be validated completely.
- Existing create/update/delete routes remain the public entrypoints. Foundations do not promise concurrency-safe booking: transaction-level race protection is a named follow-on requirement before real multi-user booking launch.

### Approved reference examples

- A naive API value `2026-10-15T09:00:00` means `2026-10-15T09:00:00Z`; `2026-10-15T09:00:00+07:00` normalizes to `2026-10-15T02:00:00Z`. Responses include an explicit UTC offset.
- A weekly `02:30` Europe/Berlin series anchored on 2026-03-22 generates 2026-03-29 at `03:30+02:00` (`01:30Z`): the nonexistent `02:30` is shifted forward by the one-hour gap. A 60-minute elapsed duration ends at `02:30Z`.
- A weekly `02:30` Europe/Berlin series anchored on 2026-10-18 generates 2026-10-25 at the earlier `02:30+02:00` (`00:30Z`) during the repeated hour.
- If an occurrence starts exactly at `interval_end`, it is excluded. An occurrence starting before the cutoff is included even when its end is later than the cutoff.
- Adjacent events `09:00–10:00` and `10:00–11:00` do not overlap. A lesson `09:00–10:00` partially overlapped by a vacation `09:30–10:30` is hidden in full from display/notifications, remains stored, and remains part of normal conflict checking.
- A teacher vacation hides all displayed event types for that teacher; a student vacation hides only that student's events. Neither vacation changes recurring conflict checks.

## Tasks and dependencies

| ID | Task | Prerequisites |
|---|---|---|
| 00 | Approve time, recurrence, and event-policy semantics | None |
| 01 | Define shared UTC and local-boundary helpers | 00 |
| 02 | Add approved recurrence metadata and integrity migrations | 00, 01 |
| 03 | Normalize timestamps across existing event APIs and persistence | 01, 02 |
| 04 | Implement bounded pure recurrence expansion | 01, 02 |
| 05 | Implement scoped candidate loading and raw range service | 03, 04 |
| 06 | Validate/persist recurring edits and cancellation lifecycle | 03, 04 |
| 07 | Implement non-destructive vacation visibility policy | 00, 05, 06 |
| 08 | Enforce actor ownership and event-kind integrity on live routes | 00 |
| 09 | Replace anchor-only conflict checks with shared occurrences | 05, 06, 07, 08 |
| 10 | Verify complete foundation and document handoff contracts | 03, 06, 07, 08, 09 |

After Task 00, Task 08 can run independently of the temporal chain. Tasks 03 and 04 can run in parallel after agreeing the Task 02 schema and Task 01 helper interfaces. Coordinate agents editing shared schemas/services and serialize migrations; do not create competing Alembic heads.

### Common acceptance criteria for implementation tasks 01–10

- Use existing domain composition and explicit module imports; do not build a parallel scheduler in the bot.
- Test the actual touched behavior in each task; Task 10 adds cross-module checks rather than postponing all testing until the end.
- Reuse [integration fixtures](./tests/conftest.py) with real isolated DBs; pure temporal helpers can have direct unit tests. No database mocks in backend integration tests.
- Run targeted tests and the existing lesson/auth smoke flows whenever shared schemas/routes/services change. Keep all existing auth/onboarding behavior intact.
- Run `uv run ruff check --fix backend`, `uv run ruff format backend`, and `uv run mypy backend`; include touched tests in Ruff checks. Record unrelated baseline failures honestly rather than repairing unrelated modules.
- Schema changes need static, reviewed, reversible migrations named `YYYY-MM-DD_slug.py`. Exercise upgrade/downgrade on disposable SQLite and PostgreSQL DBs; never use `dev.db` for test migration experiments.
- Never silently truncate recurrence results, guess a timezone, discard cancellations outside the approved series-edit rule, swallow DB errors, or resolve conflicts by arbitrary ordering.
- Keep exception/status behavior consistent with repository patterns and document intentional API changes.
- Tests use fixed dates/clocks, explicit IANA zones, and asserted expected UTC instants; do not merely assert “returned something.”

## Task 00: Approve the scheduling semantics and legacy-data policy

**Domain:** product/backend contract
**Type:** planning
**Dependencies:** none

### Context

[README scheduling rules](./README.md#изменение-расписания) do not fully specify DST, recurrence cutoffs, cancellation matching, or overlap priority. [Current models](./backend/events/models.py) cannot establish legacy timezone or creator provenance.

### What to do

- Record the approved UTC/API, recurrence, cancellation, vacation visibility, one-off admission, and teacher-break displacement contracts above; revise dependent tasks to match.
- Apply the 31-day range, 10,000-occurrence budget, and 10-year maximum new/updated-series horizon without silently truncating longer existing series.
- Inventory legacy invalid intervals/cancellations/conflicting occurrences only on an explicitly approved data source. Do not inspect production data or mutate any data as part of planning; report categories/counts without exporting personal data.

### Reuse

README defaults/roles, existing event/cancellation model structure, completed auth settings.

### Acceptance Criteria

- [x] UTC/API semantics, legacy-series mode, local recurrence, duration, DST, cutoff, cancellation, vacation, break, and resource-budget decisions are approved above.
- [x] Legacy-data treatment and intentional API compatibility changes are documented.
- [x] At least one spring-forward and fall-back example, cutoff equality, and student/teacher vacation example are recorded above.
- [x] Scope remains foundation only; no creator field or unapproved override feature is added.
- [x] Read-only local `dev.db` inventory: 14 one-off events, 3 recurring series (longest horizon 91 days), 0 cancellations, 0 invalid one-off/series interval ordering, 0 duplicate cancellation identities, and 0 overlaps among expanded occurrences. No production data was inspected or changed.

### Out of scope

Implementation, live data conversion, infrastructure rollout.

### Status

Complete. All dependent implementation tasks use the approved contract above; changes to it require renewed owner approval.

## Task 01: Define shared UTC normalization and local-range boundaries

**Domain:** `backend/events/` (existing)
**Type:** utility/schemas
**Dependencies:** 00

### Context

The README requires user-local display with a consistent DB timezone. [Shared event schemas](./backend/events/schemas.py) have no such contract today.

### What to do

- Add the smallest shared temporal helper module with typed functions for approved input normalization, UTC storage conversion/loading, local wall-clock resolution, and half-open overlap.
- Add local date/day and Monday-based week boundary helpers for future consumers, without creating HTTP endpoints.
- Resolve gaps/folds per Task 00 using explicit round-trip validation; attaching `ZoneInfo` alone must not be mistaken for validating a local time.
- Distinguish generated recurrence-local times from offset-bearing input instants; do not re-resolve an already explicit offset/fold.
- Define precise errors for invalid zones, malformed ranges, and unsupported local times.

### Reuse

Python `datetime`/`zoneinfo`, shared event schemas, repository validation exceptions; reuse approved auth timezone validation where practical without coupling to bot parsers.

### Acceptance Criteria

- [x] Two offset-bearing representations of the same instant normalize identically; approved naive compatibility is explicit.
- [x] Adjacent intervals do not overlap; invalid/reversed ranges fail.
- [x] Local days around both DST transitions map to correct 23/25-hour UTC ranges; week starts Monday in the viewer zone.
- [x] Gap/fold inputs match approved UTC examples, including an explicitly selected fold.
- [x] Focused tests, Ruff, and touched-file mypy pass. Full `mypy backend` was run and reports nine unrelated existing errors in `backend/auth/config.py`, `backend/database.py`, `backend/events/validators.py`, and `backend/events/dependencies.py`.

### Out of scope

Recurrence enumeration, DB migration, flexible natural-language date parsing.

### Suitable for

AI agent after Task 00.

## Task 02: Add recurrence metadata and database integrity

**Domain:** `backend/events/` (existing), Alembic
**Type:** schema/migration
**Dependencies:** 00, 01

### Context

[RecurrentEvent](./backend/events/models.py) lacks the metadata needed for approved local-clock recurrence. [RecurrentCancels](./backend/events/models.py) lacks explicit occurrence uniqueness/deletion policy.

### What to do

- Add the approved immutable recurrence timezone and explicit legacy UTC-stepping mode for existing series; preserve the original UTC anchor and interval representation.
- Preserve existing UTC instants and existing UTC-stepping series; add metadata defaults that classify existing rows as legacy without reinterpreting their timestamps. Preflight invalid data before modifying schema.
- Add positive-interval integrity and cancellation uniqueness under the approved identity; define portable FK/deletion behavior.
- Add measured candidate-query/cancellation indexes justified by Task 05's query shapes; avoid broad speculative index creation.
- Preserve existing event/series IDs and cancellation identities. Downgrade must not silently reinterpret local-clock series as old UTC-step series; fail safely on incompatible data if lossless reversal is impossible.

### Reuse

`Event`, `RecurrentEvent`, `RecurrentCancels`, existing Alembic naming/batch patterns and model discovery.

### Acceptance Criteria

- [x] Upgrade preserves existing UTC instants and UTC-stepping behavior without relying on mutable teacher timezones.
- [x] Invalid intervals/duplicate cancellations have explicit preflight/remediation behavior.
- [x] ORM annotations and schema defaults match actual DB nullability/constraints; Alembic reports no schema drift on SQLite or PostgreSQL.
- [x] Disposable SQLite/PostgreSQL upgrade/downgrade round trips pass, including safe refusal of lossy downgrade.
- [x] Full tests (51 passed) and Ruff pass; touched-file mypy passes. Full backend mypy retains the nine unrelated baseline errors recorded under Task 01.

### Verification

- Legacy UTC series and a cancellation were seeded at the previous revision on SQLite and PostgreSQL. Upgrade preserved their timestamps and cancellation identity and assigned `UTC_STEP` with no timezone.
- Migration preflight rejected a non-positive interval and duplicate cancellation start before changing the schema; database constraints reject those writes after upgrade.
- Downgrade refused teacher-local recurrence metadata and succeeded after restoring `UTC_STEP`, preserving legacy series/cancellation data.
- `uv run alembic check` reported no new operations on both disposable databases.

### Out of scope

Materialized occurrences, general RRULE support, creator tracking unless separately approved in Task 00.

### Suitable for

AI agent.

## Task 03: Normalize timestamps across every existing event API

**Domain:** `backend/events/`, `backend/lessons/`, `backend/breaks/`, `backend/vacations/`
**Type:** schemas/service
**Dependencies:** 01, 02

### Context

All event-like domains inherit [shared schemas](./backend/events/schemas.py) and call [shared CRUD](./backend/events/service.py). One shared fix must cover them consistently.

### What to do

- Apply approved input normalization to one-off/recurring create/update, cutoff, and cancellation timestamps through shared schemas/helpers.
- Apply UTC-only persistence before commits and aware-UTC public serialization when reading naive ORM values.
- Initialize/preserve recurrence timezone according to Task 00; derive a new series default from its teacher, not the student actor or server timezone.
- Validate positive intervals, finite cutoff, and start/end consistency using approved cutoff rules. Preserve the existing >1-minute minimum unless Task 00 explicitly changes it.
- Update OpenAPI field descriptions and directly related timestamp examples; preserve route shapes.

### Reuse

`EventCreate/Public/Update`, `RecurrentEventCreate/Public/Update`, `RecurrentCancel`, shared create/update services and domain schema inheritance.

### Acceptance Criteria

- [x] All existing event domains normalize offset input, persist UTC, and return explicit UTC offsets consistently.
- [x] Legacy naive input behaves exactly as approved; never depends on process timezone.
- [x] Offset-equivalent requests produce the same stored instant and conflicts.
- [x] Teacher display-zone changes do not move one-off instants or silently change existing series recurrence zones.
- [x] Existing auth/onboarding and lesson lifecycle remain valid; common checks pass.

### Verification

- Three API/schema tests cover UTC input documentation, offset and naive input normalization across lessons, breaks, and vacations, UTC-naive DB persistence, offset-equivalent conflict detection, recurrence timezone snapshots, profile timezone changes, and cancellation normalization.
- `uv run pytest -q`: 54 passed. Ruff check and formatting passed for all changed Python files. Targeted mypy for the changed schemas/service passed.
- Full `uv run mypy backend` retains the same nine baseline errors recorded under Task 01; no new type errors were reported.
- The recurring update schema normalizes submitted interval/cutoff fields, but persistence of recurrence interval/cutoff edits remains assigned to Task 06.

### Out of scope

New schedule endpoints, client-local formatting, event duration/default enforcement by role.

### Suitable for

AI agent.

## Task 04: Implement pure, bounded recurrence expansion

**Domain:** `backend/events/` (existing)
**Type:** service/utility
**Dependencies:** 01, 02

### Context

Current [conflict checks](./backend/events/validators.py) use series anchors, which cannot represent later instances reliably.

### What to do

- Add typed occurrence/source structures and a pure expansion function taking normalized series data and a finite UTC query range.
- Generate local-calendar recurrences or approved legacy mode, correct duration, and cutoff; use Task 01 helpers.
- Seek near the requested range, including starts before the lower bound whose ends overlap it. Account for approved gap shifting without missing a boundary occurrence.
- Produce stable source/start identities and deterministic ordering; fail explicitly on budget exhaustion or invalid recurrence.
- Keep raw expansion independent of DB access, cancellations, display visibility, and conflict admission.

### Reuse

Task 01 time helpers, `EventType`, existing interval-in-days representation.

### Acceptance Criteria

- [ ] Weekly/non-weekly intervals, later query windows, overnight/multi-day occurrences, and cutoff equality have exact expected outputs.
- [ ] Spring/fall transitions preserve approved clock and duration semantics.
- [ ] A series anchored years earlier does not require iterating every prior occurrence; verify bounded generation work, not only runtime timing.
- [ ] Overlapping adjacent query windows produce identical identity/start/end for the same occurrence.
- [ ] Budget failure is explicit and returns no falsely complete result; common checks pass.

### Out of scope

DB queries, vacation visibility, cancellations, booking limits.

### Suitable for

AI agent.

## Task 05: Load scoped candidates and expose a raw range service

**Domain:** `backend/events/` (existing)
**Type:** service/query
**Dependencies:** 03, 04

### Context

Schedule views and conflicts need all relevant one-off events, series, and cancellations, not a single arbitrary DB match.

### What to do

- Add a backend range service using explicit teacher scope and validated finite UTC bounds.
- Query overlapping one-offs with half-open SQL predicates; query series conservatively enough to include later/overnight/shifted occurrences, then filter through Task 04.
- Bulk-load cancellations for candidate series and remove only matching full occurrences.
- Keep teacher-wide vacation/break inputs available when selecting a student's visible output; other students' busy events remain available to the separate availability policy.
- Return typed raw active occurrences, stable sorting, and explicit invalid-data errors. No N+1 loading or access to another teacher's schedule.
- Keep querying and transaction ownership compatible with callers; do not commit from a read helper.

### Reuse

`AsyncSession`, event models, Task 04 expansion, `resolve_teacher_id` for caller-derived scope at entrypoints.

### Acceptance Criteria

- [ ] Real-DB tests cover one-off + recurring + cancelled occurrences, including intervals crossing both query edges.
- [ ] A second matching series is never missed because a first candidate was selected.
- [ ] Teacher scopes remain isolated; student policy retains necessary teacher blockers without exposing other students' personal data.
- [ ] Fixed query-count tests demonstrate bulk loading, not per-occurrence/per-student queries.
- [ ] Read results do not mutate stored rows; common checks pass.

### Out of scope

Public schedule endpoints, names/HTML formatting, caching.

### Suitable for

AI agent.

## Task 06: Correct recurring edits and cancellation lifecycle

**Domain:** `backend/events/` (existing)
**Type:** service/validator
**Dependencies:** 03, 04

### Context

[Recurring update service](./backend/events/service.py) currently ignores interval/cutoff changes and accepts cancellation lists without checking generated occurrence membership.

### What to do

- Persist all supported submitted update fields, including interval/cutoff and approved subject reassignment; make validator input match the subject that will actually be stored.
- Validate each cancellation against Task 04 under the final proposed series state; reject duplicates, partial intervals, off-grid starts, and out-of-series entries.
- Implement omitted/preserve, empty/clear, and supplied/replace semantics atomically. Apply Task 00's series-edit incompatibility rule.
- Keep recurrence timezone stable except for an approved explicit edit mechanism; do not implicitly replace it on a display timezone change.
- Delete series cancellations with series deletion transactionally; no orphan rows. Preserve existing one-off/recurring CRUD transaction ownership.

### Reuse

`update_event`, `update_recurrent_event`, `delete_event`, `RecurrentCancels`, shared write schemas and Task 04.

### Acceptance Criteria

- [ ] Interval/cutoff and permitted subject edits are reflected in persisted rows and subsequent expansion.
- [ ] Invalid cancellation replacement leaves original series and cancellation rows unchanged.
- [ ] Omitted, empty, and replacement lists have distinct tested behavior.
- [ ] Recurrence-defining edits clear old cancellations unless a valid replacement list is supplied; non-recurrence edits preserve them.
- [ ] Deletion leaves no cancellation orphans on both supported DBs; common checks pass.

### Out of scope

New cancel-one-occurrence HTTP route or Telegram UX; use existing cancellation-list update surface.

### Suitable for

AI agent.

## Task 07: Calculate vacation visibility without destroying events

**Domain:** `backend/events/` (existing)
**Type:** policy/service
**Dependencies:** 00, 05, 06

### Context

[README schedule viewing](./README.md#просмотр-расписания) hides events during applicable vacations. Vacations affect display and notifications only; all underlying events remain candidates for temporal conflicts.

### What to do

- Add the smallest typed visibility filter over raw occurrences from Task 05.
- Hide the whole occurrence on any overlap with a teacher vacation (all displayed event types) or its subject student's vacation (that student's events only); preserve source identity and underlying rows.
- Keep vacation filtering separate from conflict admission and availability. The raw occurrence set, not the display-filtered set, is used for every conflict check.
- Apply cancellation rows independently from vacation visibility; do not mutate lessons or create cancellations because of a vacation.
- Preserve/report unresolved overlapping active lessons instead of arbitrarily choosing a winner. Apply only the approved teacher-break displacement policy.

### Reuse

Task 05 raw range service, Task 01 overlap, `EventType`, existing teacher/subject fields.

### Acceptance Criteria

- [ ] Teacher vacation hides all displayed event types for the teacher; student vacation hides only that student's events.
- [ ] Boundary adjacency and partial vacation overlap follow approved half-open/whole-occurrence rules.
- [ ] Removing a vacation makes the same source identity visible again without database reconstruction or conflict-calendar changes.
- [ ] Break displacement and recurring cancellations produce distinct, correct effects.
- [ ] Vacation visibility filtering never changes the conflict candidate set; common checks pass.

### Out of scope

Teacher override UX, deleting/replacing lower-priority series, day/week endpoints.

### Suitable for

AI agent after the approved semantics in Task 00.

## Task 08: Enforce actor ownership and domain event-kind integrity

**Domain:** `backend/events/` and live lesson/break/vacation routes
**Type:** dependency/validator/router
**Dependencies:** 00

### Context

[Ownership helper](./backend/events/utils.py) checks teacher scope only, while [README](./README.md#изменение-расписания) restricts students to their own events. Domain default event types are not immutable constraints.

### What to do

- Extend reusable authorization to consider the actor plus current event subject: teacher can manage own schedule subjects, student only their own events.
- Apply the rule to one-off and recurring update/delete, including the existing cancellation-list update route. Student ownership is checked before any reassignment.
- Keep breaks teacher-only as specified by the README; student vacations remain supported.
- Constrain domain create schemas to the correct event kind; domain existence validators reject cross-domain IDs, including recurring IDs.
- Use the currently wired domain dependencies; do not introduce a second unused validation path.

### Reuse

`resolve_teacher_id`, `assert_can_assign_user`, `assert_owns_event`, `EventExistsValidator`, domain dependencies and schemas, `CurrentUser`.

### Acceptance Criteria

- [ ] Same-teacher student A cannot update/delete/cancel/reassign student B's events.
- [ ] Teacher can manage own students; unrelated teacher cannot; student can manage own permitted event types.
- [ ] Lesson IDs cannot be edited/deleted through break/vacation routes, and payloads cannot spoof event type.
- [ ] One-off and recurring surfaces both have real API tests; common checks pass.

### Out of scope

Broad security audit, admin scheduling tools, registration/profile authorization already completed.

### Suitable for

AI agent.

## Task 09: Wire shared occurrences into temporal conflict validation

**Domain:** `backend/events/` and domain validator composition
**Type:** validator/service
**Dependencies:** 05, 06, 07, 08

### Context

The existing `NoConflictValidation` is anchor-only and cannot supply correct temporal admission. It is the live integration point for this foundation.

### What to do

- Replace anchor comparisons with complete one-off-versus-occurrence and recurring-versus-one-off/recurring overlap checks using the shared engine.
- Exclude the current source during edits, but still check proposed series self-overlap and all other relevant active occurrences.
- Apply the one-off vacation-overlap restriction to create and time-changing update for all event types; recurring validation ignores vacation visibility.
- For teacher break create/update, return affected lesson occurrences without mutation unless explicit confirmation is supplied. On confirmation, recheck and atomically delete one-off lessons / record recurring-occurrence cancellations.
- Validate cancellation/series changes using the approved recurrence and cancellation rules. Vacation edits do not require conflict reactivation checks.
- Bound recurring validation explicitly across the full accepted cutoff horizon. Never validate only a display week then claim the entire series is conflict-free.
- Keep domain validator wiring type-safe; replace `hasattr`/ignored-call dispatch if tightly coupled changes require a typed interface.
- Return specific conflicts/unsupported-horizon errors. Never displace a lesson without explicit break confirmation or silently move events.

### Reuse

`NoConflictValidation`, `TimeValidation`, `UserExistsValidation`, `EventValidator`, Tasks 04–07, existing CRUD services.

### Acceptance Criteria

- [ ] Later recurring occurrence conflicts are detected; adjacent events and cancelled occurrences do not falsely conflict.
- [ ] Recurring writes detect both one-off and other recurring conflicts over the full accepted horizon.
- [ ] Multiple candidates, different anchors/intervals, DST changes, overnight intervals, and self-excluded edits are tested.
- [ ] Vacation create/update changes visibility only; one-off admission rejects any applicable vacation overlap, while recurring checks remain vacation-blind.
- [ ] Break conflict preview is non-mutating; confirmed retry rechecks and atomically cancels only lesson events.
- [ ] Removing a break does not restore permanently canceled lessons.
- [ ] No booking race-protection claim is made; teacher limits/automatic breaks remain explicitly unimplemented.
- [ ] Existing API/auth smoke behavior and common checks pass.

### Out of scope

Work hours/days, daily/consecutive limits, role-specific duration, automatic breaks, transaction locks/concurrency-safe booking, free-slot API.

### Suitable for

AI agent.

## Task 10: Verify the foundation and document the next-module handoff

**Domain:** `tests/`, project documentation
**Type:** integration/documentation
**Dependencies:** 03, 06, 07, 08, 09

### Context

Completion means one coherent scheduling interpretation, not just helper functions or green isolated tests.

### What to do

- Add cross-module real-DB tests: create normalized one-offs/series → skip an occurrence → query a later range → hide/unhide with vacations → preview/confirm break displacement → edit interval/cutoff → validate conflicts → delete series.
- Test teacher/student scope and all live event-kind routes, preserving auth/onboarding flows.
- Add a table-driven edge suite with exact UTC outputs for DST, cutoff equality, midnight overlap, cancellation changes, and budget refusal.
- Verify candidate/expansion bounds with query/generation counts. Test invalid legacy fixtures and explicit errors, not silent omission.
- Exercise static migrations on disposable SQLite/PostgreSQL and run relevant existing tests.
- Update [README](./README.md) with approved storage/API/recurrence/cancellation semantics, preserving the Russian product spec.
- Update [AGENTS.md](./AGENTS.md) Feature Status and stale known-issue notes based on actual completed code. Schedule viewing stays “not started” until endpoints/UI ship.
- Document callable range-service shapes, policy inputs, viewer timezone boundaries, stable occurrence identity, and error cases for the next schedule-viewing agent.

### Reuse

[Existing fixtures](./tests/conftest.py), existing auth/lesson smoke tests, helpers/services delivered by Tasks 01–09.

### Acceptance Criteria

- [ ] Exact occurrence identities and UTC timestamps agree between range reads and temporal conflict validation.
- [ ] End-to-end cancellations/vacations do not lose or duplicate lessons.
- [ ] Tests prove role isolation, bounds, DST semantics, rollback behavior, and supported migration paths.
- [ ] Documentation distinguishes implemented foundation from pending screens, booking rules, concurrency protection, caching, and notifications.
- [ ] Relevant tests and common checks pass; completion report lists commands/results and unrelated baseline failures.

### Out of scope

Building downstream features just to make documentation appear complete.

### Suitable for

AI agent; approved semantics and local-data inventory are recorded above.

## Out of scope for this milestone

- Today/week schedule HTTP endpoints, timeline formatting, Telegram screens.
- Free-slot selection, scheduling wizard, flexible date input/year rollover.
- Enforcement of saved teacher work hours/days, default duration, daily/consecutive limits.
- Automatic between-lesson break creation/update/deletion.
- Concurrency-safe booking and real multi-user launch readiness.
- Read-only schedule cache, offline views, notification scheduler/workers.
- Homework, broadcasts, student history, calendar integrations.
- RRULE framework, materialized occurrence tables, new infrastructure.
- Creator-role priority and teacher override/replacement semantics.

## Remaining execution constraints

- Task 00's semantic gates are resolved and recorded above; no production data was approved for or subject to inspection.
- The local `dev.db` inventory is not a substitute for a production-data inventory before any production migration.
- Confirmed break-displacement API behavior, vacation visibility, and UTC/recurrence semantics must not be changed implicitly by implementation.

**Next milestone after this:** schedule day/week backend API and Telegram screens, composing this engine without reimplementing recurrence or priority. Then booking-policy enforcement and concurrency protection before real multi-user scheduling.
