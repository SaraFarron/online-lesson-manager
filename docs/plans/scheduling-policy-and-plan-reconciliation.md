# Scheduling policy and plan reconciliation — Implementation plan

## Goal

Reconcile and organize the existing implementation plans, then enforce the saved teacher booking rules and prevent concurrent schedule writes from accepting conflicting bookings.

## Scope (README.md sections covered)

- [Schedule changes](../../README.md#изменение-расписания): lesson admission, teacher work windows and days, lesson duration, daily and consecutive lesson limits, and race-safe writes.
- [Start/settings](../../README.md#старт): reuse persisted teacher settings and their documented defaults.
- Project plan/status documentation: align completed-plan checklists with verified work and store plans under `docs/plans/`, not the repository root.

## Current repository evidence

- `TeacherProfile` already persists `work_start`, `work_end`, `working_days`, `lesson_length`, `between_lessons_break`, `max_consecutive_lessons`, and `max_lessons_per_day`; the onboarding/default values are documented in [AGENTS.md](../../AGENTS.md).
- The shared `validate_schedule_change` in `backend/events/conflicts.py` expands occurrences and checks temporal overlaps, vacations, and confirmed break displacement. `EventValidator` composes this for lessons, breaks, and other event domains.
- `backend/events/service.py` owns event and recurring-event writes, applies confirmed break displacements, then commits. All scheduling writes that can affect admission/conflicts must use one concurrency strategy; route-only protection or an in-process lock is insufficient.
- The approved scheduling foundation in [SCHEDULING-FOUNDATION-PLAN.md](./SCHEDULING-FOUNDATION-PLAN.md) requires complete validation of new/updated recurrences up to 10 years, explicit failure rather than silent truncation, UTC storage, teacher-local recurrence, cancellation-aware conflict checks, and stable break-displacement behavior.
- Feature status in [AGENTS.md](../../AGENTS.md) correctly describes work-hour/day, duration, daily/consecutive limits, automatic breaks, and transaction-level concurrency as pending. Automatic break creation is outside this plan.
- The Telegram-auth plan records tasks 00–13 as implemented and verified, with full Compose startup and real separate-server HTTPS deployment still unverified. The week-navigation task now records its completed implementation, tests, and documentation; the scheduling foundation and schedule-viewing plans also record completion.
- The four existing root-level plan/task documents have been reconciled and moved into `docs/plans/`; relative links and in-repository references point to the canonical location.

## Tasks

### 1. Reconcile completion records and organize plan documents

**Domain:** project documentation  
**Type:** planning/documentation  
**Dependencies:** None

Review the execution evidence in the Telegram-auth, scheduling-foundation, schedule-viewing, and Telegram-week-navigation documents. Correct checkboxes and status summaries so they agree; retain unchecked status only for work explicitly unverified (including full Compose startup and real separate-server HTTPS deployment). Move the existing root plan/task documents into `docs/plans/`, update their relative links and any in-repository references, and make `docs/plans/` the canonical location for future plan Markdown files.

Do not claim tests or deployment checks passed unless the existing plan records that evidence. Preserve the existing untracked week-navigation task content while moving it.

### 2. Approve booking-rule edge cases and concurrency contract

**Domain:** product/backend contract  
**Type:** planning  
**Dependencies:** 1

Record the precise admission semantics before implementation, reusing the approved scheduling-foundation semantics wherever they apply. Confirm:

- Which event types and actors are constrained by teacher work hours/days and lesson length; the README clearly describes student booking and teacher-controlled duration, but does not spell out every teacher-created/personal-event case.
- Whether an event ending exactly at `work_end` is valid; whether local working-day membership is determined by the occurrence's teacher-local start date; and how to handle any event that crosses local midnight.
- How lesson duration is represented at the API boundary. Recommended backward-compatible shape: keep accepting explicit `end`; allow it to be omitted for lesson creation and derive it from the teacher's `lesson_length`; permit a non-default duration only for teacher-authorized writes. Existing clients that send `end` continue to work, subject to the approved actor rule.
- Count semantics for daily and consecutive limits: count all teacher lessons by teacher-local occurrence start date; canceled occurrences do not count; define how the configured `between_lessons_break` determines whether two lessons belong to one consecutive chain. Proposed rule: a gap less than or equal to that setting joins the chain, including exact adjacency when the setting is zero.
- That lesson limits apply to every occurrence of a new or updated series across the full accepted recurrence horizon, and how legacy `UTC_STEP` occurrences are evaluated against teacher-local work hours/days.
- A database-backed serialization strategy that works for the supported SQLite and PostgreSQL deployments. It must cover every schedule mutation that can race with validation or displacement (lessons, breaks, vacations, one-off and recurring), not merely lesson routes. Do not rely solely on a process-local mutex.

Record approved choices in this plan before implementing dependent tasks. No new teacher settings are in scope: their fields/defaults already exist.

#### Owner-approved booking and concurrency contract (2026-10-23)

The product owner approved the following semantics for Tasks 3–6. Preserve the
existing authorization rules; this contract does not grant new roles access to
schedule mutations.

**Lesson admission and duration**

- Work-day, work-hour, duration, daily-count, and consecutive-count rules apply
  to every lesson on the teacher's calendar: teacher personal lessons and
  lessons for all assigned students, regardless of whether the authorized
  writer is the teacher or a student. Breaks and vacations are not subject to
  these lesson booking rules; their existing validation remains authoritative.
- Evaluate a lesson against its teacher-local occurrence start date and require
  its entire interval to fit within that local work day. The start must be at
  or after `work_start`; an end exactly equal to `work_end` is valid. Reject
  any lesson that crosses local midnight.
- At the API boundary, retain explicit `end` values for compatibility. A
  lesson create may omit `end`, in which case derive it by adding the
  configured `lesson_length` as elapsed minutes to `start`. A student-created
  lesson must use that configured duration. A non-default duration is allowed
  only when the existing route authorization identifies a teacher-authorized
  write; do not broaden ownership or authorization. No migration or new setting
  is approved.

**Daily/consecutive counts and recurrence**

- Count every active, non-canceled teacher lesson, including personal lessons
  and lessons for all assigned students. Group lessons by their evaluated
  teacher-local occurrence start date. When updating a one-off event or series,
  exclude that event or series from the existing count before adding its
  candidate occurrence(s).
- A consecutive chain comprises chronologically adjacent lessons on the same
  evaluated local date when the elapsed gap from the previous lesson's end to
  the next lesson's start is less than or equal to
  `between_lessons_break`. Thus zero joins exact adjacency only. Enforce both
  `max_lessons_per_day` and `max_consecutive_lessons` against existing and
  candidate occurrences.
- Apply these checks to every candidate occurrence over the complete accepted
  recurrence horizon, up to the scheduling foundation's ten-calendar-year
  maximum. A canceled occurrence does not count. Use the immutable timezone
  snapshot for a new teacher-local series; evaluate its work hours, working
  day, and local-date counts in that snapshot. Evaluate one-off events and
  legacy `UTC_STEP` series in the teacher's current profile timezone, converting
  each generated UTC occurrence before checking local policy. Order elapsed
  gaps by UTC instants. Preserve the foundation's explicit horizon, expansion
  budget, and invalid-data failures; never silently truncate.

**Database-backed serialization**

- Use a dedicated persistent guard row keyed by teacher. Acquire it with an
  atomic insert-or-update before loading schedule conflict or booking-limit
  inputs, and hold its transaction lock through validation, confirmed break
  displacement/cancellation, event writes, and transaction completion.
- PostgreSQL serializes mutations for the same teacher on that teacher's guard
  row. SQLite's database write-lock semantics may serialize different
  teachers more coarsely, but must prevent two concurrent schedule mutations
  from both validating against stale inputs and committing conflicting results.
  The guard row is the database-backed correctness boundary, not an
  in-process mutex. Add a static reversible migration for the guard table if
  required by the implementation.
- Apply this mechanism to all lesson, break, and vacation mutations, both
  one-off and recurring, including writes that can displace or cancel lessons.

### 3. Serialize schedule validation and writes per teacher

**Domain:** `backend/events/` and event-domain write routes  
**Type:** service/transaction  
**Dependencies:** 2

Implement the approved database-backed per-teacher serialization mechanism. Acquire it before loading conflict/limit inputs; hold it through validation, any confirmed break displacement/cancellation, the event write, and transaction completion. Apply it consistently to all event mutations that can affect booking decisions, including lesson, break, and vacation CRUD, both one-off and recurring.

Preserve the existing service/domain composition and confirmed break semantics. Keep validation and mutations atomic; do not introduce a second scheduling service or a process-local lock as the correctness boundary. Add no migration unless the approved mechanism genuinely requires schema changes; any required migration must be static, reviewed, reversible, and named `YYYY-MM-DD_slug.py`.

### 4. Enforce teacher work windows, working days, and lesson duration

**Domain:** `backend/events/` and `backend/lessons/`  
**Type:** validator/service/API schema  
**Dependencies:** 2, 3

Compose a booking-policy check with the existing `EventValidator` and `validate_schedule_change` flow. For lesson creates and relevant updates, use the persisted teacher timezone and `TeacherProfile` preferences to check local work days and hours. Validate every occurrence of a recurring candidate within the approved horizon; use the immutable recurrence timezone for teacher-local series and the approved legacy mode for existing UTC-step series. Do not query per occurrence or silently truncate validation.

Implement the duration contract approved in Task 2. Default new lessons from `lesson_length` where permitted; preserve explicit end-time requests for compatibility; enforce that only an authorized teacher can select a non-default duration. Keep existing ownership, vacation, conflict, and break-displacement behavior intact. Do not apply lesson booking limits to vacations or breaks unless Task 2 explicitly approves a need; their existing domain validation remains authoritative.

No settings migration is expected. If the chosen request contract requires a schema change or a legacy-data change, stop and document the migration/data decision before proceeding.

### 5. Enforce daily and consecutive lesson limits

**Domain:** `backend/events/`  
**Type:** validator/service  
**Dependencies:** 2, 3, 4

For lesson creates and updates, count candidate and existing visible-to-conflict-calendar lesson occurrences for the teacher, including teacher personal lessons and lessons for all assigned students. Group counts by the approved teacher-local occurrence date. Exclude canceled occurrences and the event/series being updated; include all other applicable occurrences, including recurring instances across the complete accepted validation horizon. Apply `max_lessons_per_day` and the approved consecutive-chain rule based on `between_lessons_break`.

Use the existing bounded occurrence and range-query building blocks in `backend/events/occurrences.py` and `backend/events/range_service.py` (or a narrowly extended shared helper) rather than implementing another recurrence engine. Preserve the foundation's explicit budget/horizon errors and vacation semantics. Return the repository-standard validation/conflict response with enough detail to explain which policy rejected the lesson, without disclosing another student's private profile data.

### 6. Verify the complete booking contract and update feature status

**Domain:** `tests/`, `backend/`, project documentation  
**Type:** integration/documentation  
**Dependencies:** 1, 3, 4, 5

Add focused real-database integration coverage for teacher/student authorization, default and explicit lesson duration, work-hour/day boundaries, max/day, consecutive chains (including exact gap boundary and zero break), daily grouping near UTC midnight and DST transitions, recurrence/cancellation/update behavior, and break/vacation interactions. Add deterministic concurrent-request tests proving that conflicting lesson, break, or vacation mutations cannot both pass validation and commit inconsistent results. Exercise SQLite and PostgreSQL for transaction behavior.

Update [AGENTS.md](../../AGENTS.md) and the scheduling handoff in [README.md](../../README.md) to distinguish implemented booking enforcement and race protection from automatic break creation, which remains out of scope. Record exact targeted test/lint/typecheck and migration results; call out unrelated baseline failures without repairing unrelated issues.

## Out of scope for this release

- Automatically creating/updating/deleting between-lesson break events when lessons change.
- Notification delivery, homework, student history, HTMX screens, and offline schedule caching.
- New teacher preference fields or speculative scheduling settings.
- Changing the approved recurrence, UTC storage, vacation visibility, cancellation, or confirmed break-displacement contracts.
- Production data inspection or mutation.

## Open questions

- Task 2's product decisions and database-backed guard-row strategy are approved above. Tasks 3–6 must implement that contract; if the current session/transaction setup cannot provide the approved SQLite and PostgreSQL guarantees, stop and obtain approval for the smallest required adjustment rather than claiming race safety.
- Verify the current occurrence/range service can validate per-day and consecutive limits for all accepted recurrence horizons within its explicit query/expansion budgets. If it cannot, identify the precise bounded extension needed; never silently sample or truncate the schedule.
