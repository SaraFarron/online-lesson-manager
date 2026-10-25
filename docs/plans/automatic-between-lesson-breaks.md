# Automatic between-lesson breaks — Plan

## Goal

Every active lesson reserves the teacher's configured post-lesson break automatically, with consistent create/edit/cancel/delete behavior and clear Telegram presentation and settings.

**Status:** Product contract approved 2026-10-25; implementation pending.

## Scope (README.md sections covered)

- [Teacher break setting](../../README.md#старт): nonzero minutes add a break after each lesson; zero disables creation; deleting the lesson removes its break.
- [Schedule changes](../../README.md#изменение-расписания): conflict-safe lifecycle for one-off and recurring lessons.
- [Schedule views](../../README.md#просмотр-расписания) and Telegram: show automatic breaks and explain affected booking/settings actions.

## Baseline, prerequisites, and proposed design

- Complete [Telegram scheduling/settings UI](./telegram-scheduling-and-settings-ui.md) first for integrated user acceptance. Backend design work can start independently after Task 00; do not ship only backend behavior and mark Telegram complete.
- [TeacherProfile](../../backend/auth/models.py) already has `between_lessons_break`; zero is valid and defaults to five. Do not add another preference.
- [Booking policy](../../backend/events/booking_policy.py) uses that preference to define consecutive chains, but does not create/reserve breaks. Its count rule and the approved [booking contract](./scheduling-policy-and-plan-reconciliation.md) must remain coherent.
- [Shared event service](../../backend/events/service.py) serializes mutations per teacher using `TeacherScheduleGuard`; [occurrence expansion](../../backend/events/occurrences.py), [range service](../../backend/events/range_service.py), [conflicts](../../backend/events/conflicts.py), and [visibility](../../backend/events/visibility.py) are the shared sources of time/recurrence policy.
- Manual teacher breaks currently require explicit displacement confirmation and can permanently cancel lessons. Automatically reserving a break must not silently invoke that destructive behavior.
- **Recommended design for approval:** store a post-break duration snapshot on each lesson/series, derive its automatic break interval from each non-canceled occurrence's end, and avoid materializing years of child events. Distinguish derived automatic breaks from independently editable manual breaks. Reuse stable parent occurrence identity plus an explicit automatic-break discriminator so an automatic break is not mistaken for its parent lesson by the bot.
- This recommendation requires a reviewed schema/API change and decisions about existing lessons and preference changes. Do not assume saved duration or retrospective behavior.

## Task dependencies

| Task | Prerequisites |
|---|---|
| 00 Approve lifecycle, rollout, and UI semantics | None |
| 01 Persist approved lesson-break metadata | 00 |
| 02 Derive bounded automatic-break occurrences | 01 |
| 03 Wire transactional lesson/break lifecycle | 02 |
| 04 Enforce automatic-break availability/conflicts | 02, 03 |
| 05 Shape authorized schedule/management output | 02, 04 |
| 06 Apply preference-change behavior safely | 03, 04 |
| 07 Integrate typed bot data and rendering | 05 |
| 08 Wire Telegram workflows and settings | 06, 07; scheduling UI milestone |
| 09 Verify lifecycle end to end and document rollout | 01–08 |

## Common acceptance criteria

- Compose existing event/occurrence services, UTC/DST rules, cancellation identity and teacher serialization. No second recurrence engine or unbounded persistent occurrence expansion.
- All affected writes, including approved preference-triggered changes, validate and commit atomically under the same database-backed guard. Never silently cancel lessons to fit automatic breaks.
- Use real isolated DB fixtures and the existing recording Bot API transport. Target exact boundary, lifecycle and concurrent behaviors in SQLite/PostgreSQL; never mutate production/dev data for migration tests.
- Run targeted tests and applicable `uv run ruff check backend telegram tests`, touched-file format checks, `uv run mypy backend`, and `uv run mypy telegram`. Report baseline failures separately.
- Any schema change needs a static reversible `YYYY-MM-DD_slug.py` Alembic migration, ORM discovery, and disposable SQLite/PostgreSQL upgrade/downgrade tests; lossy downgrade must refuse safely.

## Task 00: Approve break semantics and deployment policy

**Domain:** product/backend/UI contract. **Type:** planning. **Dependencies:** None.

**Context:** README specifies adding/removing a post-break, but not retrofit, work-end, or manual-break collisions.

**What to do:** Approve representation and the following cases: break after the last lesson; whether it may extend beyond work-end/local midnight; whether the setting affects existing lessons/series or only future writes; zero-to-nonzero changes; legacy rollout; standalone editing/deleting of auto breaks; visibility to students; teacher/student vacations; manual-break overlap/merging and displacement. Recommend snapshot-on-create, no silent legacy retrofit, no standalone automatic-break editing, and no destructive displacement by automatic creation; these are proposals, not requirements. For recurring series, one skipped/moved occurrence must remove/move only its break. Decide how a manual break overlapping only an automatic break is handled and what confirmation previews contain when it overlaps the parent lesson. Preserve consecutive counts based on lessons, not extra break rows.

**Owner-approved contract (2026-10-25):**

- Reserve a post-break after every lesson, including the last lesson of a day.
  It may extend beyond work-end or local midnight and uses elapsed UTC minutes.
  It is an availability reservation, not a lesson, and does not affect lesson
  counts.
- Snapshot the configured duration on new one-off lessons and recurring series.
  Preference changes apply only to future writes. Existing lessons and series
  receive a zero-minute legacy default; there is no retrofit.
- Derive each break from its parent occurrence. Moving, deleting, canceling, or
  skipping an occurrence moves/removes only that occurrence's break.
  Automatic breaks cannot be edited or deleted independently.
- Automatic creation never displaces lessons. A manual break that overlaps
  only an automatic break is rejected rather than merged. A manual break that
  overlaps a lesson retains the existing explicit confirmation flow; its
  preview names the lesson and its attached automatic break.
- Teachers see all automatic breaks. Students see only breaks attached to
  their own visible lessons; vacation visibility follows the parent lesson.
- Telegram lesson review copy:
  - Teacher: “After this lesson, a {N}-minute break will be reserved. It may
    extend beyond working hours or midnight.”
  - Student: “The teacher’s {N}-minute break after this lesson is unavailable
    for booking.”
  - Teacher Settings save confirmation: “This change applies to new lessons
    only; existing lessons and series are unchanged.”

**Reuse:** Existing setting, vacation visibility, manual break confirmation and stable occurrence identity.

**Acceptance:** [x] Boundary examples and chosen migration/retrofit/preference semantics are recorded. [x] Teacher/student bot review text is approved. [x] The booking contract remains lesson-count-only; automatic breaks do not alter consecutive lesson counts.

**Out of scope:** Unapproved automatic cancellation or retroactive data rewrites. **Suitable for:** Human/product owner with agent preparation.

## Task 01: Add approved lesson-break storage and migration

**Domain:** existing `backend/events/`, Alembic. **Type:** schema/migration. **Dependencies:** 00.

**Context:** Current event rows have no automatic-break duration/provenance.

**What to do:** Add only the metadata required by the approved design to one-off lessons and recurring series, with nonnegative constraints and correct handling for non-lessons. Implement the approved safe legacy default/backfill policy, without modifying existing manual breaks or guessing which ones were automatic. Keep occurrence identities and cancellation rows stable.

**Reuse:** `Event`, `RecurrentEvent`, migration batch patterns; teacher preference is not duplicated.

**Acceptance:** [x] Existing instants, manual breaks and cancellations survive upgrade. [x] Constraints prevent invalid metadata; downgrade behavior and schema drift are verified on both DBs. [x] Targeted tests, Ruff, and touched-file formatting pass.

**Out of scope:** Materialized ten-year break schedules. **Suitable for:** AI agent.

**Execution:** Added nonnegative `post_break_minutes` snapshots to one-off and
recurring event rows, constrained to lessons and defaulted legacy rows to zero.
Disposable SQLite and PostgreSQL databases verified upgrade preservation,
constraint rejection, lossy-downgrade refusal, clean downgrade, and no Alembic
schema drift. Targeted tests passed (28). Ruff and touched-file formatting,
`mypy backend/events/models.py`, and `mypy telegram` passed. Full `mypy backend`
still reports existing errors in `backend/auth/config.py`,
`backend/database.py`, and `backend/events/dependencies.py`.

## Task 02: Derive automatic-break intervals through the shared occurrence engine

**Domain:** existing `backend/events/`. **Type:** service/utility. **Dependencies:** 01.

**Context:** Post-break intervals must use actual occurrence ends, not a second calendar expansion.

**What to do:** Derive `[lesson.end, lesson.end + approved_minutes)` from each surviving parent occurrence. Zero creates no interval. Use elapsed UTC minutes, explicit provenance/identity and deterministic ordering. Load parents ending before a query start whose breaks overlap the range; cancellation and query budgets must account for the derived intervals. Reject overflow/budget errors explicitly.

**Reuse:** `Occurrence`, `expand_recurrence`, range candidate loading and cancellation filtering.

**Acceptance:** [x] Exact one-off/recurring, DST, range-edge, zero, canceled occurrence and overflow cases pass. [x] No per-occurrence DB queries; complete bounded results reject over-budget ranges. [x] Targeted tests, Ruff, and touched-file formatting pass.

**Out of scope:** General derived-event framework. **Suitable for:** AI agent.

**Execution:** Added an explicit automatic-break source kind and stable
parent-occurrence identity. The shared range loader can opt in to deriving
elapsed-UTC break intervals, loads earlier parent occurrences through a single
duration aggregate, applies cancellations before derivation, filters half-open
range edges, and counts derived rows against the query budget. Thirty-six
focused occurrence/range tests passed, including DST, cancellation, overflow,
and a fixed query-count assertion; Ruff, changed-service mypy, and Telegram
mypy passed. Full `mypy backend` retains the three existing unrelated errors
recorded above.

## Task 03: Wire automatic-break lifecycle into serialized mutations

**Domain:** existing event service. **Type:** service. **Dependencies:** 02.

**Context:** Lesson changes and manual-break displacement must not leave orphan reservations.

**What to do:** Persist approved break duration with lesson creation; update it under Task 00's policy. Ensure one-off moves/deletes, series edits/deletes, exact occurrence cancellations/moves, and manual-break lesson displacement update the corresponding automatic reservations in the same transaction. Preserve a break for every unaffected parent occurrence. A failed mutation changes neither lesson nor break.

**Reuse:** Existing create/update/delete, exact-occurrence actions from the UI plan, `_run_serialized_schedule_mutation`, `_apply_displacements`.

**Acceptance:** [x] Lifecycle and rollback tests cover creation, update, move, cancel, delete, displacement, rollback, and duplicate cancellation. [x] Manual breaks remain independent; removing a parent does not restore displaced lessons. [x] Targeted tests, Ruff, and touched-file type checks pass.

**Out of scope:** Notification scheduling or independent auto-break editing. **Suitable for:** AI agent.

**Execution:** New lesson and series writes snapshot the teacher preference;
ordinary edits preserve that snapshot, and a moved series occurrence carries
its parent series' duration. Revision hashes now cover the saved duration.
Because breaks are derived, existing serialized parent mutations atomically
move/remove them without child rows. Real SQLite service tests verify create,
edit, move, cancel, duplicate cancel, delete, one-off and recurring manual-break
displacement, and failed-move rollback while retaining unaffected occurrences.
Focused lifecycle/mutation tests and event-management/occurrence-route tests
passed (46 passed, 4 PostgreSQL-only tests skipped across the focused runs).
Ruff, changed-service mypy, and Telegram mypy pass; full backend mypy retains
the three existing unrelated errors recorded above.

## Task 04: Include automatic breaks in admission and availability

**Domain:** existing conflict and booking validators. **Type:** validator/service. **Dependencies:** 02, 03.

**Context:** Display-only automatic breaks would not prevent back-to-back booking.

**What to do:** Check candidate lesson plus its post-break against existing lessons and automatic/manual breaks under the approved collision rules, including a later lesson's break overlapping a candidate. Exclude the updated parent and its derived intervals. Validate full recurrence horizons and whole policy-day windows. Keep lesson counts lesson-only; teacher custom duration changes must revalidate the resulting break. Use the same logic for any availability picker delivered by the UI plan.

**Reuse:** `validate_schedule_change`, raw validation range loader and `validate_lesson_limits`.

**Acceptance:** [x] With five minutes, a 10:00 lesson end allows next start at 10:05, rejects 10:04; zero permits exact adjacency when other rules allow. [x] Concurrent create/move cannot steal reserved time; full-series and manual-break collision cases pass. [x] Common checks pass.

**Out of scope:** Teacher override without approved confirmation semantics. **Suitable for:** AI agent.

**Execution:** Candidate lesson writes now carry their saved break duration and
are checked together with their derived reservations against lessons, manual
breaks, and existing derived breaks. Existing parent series and their derived
rows are excluded together during updates; work-window and daily/consecutive
policies continue to receive lessons only. Validation ranges cover whole policy
days and the final recurrence reservation, and derived rows count against the
validation budget. Manual-break previews include each displaced lesson's
attached automatic interval; overlaps with that linked interval are part of
the same parent confirmation, while an automatic-break-only collision rejects
without creating a cancellation target. Telegram preview parsing and rendering
were updated with the optional attached interval.

The 5-minute 10:04 rejection / 10:05 acceptance, zero-minute exact adjacency,
custom-duration candidate break, existing-break overlap, final-series-occurrence
collision, vacation display-only behavior, linked and auto-only manual-break
cases, saved-duration moves, and a competing create-versus-move write were
verified. The focused regression run passed (83 passed, 5 PostgreSQL-only
skips). Changed-file Ruff checks, backend changed-service mypy, and full
Telegram mypy passed. The full backend mypy baseline still has the three unrelated errors recorded
under Task 01.

## Task 05: Expose authorized automatic breaks consistently

**Domain:** existing schedule and management schemas/services. **Type:** service/router. **Dependencies:** 02, 04.

**Context:** Current schedule source kinds identify only persisted events/series and cannot safely identify derived breaks.

**What to do:** Add the approved minimal response discriminator/parent reference, updating backend and typed clients coherently. Apply viewer authorization and approved vacation visibility before shaping derived intervals; a student's result must not disclose another student's parent. Keep display and management schemas distinct. Make standalone mutation attempts on derived break IDs explicit errors; direct users to the parent lesson.

**Reuse:** `get_schedule`, `shape_schedule`, role filters and vacation visibility; management reads from the UI milestone.

**Acceptance:** [x] Teachers see approved auto-break rows; students see only approved own scope. [x] Row selection never edits/deletes the parent accidentally. [x] Complete pagination and old manual-break identities remain correct; common checks pass.

**Out of scope:** Raw teacher-wide data in student responses. **Suitable for:** AI agent.

**Execution:** Schedule responses now identify derived breaks with their parent
kind and occurrence start; authorization and vacation visibility are applied
before shaping, and students receive only their own parent-linked rows.
One-off and recurring break-management reads/updates/deletes reject parent
lesson IDs as read-only, while cross-teacher requests retain the not-found
response. Added coverage for schedule serialization, student scope, parent
vacation visibility, manual-break identities, and read-only mutation attempts.
Focused schedule, visibility, break-management, and Telegram schedule suites
passed (62 passed). Changed-file Ruff check/format and backend plus Telegram
mypy passed. Commit: `af69c02`.

## Task 06: Implement safe post-break preference updates

**Domain:** existing auth preference update/event services. **Type:** service. **Dependencies:** 03, 04.

**Context:** `update_teacher_profile` currently commits profile updates independently of event mutations.

**What to do:** Implement the approved snapshot/live-update behavior for `between_lessons_break` under teacher serialization where it affects scheduling. If retroactive updates were approved, bound the operation, validate all affected reservations, and roll back the entire change on conflict; do not partially update a series or silently reduce breaks. If changes apply only to future lessons, return/explain that exact scope and preserve saved snapshots.

**Reuse:** Existing merged preference validation, teacher guard and occurrence validators.

**Acceptance:** [x] Nonzero/zero transitions and conflicts preserve unrelated preferences. [x] Concurrent booking uses a coherent policy snapshot; no partial retrofit. [x] Common checks pass.

**Out of scope:** Rewriting old lesson times. **Suitable for:** AI agent.

**Execution:** Teacher preference updates now run under the same
database-backed schedule guard as lesson writes, refresh the profile after
acquiring the guard, and preserve saved snapshots on existing one-off and
recurring lessons. API schema/route descriptions state the future-only scope.
Regression coverage verifies zero/nonzero changes, conflicting bookings,
unrelated profile values, and concurrent guard contention/retry. Focused
backend and bot suites passed (60 passed, 5 PostgreSQL-only skips); repository
Ruff, touched-file format, changed-file mypy, and Telegram mypy passed. Full
backend mypy retains its three previously recorded unrelated baseline errors.
Commit: `dbeb54b`.

## Task 07: Update Telegram models and schedule rendering

**Domain:** existing bot client and schedule formatter. **Type:** bot. **Dependencies:** 05.

**Context:** Existing `ScheduleItem` validates strict source kinds and requires coordinated changes.

**What to do:** Parse approved metadata, render clearly labeled automatic breaks with local times, and retain complete UTF-16-safe pagination and NOW behavior. For automatic rows, offer parent lesson details or explain read-only status; preserve manual-break editing. Handle mismatched/invalid responses explicitly rather than silently dropping rows.

**Reuse:** `BackendClient`, `ScheduleItem`, `format_schedule_pages`, event details from the UI plan.

**Acceptance:** [x] Exact teacher/student text, DST times, identity handling and long-week pagination pass with no omitted events. [x] Existing schedule/onboarding tests and common checks pass.

**Out of scope:** Offline caching. **Suitable for:** AI agent.

**Execution:** Automatic schedule rows are now labeled read-only with
role-specific text; manual breaks retain their existing label. Formatting uses
viewer-local times and the existing NOW divider. Tests cover shared parent IDs
with distinct source kinds, DST fold offsets, invalid parent metadata, and a
160-row week paginated without omissions or Telegram UTF-16 budget violations.
The real bot schedule dialog displays the row on refresh. Focused Telegram
schedule/client tests passed (42 passed); repository Ruff, touched-file format,
and full Telegram mypy passed. Commit: `ea3c246`.

## Task 08: Integrate automatic breaks into Telegram booking and Settings

**Domain:** Telegram scheduling/settings dialogs. **Type:** bot. **Dependencies:** 06, 07 and completed scheduling UI plan.

**Context:** Backend automatic breaks need visible user-facing consequences, not just new schedule rows.

**What to do:** Show the computed post-break in lesson review/success, clarify the teacher-only custom lesson duration effect, and make break-conflict failures actionable in create/move screens. Keep automatic and manual breaks distinct in management/confirmation screens. Remove the pending-feature text from Settings, expose existing zero/nonzero preference editing, and explain the approved future-only/retroactive effect before Save.

**Reuse:** Scheduling/settings dialogs, error mapper, safe mutation/reload flow.

**Acceptance:** [ ] Both roles create/move/cancel lessons and observe correct auto-break results. [ ] Teachers change break preference with correct scope/confirmation; students cannot mutate teacher settings or auto breaks. [ ] Same-message/error/common checks pass.

**Out of scope:** New preference fields or bot-side admission logic. **Suitable for:** AI agent.

## Task 09: Verify full lifecycle, migrations, and Telegram acceptance

**Domain:** tests and docs. **Type:** integration/documentation. **Dependencies:** 01–08.

**Context:** Correctness spans derived occurrences, write serialization, saved settings, and bot actions.

**What to do:** Test real backend + dispatcher sequences for one-off/series creation, move, cancellation, deletion, manual-break preview/confirm, vacation hide/unhide, preference changes, cross-midnight/DST boundaries, and concurrent competing writes. Assert exact parent/break identities and rows after failures. Update README/AGENTS and deployment migration instructions; provide explicit legacy behavior and rollback constraints.

**Reuse:** Booking policy/concurrency suites and bot integration transport.

**Acceptance:** [ ] Both supported DBs and targeted bot flows pass with recorded evidence. [ ] All Telegram entries are reachable and edit the active screen except justified fallback. [ ] Automatic breaks are marked shipped only after backend and bot acceptance.

**Out of scope:** Homework or notification delivery. **Suitable for:** AI agent; human for optional live UI smoke.

## Open questions and release boundaries

- Existing conflicting schedules must not be repaired by guessed cancellation/backfill. Report categories on approved data sources and request remediation decisions.
- Notification consumers must eventually use the same lesson visibility and must not treat automatic breaks as lessons.
- Next milestone: [homework backend and Telegram UI](./homework-backend-and-telegram-ui.md).
