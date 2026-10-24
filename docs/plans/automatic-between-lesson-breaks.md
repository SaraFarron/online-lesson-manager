# Automatic between-lesson breaks — Plan

## Goal

Every active lesson reserves the teacher's configured post-lesson break automatically, with consistent create/edit/cancel/delete behavior and clear Telegram presentation and settings.

**Status:** Planned; product decisions below are not yet approved.

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

**Reuse:** Existing setting, vacation visibility, manual break confirmation and stable occurrence identity.

**Acceptance:** [ ] Boundary examples and chosen migration/retrofit/preference semantics are recorded. [ ] Teacher/student bot review text is approved. [ ] Any conflict with the existing booking contract is explicitly reconciled before implementation.

**Out of scope:** Unapproved automatic cancellation or retroactive data rewrites. **Suitable for:** Human/product owner with agent preparation.

## Task 01: Add approved lesson-break storage and migration

**Domain:** existing `backend/events/`, Alembic. **Type:** schema/migration. **Dependencies:** 00.

**Context:** Current event rows have no automatic-break duration/provenance.

**What to do:** Add only the metadata required by the approved design to one-off lessons and recurring series, with nonnegative constraints and correct handling for non-lessons. Implement the approved safe legacy default/backfill policy, without modifying existing manual breaks or guessing which ones were automatic. Keep occurrence identities and cancellation rows stable.

**Reuse:** `Event`, `RecurrentEvent`, migration batch patterns; teacher preference is not duplicated.

**Acceptance:** [ ] Existing instants, manual breaks and cancellations survive upgrade. [ ] Constraints prevent invalid metadata; downgrade behavior and schema drift are verified on both DBs. [ ] Common checks pass.

**Out of scope:** Materialized ten-year break schedules. **Suitable for:** AI agent.

## Task 02: Derive automatic-break intervals through the shared occurrence engine

**Domain:** existing `backend/events/`. **Type:** service/utility. **Dependencies:** 01.

**Context:** Post-break intervals must use actual occurrence ends, not a second calendar expansion.

**What to do:** Derive `[lesson.end, lesson.end + approved_minutes)` from each surviving parent occurrence. Zero creates no interval. Use elapsed UTC minutes, explicit provenance/identity and deterministic ordering. Load parents ending before a query start whose breaks overlap the range; cancellation and query budgets must account for the derived intervals. Reject overflow/budget errors explicitly.

**Reuse:** `Occurrence`, `expand_recurrence`, range candidate loading and cancellation filtering.

**Acceptance:** [ ] Exact one-off/recurring, DST, range-edge, zero, canceled occurrence and overflow cases pass. [ ] No per-occurrence DB queries; complete bounded results. [ ] Common checks pass.

**Out of scope:** General derived-event framework. **Suitable for:** AI agent.

## Task 03: Wire automatic-break lifecycle into serialized mutations

**Domain:** existing event service. **Type:** service. **Dependencies:** 02.

**Context:** Lesson changes and manual-break displacement must not leave orphan reservations.

**What to do:** Persist approved break duration with lesson creation; update it under Task 00's policy. Ensure one-off moves/deletes, series edits/deletes, exact occurrence cancellations/moves, and manual-break lesson displacement update the corresponding automatic reservations in the same transaction. Preserve a break for every unaffected parent occurrence. A failed mutation changes neither lesson nor break.

**Reuse:** Existing create/update/delete, exact-occurrence actions from the UI plan, `_run_serialized_schedule_mutation`, `_apply_displacements`.

**Acceptance:** [ ] Lifecycle and rollback tests cover all write entrypoints and duplicate requests. [ ] Manual breaks do not get deleted with unrelated lessons; removal does not restore displaced lessons. [ ] Common checks pass.

**Out of scope:** Notification scheduling or independent auto-break editing. **Suitable for:** AI agent.

## Task 04: Include automatic breaks in admission and availability

**Domain:** existing conflict and booking validators. **Type:** validator/service. **Dependencies:** 02, 03.

**Context:** Display-only automatic breaks would not prevent back-to-back booking.

