# Consistent Telegram event creation and editing — Plan

## Goal

Make every implemented Telegram event creation and update flow follow the
working lesson UX: accepted date/time input disappears, defaults avoid unnecessary
steps, and focused edits preserve unrelated draft values.

**Status:** In progress; Task 01 is complete (SQLite verified; PostgreSQL unavailable).

## Scope (README.md sections covered)

- [Bot interface](../../README.md#интерфейс-бота): edit-in-place navigation,
  accepted-input cleanup, date/time parsing, errors, and recovery.
- [Schedule changes](../../README.md#изменение-расписания): one-time and weekly
  lesson/break creation and updates, lesson occurrence moves, and finite
  vacation creation/updates for authorized teachers and students.
- [Time and recurrence contract](../../README.md#time-and-recurrence-contract)
  and [open-ended series handoff](../../README.md#open-ended-weekly-lessons-and-deployment):
  extend genuine open-ended recurrence to weekly manual breaks.

This follows the completed [interface streamlining](./telegram-interface-streamlining.md),
[UI refinements](./telegram-ui-refinements.md), and
[error feedback](./telegram-error-emphasis.md) plans. It intentionally supersedes
their **finite-only recurring-break** boundary and **lesson-only time cleanup**
boundary, not their completed verification records.

## Analysis: screen inventory and confirmed gaps

| Surface | Current sequence / behavior | Planned treatment |
|---|---|---|
| Teacher one-time lesson creation | Kind → date → time → review; personal assignee and saved duration default; optional overrides | Reference UX; retain authorization and exact payload behavior |
| Student one-time lesson creation | Kind → date → time → review; own account and teacher duration | Reference UX; no new assignee/duration permissions |
| Teacher/student weekly lesson creation | Same short path; defaults to `No end`; optional inclusive cutoff with rollback on Back | Reference UX; retain finite choices through unrelated edits |
| One-time lesson update | Fresh detail → new date → time → review; stored duration retained | Preserve; regression coverage for cleanup and focused edits |
| Weekly lesson update / cutoff-only update | Start edits retain existing cutoff; direct optional cutoff editor supports `No end` | Preserve both finite and open-ended behavior |
| Lesson occurrence selection and move | Enter exact source occurrence date/time; move enters replacement date/time and creates a one-off replacement | Include cleanup/navigation regression coverage; preserve exact identity and atomic cancellation/replacement |
| Teacher one-time break creation | Break list → Add → date → start time → end time → review | Keep explicit break interval; clean accepted start/end times and preserve draft edits |
| Teacher weekly break creation | Same path, then mandatory last-date prompt | Default to `No end`, go straight to review; make cutoff an optional edit |
| One-time/weekly break update | `begin_edit` clears all fields and restarts date entry; weekly cutoff must be re-entered | Seed from fresh detail, open review, and edit only chosen fields |
| Break review field changes | Date/end-time changes clear cutoff; accepted end time always clears it again; opening cutoff editor discards prior choice | Retain cutoff and unrelated values; transactional editor Back; recompute only affected resolution/preview |
| Teacher vacation creation | Mandatory student/personal picker before date → start time → inclusive end date | Default to personal vacation, with optional authorized assignee picker like lessons |
| Student vacation creation | Own subject; date → start time → inclusive end date | Preserve required finite range; clean accepted time |
| Teacher/student vacation update | Saved fields loaded into review; field edits available | Retain this good baseline, add reliable Back/Cancel and cleanup |
| Vacation review edits and repeated creation | Creation retains `edit_flow="create"`, so a review start-date edit replays subsequent steps; `begin_create` does not reset `date_field` / pending confirmation state | Separate initial collection from focused review edits; reset transient field state between drafts |
| Recurring vacations | No Telegram creation/update dialog or dedicated recurring-vacation router | Not an implemented surface; do not invent one |
| Automatic post-lesson reservations | Derived/read-only, not manual event forms | Remain non-editable; protect parent behavior and displacement previews |

### Root causes and reusable building blocks

- [AGENTS.md Feature Status](../../AGENTS.md#feature-status-vs-product-spec)
  confirms that scheduling CRUD is built. There is no dedicated “Known Issues /
  Tech Debt” section in the current file. This is a consistency follow-up, not
  a rebuild of existing domain features.
- [Lesson creation](../../telegram/lesson_dialogs.py) `start_time_input`
  cleans input only after backend local-time resolution succeeds;
  `_begin_cutoff_edit`, `_restore_cutoff_edit`, and `_finish_cutoff_edit`
  demonstrate saved-choice preservation. Optional assignee selection records
  a return screen and does not eagerly load the roster.
- [Lesson management](../../telegram/event_management_dialogs.py) `time_input`,
  `_open_cutoff_editor`, and `_resolved_interval` already handle accepted
  input, nullable cutoffs, duration preservation, and occurrence moves.
  Treat these as behavioral prior art, not a requirement to merge all dialogs.
- [Break dialogs](../../telegram/break_dialogs.py) import only
  `delete_accepted_date_message`; `start_time_input` ignores its incoming
  message, and `end_time_input` never deletes it. `begin_edit`,
  `change_start_date`, `change_end_time`, `change_cutoff_date`, `_review_text`,
  `_build_payload`, and `detail_data` contain the reset/finite-only assumptions.
- [Vacation dialogs](../../telegram/vacation_dialogs.py) `start_time_input`
  also ignores its message. `begin_create`, `_date_input`, `_accept_date`,
  and `date_data` share transient state that must be reset and routed
  deliberately. `_local_interval` already owns inclusive vacation-end handling.
- [Input cleanup](../../telegram/date_cleanup.py)
  `delete_accepted_input_message` already supports `"date"` and `"time"`,
  classified Telegram deletion failures, warning logs, and inline attention.
  [Runtime middleware](../../telegram/runtime.py) `EditInputMiddleware`
  selects `ShowMode.EDIT`; it does not delete user messages.
- [Input utilities](../../telegram/inputs.py) own `parse_schedule_date`,
  `parse_schedule_time`, `combine_schedule_date_time`,
  `format_schedule_date_confirmation`, `format_duration`, and interval/date
  formatting. [BackendClient](../../telegram/api.py) owns
  `resolve_local_interval`; do not duplicate timezone/DST logic in dialogs.
- Open-ended breaks are currently rejected independently by
  [ORM constraints](../../backend/events/models.py),
  [shared schemas](../../backend/events/schemas.py)
  `_validate_open_ended_recurrence`,
  [break schemas](../../backend/breaks/schemas.py)
  `RecurrentBreakUpdate.require_recurrence_cutoff`,
  [event service](../../backend/events/service.py)
  `_create_recurrent_event` / `_update_recurrent_event`,
  [conflict validation](../../backend/events/conflicts.py)
  `_candidate_occurrences`, and four model guards in the
  [typed bot client](../../telegram/api.py). All must agree before the UI ships.
- [Occurrence expansion](../../backend/events/occurrences.py)
  `expand_recurrence` already supports null cutoffs with bounded queries;
  `open_ended_validation_cutoff` already implements the one-calendar-year
  anniversary with leap-day/DST handling. Reuse
  [range service](../../backend/events/range_service.py)
  `load_raw_occurrences_for_validation` and existing management serializers.
- Active [break dependencies](../../backend/breaks/dependencies.py) return
  `default_break_validator` from [break validators](../../backend/breaks/validators.py).
  `EventValidator.validate_all` composes `TimeValidation`, user checks, and
  `validate_schedule_change`; these are wired, not dead code.
  The generic [event router](../../backend/events/router.py) is empty and its
  [dependency aliases](../../backend/events/dependencies.py) are not the
  break write path. Do not route this work through them or schedule unrelated
  cleanup.
- Existing break/vacation test helpers assert date deletion but not time
  deletion. Some schema tests explicitly require breaks to stay finite;
  update those expectations intentionally rather than weakening all guards.

## Agreed behavior and safety contract

### Common event-form UX

- Keep the working lesson defaults and permissions. Apply teacher-personal
  default plus optional **Change assignee** to vacation creation; students
  always act for themselves. Breaks remain teacher-wide, with no picker.
- Use the same date/time grammar, inferred-next-year confirmation, human
  dates/durations, escaped leading feedback, and edit-in-place rendering.
  Date prompts omit timezone hints; reviews retain timezone context.
- Delete exactly the accepted date/time message, never chat history.
  Invalid/rejected input stays. For independently accepted start-time fields,
  accept only after local-time resolution succeeds, as in lessons; end-time
  fields additionally require valid interval resolution. A later write-level
  scheduling rejection does not undo earlier accepted input.
- Inferred-year date input can be deleted when accepted as a pending date;
  confirmation still occurs before applying it. Expected deletion failures
  log and show attention without losing the draft or retrying the backend
  mutation. Do not globally delete duration inputs, commands, homework, or files.
- Optional edits return to their caller. **Back** abandons only the in-progress
  field edit and restores its previous value; **Cancel** exits without a
  backend write. Preserve unrelated fields and confirmed cutoffs.
- Changes affecting interval resolution invalidate/recompute it before Save.
  Changes affecting a displacement request discard its old preview/token.
  No enabled submit for an incomplete/invalid draft, even if review rendering
  reports a recoverable error.
- Updates start from current detail and retain revision checks. Do not add
  unrelated editing capabilities absent from lesson management, such as a new
  update-time assignee transfer or duration override.
- Preserve existing stale-revision reload, duplicate-submit guards, uncertain
  outcome reconciliation, and no blind write replay. Deleting a manual break
  never restores displaced lessons; vacations remain display-only overlays.

### Weekly breaks: owner-approved policy

Confirmed during this planning session:

1. New weekly breaks default to genuinely **No end**, matching lessons.
   Store `interval_end=null`; never use a distant/sentinel date.
2. Only interval-7 lessons and manual breaks can be open-ended. Recurring
   vacations and nonweekly breaks still require finite cutoffs. Existing
   finite series and their intervals remain unchanged unless explicitly edited.
3. Use the existing **one calendar year from submitted series start** validation
   window in the snapshotted recurrence timezone (UTC for legacy `UTC_STEP`).
   This is not a stored cutoff or a rolling “today plus one year” horizon.
   Finite recurrences retain the ten-year limit and occurrence budgets.
4. An open-ended break preview/confirmation displaces only the exact affected
   lesson occurrences in that validation window. Include full interval tails;
   preserve existing automatic-break handling and preview-token revalidation.
   There is no scheduled future cancellation job or implicit later displacement.
5. Clearly disclose the limitation on open-ended break review, detail, and
   displacement confirmation: existing lessons beyond that window may overlap
   the recurring break. Later cancellation requires a new explicit preview and
   confirmation through an existing edit or a finite break write covering it.
   Saving the same old start does not advance its one-year window.
6. Later lesson/event writes still expand existing open-ended breaks over their
   own requested bounded validation ranges and reject applicable conflicts.
   Do not claim that existing later lessons were checked or cancelled.

## Task 01: Extend the persisted recurrence contract to weekly breaks

**Domain:** Existing shared events and break schemas. **Type:** schema/migration.
**Dependencies:** None. **Migration:** Required.

### Context

The approved policy extends the current README lesson-only null-cutoff contract.
The column is already nullable; the check constraint and validation guards are
the blockers.

### What to do

- Update `RecurrentEvent`'s `interval_end_after_start` constraint to permit
  null only for interval-7 `LESSON` or `BREAK`.
- Add a new static `YYYY-MM-DD_open_ended_weekly_breaks.py` migration after the
  actual current Alembic head; do not edit the shipped lesson migration.
  Preserve all rows and existing finite cutoffs.
- Downgrade refuses before any DDL/data mutation while null-cutoff breaks
  exist. Otherwise restore the preceding lesson-only constraint, retaining
  legitimate null-cutoff lessons and the nullable column.
- Extend `_validate_open_ended_recurrence` and the specialized break-update
  guard consistently, including explicit null on update and nonweekly rejection.
  Preserve serializers and correct directly touched cutoff descriptions to
  distinguish the API's exclusive occurrence-start boundary from UI inclusive dates.

### Reuse

Existing shared schema validation, `UtcTimestamp`, constraint naming, and the
batch-migration/refusal patterns in
[the lesson migration](../../alembic/versions/2026-10-27_open_ended_weekly_lessons.py).

### Acceptance Criteria

- [x] Weekly lesson/break null cutoffs pass shared/specialized schema tests;
  nonweekly break and vacation null cutoffs fail.
- [x] SQLite upgrade/downgrade tests preserve finite rows and open lessons;
  downgrade refusal preserves schema and data when open breaks exist.
- [x] PostgreSQL verification was unavailable because no `DATABASE_URL` is
  configured; no PostgreSQL coverage is claimed.
- [x] Applied common backend checks below.

### Out of scope

New columns, automatic conversion of finite series, UI, and conflict policy.

### Suitable for

AI agent.

## Task 02: Admit and validate open-ended break writes safely

**Domain:** Existing event service/conflicts. **Type:** service.
**Dependencies:** 01. **Migration:** None beyond 01.

### Context

Removing schema restrictions alone leaves service/conflict guards rejecting
the new shape. Break writes also have destructive confirmation semantics.

### What to do

- Extend `_create_recurrent_event`, `_update_recurrent_event`, and
  `_candidate_occurrences` to accept weekly open-ended breaks using the agreed
  one-year validation boundary.
- Keep bounded expansion, exclusion of the edited source, recurrence timezone
  snapshots, legacy mode, mutation serialization, and cancellation lifecycle.
- Preserve `validate_schedule_change`, `_break_confirmation_token`, the
  non-mutating 409 preview, and the atomic confirmed write. Restrict its
  affected set to generated candidates within the approved window.
- Verify management list/detail, day/week reads, and distant later writes
  against open breaks through the existing services; fix only coupled assumptions.

### Reuse

`open_ended_validation_cutoff`, `expand_recurrence`,
`load_raw_occurrences_for_validation`, `EventValidator.validate_all`, existing
displacement application, ownership/revision checks, and teacher schedule guard.

### Acceptance Criteria

- [ ] API POST/PUT round-trip weekly breaks with JSON null; finite and legacy
  nonweekly breaks retain their contracts.
- [ ] Preview changes no data; confirmation deletes/cancels only exact in-window
  lesson occurrences, and changed affected sets require renewed confirmation.
- [ ] Fixtures immediately before/at/after the exclusive anniversary prove
  the boundary; later existing lessons remain untouched and distant new
  lesson writes detect the open break.
- [ ] Leap-day, DST, cancellation, automatic-break tails, occurrence-budget
  failure, stale revision, and failed-write rollback retain explicit behavior.
- [ ] Existing lesson and finite-break tests pass; common backend checks pass.

### Out of scope

Rolling validation, background cancellation, infinite validation, or automatic
repair of previously booked out-of-window overlaps.

### Suitable for

AI agent.

## Task 03: Carry the new contract through the typed Telegram client

**Domain:** [Typed bot client](../../telegram/api.py). **Type:** bot.
**Dependencies:** 01, 02. **Migration:** None.

### Context

Even a successful backend response currently fails client model validation for
an open-ended break.

### What to do

- Extend `EventManagementDetail.validate_recurrence_shape` and the open-ended
  shape guards on `RecurrentEventCreatePayload`, `RecurrentEventUpdatePayload`,
  and `RecurrentEventWriteResult` to allow exactly weekly lessons/breaks.
- Preserve explicit-null update serialization, strict types, recurrence metadata
  requirements, and rejection of malformed one-off details.
- Test actual request/response JSON for create/update/detail, including
  displacement payload storage and reconstruction.

### Reuse

Existing models, `write_management_event`, response parsing, and
[client tests](../../tests/test_bot_client.py).

### Acceptance Criteria

- [ ] Open/finite lesson and break payloads/results/details round-trip;
  null nonweekly/vacation shapes remain rejected.
- [ ] Null is sent as null, not omitted accidentally or replaced with a date;
  preview tokens and revision headers are preserved.
- [ ] Common bot checks pass.

### Out of scope

New API endpoints, backend imports in the bot, or permissive fallback parsing.

### Suitable for

AI agent.

## Task 04: Apply accepted-time cleanup to breaks and vacations

**Domain:** Existing break/vacation input handlers. **Type:** bot.
**Dependencies:** None. **Migration:** None.

### Context

Lesson date/time cleanup works; break and vacation time handlers omit it.
Cleanup must follow successful field acceptance, not merely receipt of text.

### What to do

- Retain the incoming message in both `start_time_input` handlers. Parse and
  resolve the candidate local start through `resolve_local_interval` before
  committing the field, using the existing lesson probe pattern.
- Delete accepted break start-time, break end-time, and vacation start-time
  messages with `delete_accepted_input_message(..., "time")`.
- Break end-time cleanup follows successful positive interval resolution;
  vacation final interval validation remains authoritative at submit.
- Keep parse/resolution failures on the same prompt without cleanup or draft
  overwrite. Test inferred dates and both create/update entry points.

### Reuse

Existing cleanup helper, lesson resolution/acceptance pattern, `ShowMode.EDIT`,
`feedback_block`, and recording transport deletion-failure injection.

### Acceptance Criteria

- [ ] Each accepted date/time deletes only its exact message once; invalid or
  backend-rejected times are not deleted.
- [ ] Expected deletion failures log/show attention and retain the accepted
  draft; unrelated Telegram errors are not swallowed.
- [ ] No extra screen send or backend mutation is triggered by cleanup;
  tests assert message IDs and recorded calls, not just displayed text.
- [ ] Common bot checks pass.

### Out of scope

Global middleware deletion, duration-input cleanup, homework, and file handling.

### Suitable for

AI agent.

## Task 05: Make weekly break creation default-first

**Domain:** Existing break creation/review. **Type:** bot.
**Dependencies:** 03, 04. **Migration:** None.

### Context

The existing mandatory cutoff screen and finite-only render/payload code prevent
the lesson-like default path approved above.

### What to do

- Initialize weekly creation with no end. After a valid start/end interval,
  open review directly, showing **No end** and **Change series end**.
- Make cutoff editing optional, with **No end**, inferred-year confirmation,
  and **Back** restoration of the previously committed choice.
- Preserve finite cutoff choices through date/time changes; if the new start
  excludes every occurrence, show an actionable error and require correction
  before Save rather than silently clearing it.
- Make `_review_text`, `_build_payload`, `detail_data`, preview reconstruction,
  and recovery paths null-safe. Show the approved validation/displacement
  limitation, without presenting the one-year window as the series end.
- Invalidate stale interval/preview state only when its inputs change.

### Reuse

Lesson cutoff-editor transaction pattern, current break interval inputs,
`_resolve_cutoff_date`, `_send_write`, and typed client models.
Extract a small shared helper only if it removes real duplication; no generic
event-wizard framework.

### Acceptance Criteria

- [ ] Weekly date → start time → end time → review needs no end-date entry;
  POST contains `interval=7` and `interval_end=null`.
- [ ] Explicit finite end, clearing it, cancelling an edit, inferred dates,
  and changing start/end all preserve the intended committed values.
- [ ] Reviews/details render both shapes; invalid drafts cannot submit.
- [ ] Displacement preview paging, token revalidation, Cancel, and unknown
  create-outcome handling remain intact; common bot checks pass.

### Out of scope

Inventing a default break duration, changing same-local-date break input rules,
or replacing the existing break-list navigation.

### Suitable for

AI agent.

## Task 06: Make break updates focused and draft-preserving

**Domain:** Existing break management. **Type:** bot.
**Dependencies:** 05. **Migration:** None.

### Context

`begin_edit` currently throws away saved values, unlike lesson updates and
vacation review-based editing.

### What to do

- Seed an editable draft from freshly loaded detail: start/end, finite/null
  cutoff, actual interval, recurrence mode/timezone, and revision. Open review
  with focused date/start/end/cutoff actions.
- Store/restore only the selected field editor's working values on Back.
  Changing the start date should preserve the selected clock interval;
  changing a clock should not erase the cutoff or restart unrelated fields.
- Resolve edited wall times through the existing backend boundary; preserve
  stored instants/seconds on no-op edits, actual nonweekly intervals on legacy
  finite breaks, and recurrence timezone/mode.
- For nonweekly finite breaks, do not offer **No end**. Keep Save gated by
  valid interval/cutoff, revision, and recovery state.
- Preserve permanent-displacement and cancellation-reset warnings, and require
  a fresh displacement confirmation for any changed request/affected set.

### Reuse

`_detail`, lesson/vacation detail-to-draft patterns, `_build_payload`,
`_send_write`, `_refresh_detail`, and existing reload reconciliation.

### Acceptance Criteria

- [ ] One-time, finite weekly, open weekly, and legacy nonweekly break edits
  begin at a populated review; only edited values change in PUT.
- [ ] Date/time-only edits retain finite/null cutoff and actual recurrence
  interval; cutoff-only edits retain original interval endpoints.
- [ ] Back/Cancel, invalid cutoffs, timezone changes, stale revisions, changed
  previews, and unknown outcomes retain safe behavior.
- [ ] Existing displaced lessons are never restored; common bot checks pass.

### Out of scope

Changing series type/ownership or adding occurrence-move actions for breaks.

### Suitable for

AI agent.

## Task 07: Make vacation creation personal-first with optional assignee

**Domain:** Existing vacation creation/subject picker. **Type:** bot.
**Dependencies:** 04. **Migration:** None.

### Context

Teachers must currently choose a subject even for a personal vacation, while
lessons already provide a personal default and optional picker.

### What to do

- Start teacher vacation creation at the date prompt with personal subject
  explained; expose **Change assignee** there and on creation review.
- Reuse paginated student/personal selection, loaded only when requested.
  Preserve the caller, all entered interval values, and subject when backing
  out; an optional roster-read failure must not block personal creation.
- Keep student self-only creation and teacher-wide break ownership unchanged.
- Reset transient `date_field`, pending date/confirmation, picker return state,
  warnings, resolution, and write-recovery state when starting a genuinely new
  draft, without resetting a draft during an optional edit.

### Reuse

Lesson `_open_subject_picker` / return-state pattern and existing vacation
picker functions, authorized backend picker, and inclusive vacation interval.

### Acceptance Criteria

- [ ] Teacher Add vacation → date without a roster fetch; student flow remains
  self-only. Both still require explicit inclusive last vacation date.
- [ ] Optional assignee changes and Back preserve entered dates/time; failed
  picker reads leave personal creation available.
- [ ] A second creation after end-date editing cannot interpret the next start
  date as `end_date`; common bot checks pass.

### Out of scope

Changing existing vacation ownership during update, recurring vacations, or
inventing a default vacation length/end date.

### Suitable for

AI agent.

## Task 08: Make vacation field edits return to review reliably

**Domain:** Existing vacation date/time editors. **Type:** bot.
**Dependencies:** 07. **Migration:** None.

### Context

Saved updates already open review, but creation-review edits still follow the
initial collection path and most input screens have only a whole-form Cancel.

### What to do

- Distinguish initial required-field collection from focused review edits in
  `_begin_date_input`, `_accept_date`, and `start_time_input`.
- A focused accepted date/time edit returns directly to review if the draft is
  complete; it does not force time/end-date re-entry.
- Add field-editor Back with restoration, including pending inferred-year
  confirmation. Keep whole-form Cancel and existing exit destinations.
- Keep start/end ordering errors actionable without destructive draft changes;
  preserve inclusive end → following local midnight, backend DST resolution,
  and display-only vacation semantics.
- Invalidate stale resolution on temporal edits; no Save for an incomplete
  interval. Avoid backend writes from navigation.

### Reuse

`_local_interval`, `_date_input`, existing date ordering guards, shared input
cleanup, lesson editor return-state pattern, and current submit/reload logic.

### Acceptance Criteria

- [ ] Teacher/student create-review and saved-update field edits change only
  the selected field and return to review without replaying the wizard.
- [ ] Back from date/time/confirmation restores previous values; Cancel makes
  no write. Invalid ranges retain accepted draft fields.
- [ ] Same-day vacations, cross-year end dates, DST, hidden lessons, and
  unknown write outcomes preserve their contracts; common bot checks pass.

### Out of scope

All-day vacation redesign, defaults for mandatory range endpoints, or changes
to vacation visibility/conflict policy.

### Suitable for

AI agent.

## Task 09: Verify cross-flow parity and publish the implementation handoff

**Domain:** Existing integration tests and project documentation.
**Type:** bot/service regression verification. **Dependencies:** 02, 03, 04, 05,
06, 07, 08. **Migration:** None.

### Context

Each implementation task includes focused tests. Final verification must prove
consistent behavior across the inventory, not merely that the new happy path
renders successfully.

### What to do

- Extend existing test files rather than duplicating harnesses:
  [lesson creation](../../tests/test_bot_lesson_creation.py),
  [lesson management](../../tests/test_bot_lesson_management.py),
  [break management](../../tests/test_bot_break_management.py),
  [vacation management](../../tests/test_bot_vacation_management.py),
  [cross-flow](../../tests/test_bot_cross_flow.py),
  [runtime](../../tests/test_bot_runtime.py),
  [schema](../../tests/test_open_ended_recurrence_schema.py),
  [conflicts](../../tests/test_event_conflicts.py), and
  [recurrence edits](../../tests/test_recurrent_event_edits.py).
- Update helpers intentionally: a finite recurring break now uses the optional
  editor; do not keep tests passing by silently supplying a fake default date.
- Use simulated dispatcher updates and real isolated ASGI/SQLite backend
  writes to assert screen order, exact deleted message IDs, one active screen,
  request bodies/headers/counts, and persisted rows/cancellations.
- Cover teacher/student permissions, personal/student subject choices,
  finite/open weekly series, one-time forms, occurrence moves, invalid input,
  expected/unexpected deletion failures, Back/Cancel, and repeated entry.
- Test cross-flow draft isolation, timezone setting changes, `/start` and stale
  callbacks after restart, duplicate submission, committed-write refresh
  failure, unknown outcomes, and displacement previews after intervening writes.
- Update README schedule/bot/recurrence/deployment handoff and AGENTS Feature
  Status/boundaries only when implemented. Link this plan and record actual
  verification; preserve historical plans rather than rewriting old results.

### Reuse

[BotDriver / RecordingSession](../../tests/bot_support.py), existing dependency
overrides and real database fixtures, current per-user isolation, migration
test patterns, and the evidence matrix below.

### Acceptance Criteria

- [ ] Every inventory row has either regression evidence or an explicit
  unchanged/not-exposed boundary; no existing lesson UX regresses.
- [ ] Tests demonstrate no out-of-window displacement and no automatic later
  cancellation; user-visible limitations match the approved policy.
- [ ] Focused suites and common checks pass. Run the existing full tests only
  when integration failures or broader shared-service changes warrant it.
- [ ] Deployment notes require migration → compatible backend → compatible
  bot; downgrade refusal and old-client incompatibility are explicit.
- [ ] Plan completion records distinguish automated, manual, and unavailable
  PostgreSQL/real-Telegram checks.

### Out of scope

Claims of real Telegram/PostgreSQL verification without execution, a new test
framework, unrelated formatting, and implementation of deferred features.

### Suitable for

AI agent; human owner for optional real-chat acceptance.

## Verification matrix and common checks

| Requirement | Minimum evidence |
|---|---|
| Accepted input cleanup | Exact date/time IDs deleted once; invalid/resolution-rejected input not deleted; same active message |
| Default-first creation | No mandatory roster/cutoff step where optional; exact null payload and persisted null |
| Finite series preserved | Existing finite cutoff/interval survives unrelated edits; explicit clearing only |
| Field edit isolation | Before/after draft and PUT comparisons; Back/Cancel issue no mutation |
| Safe destructive confirmation | Non-mutating preview; token/affected-set recheck; exact in-window deletions/cancellations only |
| Bounded recurrence policy | Before/at/after anniversary, distant reads/writes, DST/leap day, legacy mode, budget errors |
| Recovery | Stale revision reload, no unknown-outcome replay, no duplicate writes, restart/stale-callback handling |
| Authorization and domain invariants | Students self-only, teacher-scoped breaks, private data isolation, derived breaks read-only, vacations non-destructive |

Run the smallest relevant pytest selectors together for each task using
`uv run pytest -q ...`; use existing tooling, no new dependencies by default.
Backend changes require `uv run ruff check --fix backend`,
`uv run ruff format backend`, and `uv run mypy backend`.
Bot changes require `uv run ruff check telegram tests`,
`uv run ruff format --check telegram tests`, and `uv run mypy telegram`.
Inspect formatter/linter changes and retain only in-scope edits.
Migration tests use disposable databases, never the developer's data.

## Out of scope for this release

- HTMX/frontend forms (scaffold only), new recurring-vacation screens,
  notifications, offline cache, or a scheduler.
- Default manual-break duration, default vacation duration, account transfer,
  new update-time assignee/duration capabilities, or a universal dialog engine.
- Unlimited conflict validation, rolling future cancellation, automatic
  out-of-window overlap repair, or a promise of forever conflict-free series.
- Unrelated dead-code removal and retroactive changes to finite stored events.

## Open questions and remaining limitations

- No blocking product decision remains for the two reported defects: weekly
  breaks default to genuine no end, using the explicitly approved one-year
  validation/displacement policy.
- Already-booked lessons outside that year can overlap an open-ended break.
  This is an accepted limitation, not an implementation omission; make it
  visible before saving and in management detail.
- Vacation endpoints remain mandatory and finite. Consistent UX means common
  acceptance, optional assignee, and focused-edit behavior, not pretending all
  event types have the same fields or recurrence capabilities.
- At implementation time, re-check the Alembic head and live lesson UX before
  editing; this document records the inspected baseline, not a frozen worktree.