**What to do:** Check candidate lesson plus its post-break against existing lessons and automatic/manual breaks under the approved collision rules, including a later lesson's break overlapping a candidate. Exclude the updated parent and its derived intervals. Validate full recurrence horizons and whole policy-day windows. Keep lesson counts lesson-only; teacher custom duration changes must revalidate the resulting break. Use the same logic for any availability picker delivered by the UI plan.

**Reuse:** `validate_schedule_change`, raw validation range loader and `validate_lesson_limits`.

**Acceptance:** [ ] With five minutes, a 10:00 lesson end allows next start at 10:05, rejects 10:04; zero permits exact adjacency when other rules allow. [ ] Concurrent create/move cannot steal reserved time; full-series and manual-break collision cases pass. [ ] Common checks pass.

**Out of scope:** Teacher override without approved confirmation semantics. **Suitable for:** AI agent.

## Task 05: Expose authorized automatic breaks consistently

**Domain:** existing schedule and management schemas/services. **Type:** service/router. **Dependencies:** 02, 04.

**Context:** Current schedule source kinds identify only persisted events/series and cannot safely identify derived breaks.

**What to do:** Add the approved minimal response discriminator/parent reference, updating backend and typed clients coherently. Apply viewer authorization and approved vacation visibility before shaping derived intervals; a student's result must not disclose another student's parent. Keep display and management schemas distinct. Make standalone mutation attempts on derived break IDs explicit errors; direct users to the parent lesson.

**Reuse:** `get_schedule`, `shape_schedule`, role filters and vacation visibility; management reads from the UI milestone.

**Acceptance:** [ ] Teachers see approved auto-break rows; students see only approved own scope. [ ] Row selection never edits/deletes the parent accidentally. [ ] Complete pagination and old manual-break identities remain correct; common checks pass.

**Out of scope:** Raw teacher-wide data in student responses. **Suitable for:** AI agent.

## Task 06: Implement safe post-break preference updates

**Domain:** existing auth preference update/event services. **Type:** service. **Dependencies:** 03, 04.

**Context:** `update_teacher_profile` currently commits profile updates independently of event mutations.

**What to do:** Implement the approved snapshot/live-update behavior for `between_lessons_break` under teacher serialization where it affects scheduling. If retroactive updates were approved, bound the operation, validate all affected reservations, and roll back the entire change on conflict; do not partially update a series or silently reduce breaks. If changes apply only to future lessons, return/explain that exact scope and preserve saved snapshots.

**Reuse:** Existing merged preference validation, teacher guard and occurrence validators.

**Acceptance:** [ ] Nonzero/zero transitions and conflicts preserve unrelated preferences. [ ] Concurrent booking uses a coherent policy snapshot; no partial retrofit. [ ] Common checks pass.

**Out of scope:** Rewriting old lesson times. **Suitable for:** AI agent.

## Task 07: Update Telegram models and schedule rendering

**Domain:** existing bot client and schedule formatter. **Type:** bot. **Dependencies:** 05.

**Context:** Existing `ScheduleItem` validates strict source kinds and requires coordinated changes.

**What to do:** Parse approved metadata, render clearly labeled automatic breaks with local times, and retain complete UTF-16-safe pagination and NOW behavior. For automatic rows, offer parent lesson details or explain read-only status; preserve manual-break editing. Handle mismatched/invalid responses explicitly rather than silently dropping rows.

**Reuse:** `BackendClient`, `ScheduleItem`, `format_schedule_pages`, event details from the UI plan.

**Acceptance:** [ ] Exact teacher/student text, DST times, identity handling and long-week pagination pass with no omitted events. [ ] Existing schedule/onboarding tests and common checks pass.

**Out of scope:** Offline caching. **Suitable for:** AI agent.

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

- Task 00 is an owner decision gate, not implied approval of the proposed snapshot design.
- Existing conflicting schedules must not be repaired by guessed cancellation/backfill. Report categories on approved data sources and request remediation decisions.
- Notification consumers must eventually use the same lesson visibility and must not treat automatic breaks as lessons.
- Next milestone: [homework backend and Telegram UI](./homework-backend-and-telegram-ui.md).
