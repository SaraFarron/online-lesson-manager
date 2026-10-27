# Telegram home, event entry, and schedule refinements — Plan

## Goal

Make Telegram home and scheduling screens easier to read, remove unnecessary
input steps and chat messages, and support genuinely open-ended weekly lessons
without changing stored event times or authorization.

**Status:** Completed; Tasks 1–10 implemented, verified, and committed.

## Scope (README.md sections covered)

- [Bot interface](../../README.md#интерфейс-бота): edit-in-place navigation,
  accepted lesson-time message cleanup, and simpler date prompts.
- [Teacher settings](../../README.md#старт): readable teacher-home workdays
  and warnings only when there is an actual problem.
- [Schedule changes](../../README.md#изменение-расписания): human-readable
  durations in creation/editing and optional weekly-lesson end dates.
- [Schedule viewing](../../README.md#просмотр-расписания): capitalized weekday
  abbreviations, an upcoming marker, and shorter automatic-break labels.

This is a follow-up to the completed
[Telegram interface streamlining plan](./telegram-interface-streamlining.md).
It supersedes that plan's lowercase weekday presentation and date-only input
cleanup boundary. It also intentionally changes the current finite-only
weekly-lesson contract. Do not mark the earlier verification as evidence for
these new behaviors.

## Baseline and reusable building blocks

- [AGENTS.md Feature Status](../../AGENTS.md#feature-status-vs-product-spec)
  records implemented registration, settings, scheduling writes, automatic
  breaks, and live schedules. There is no dedicated “Known Issues / Tech Debt”
  section in the current file. These requests refine working features rather
  than introduce a new scheduling client.
- [Home `screen_data`](../../telegram/dialogs.py) displays raw ISO weekday
  lists and unconditional saved/available/notifications-inactive prose.
  `SessionView` in [the typed client](../../telegram/api.py) contains saved
  account/profile data, not a general booking-health or unsaved-draft flag.
- [Input helpers](../../telegram/inputs.py) own human date/time presentation.
  `_SHORT_WEEKDAY_NAMES` is lowercase. `DAY_NAMES` in
  [onboarding](../../telegram/onboarding.py) already provides English
  `Mon`–`Sun`; [settings](../../telegram/settings_dialogs.py) uses these names
  without grouping. Reuse/consolidate the vocabulary, not a second weekday map.
- [Lesson creation](../../telegram/lesson_dialogs.py) `start_time_input`
  resolves local time and then unconditionally opens the cutoff-date screen
  for weekly lessons. `submit_lesson` parses a mandatory `interval_end`.
  The review already has **Change series end**.
- [Lesson management](../../telegram/event_management_dialogs.py) assumes
  recurring lessons have a finite end, including an assertion and mandatory
  cutoff entry when editing a series. Null support must reach this path too.
- [Date cleanup](../../telegram/date_cleanup.py)
  `delete_accepted_date_message` already performs classified deletion,
  warning logging, and a user-visible notice for expected failures. Reuse
  this policy for lesson-time input, rather than installing global deletion.
- [Schedule formatting](../../telegram/schedule_format.py)
  `format_schedule_pages`, `_render_item`, and `_render_page` own status
  presentation, role-specific labels, escaping, and the 3,500 UTF-16-unit
  page budget. **Today** and **This week** share these helpers.
- Genuine “no end” is currently unsupported: `RecurrentEvent.interval_end`
  in [models](../../backend/events/models.py) is non-null; shared
  [schemas](../../backend/events/schemas.py), serializers, and bot payloads
  require a timestamp. [Occurrence expansion](../../backend/events/occurrences.py)
  requires a cutoff; [conflict validation](../../backend/events/conflicts.py)
  expands candidates through that cutoff and limits finite series to ten years.
- Reuse `RecurrenceSeries`, `expand_recurrence`,
  `load_raw_occurrences_for_validation` in
  [range service](../../backend/events/range_service.py),
  `prepare_lesson_write`, `validate_lesson_work_window`, and
  `validate_lesson_limits` in [booking policy](../../backend/events/booking_policy.py).
  Preserve [time utilities](../../backend/events/time_utils.py), cancellation
  identities, revision checks, and existing per-teacher mutation serialization.
  Do not introduce a parallel recurrence engine or bot-side scheduling checks.
- No new dead-code dependency was identified in the inspected paths. Do not
  wire unused generic validator/dependency files as a shortcut; extend the
  active shared service/conflict/booking-policy path.

## Approved presentation and behavior contract

### Home and durations

- Teacher home uses `Workdays: Mon - Wed, Sat, Sun` for `[1, 2, 3, 6, 7]`.
  Sort in ISO order; compress runs of three or more consecutive days and
  leave pairs separate, matching the supplied example. Do not wrap a range
  from Sunday to Monday. `[2, 3, 4, 5, 6, 7]` becomes `Tue - Sun`.
- Remove the healthy teacher-home paragraph beginning “Preferences saved”.
  Do not replace it with a different success banner or a notifications warning.
  Notification delivery remains unimplemented; removing the banner does not
  enable it.
- Retain actual error/recovery notices and show missing-profile/booking
  warnings only when supported by real state. Do not invent a “not saved”
  flag or claim all backend booking checks have run at home-render time.
  Existing settings draft/save failures remain explicit.
- Confirmed during planning: durations use hours and remaining minutes:
  `0 minutes`, `1 minute`, `45 minutes`, `1 hour`, `1 hour 1 minute`,
  `1 hour 30 minutes`, `2 hours`. Apply to event creation/editing duration
  summaries, duration-choice labels, and automatic-break duration prose.
  Minute-input instructions and API/storage units remain minutes.
  Onboarding, reminder settings, and general settings display are not a
  repository-wide duration rewrite.

### Event input and recurrence

- Delete successfully accepted lesson-time messages in creation and
  lesson-management input paths, just as accepted dates are deleted.
  Keep rejected input on validation/backend failure. Preserve edit-in-place
  navigation and the existing narrow deletion-failure policy.
- Remove timezone text from date-entry screens for lesson, break, and vacation
  creation/editing, including optional cutoff dates. Keep date grammar,
  next-year confirmation, and errors. Continue interpreting dates in the saved
  viewer timezone; retain timezone context on reviews and applicable schedules.
  This does not remove timezone configuration or DST resolution.
- New weekly lessons default to genuinely open-ended recurrence. Persist
  `interval_end = null`, not a distant date, sentinel, or finite date hidden
  behind “No end”. After accepted start time, go directly to review.
- Review clearly identifies the weekly recurrence and **No end**. Existing
  **Change series end** opens the optional inclusive date editor, with an
  explicit **No end** action to clear a previously chosen cutoff. A Back action
  returns without discarding the previous selection. Preserve explicit
  finite ends through unrelated edits; reject a cutoff before the first lesson.
- Existing finite lessons remain finite; do not convert stored series.
  Preserve their ten-year maximum. Open-ended writes are scoped to weekly
  lessons, not recurring breaks or vacations.
- Confirmed during planning: open-ended lesson writes validate a finite
  **one-calendar-year window from the submitted series start**. This window
  is never stored as the series end. Use the snapshotted recurrence timezone
  for the anniversary, clamp February 29 to February 28 in the following
  year, and preserve the existing half-open occurrence-start cutoff convention.
  Include complete lesson/automatic-break tails and full local days needed
  for daily/consecutive checks.
- This is an intentional validation-policy change: an open-ended write does
  not promise conflict/limit validation of all occurrences beyond that year.
  Future writes must load existing open-ended series in their own relevant
  validation dates, even decades after the existing series start. No scheduler
  or automatic extension job is implied. Reads/occurrence actions are bounded
  by their requested ranges, not by the original write-validation window.
  Editing a historical anchor still uses its submitted start; the policy is
  not silently re-anchored to today.

### Schedule presentation

- Shared short weekday labels become `Mon`, `Tue`, `Wed`, `Thu`, `Fri`, `Sat`,
  `Sun`, including week headings and cross-midnight endpoint labels.
- Confirmed during planning: replace `[UPCOMING]` with **⏳**. Keep the existing
  past/ongoing distinction; changing those markers was not requested.
  Backend `past` / `ongoing` / `upcoming` values do not change.
- Remove `(read-only)` from schedule automatic-break labels for both roles,
  keeping `Automatic break` versus `Teacher's automatic break`. The
  reservations remain derived and non-editable; this is not an authorization
  change. Unrelated management explanations are outside this copy change.
- Apply shared schedule presentation to Today as well as This week so the
  same event does not acquire different labels between periods. Preserve NOW,
  empty-day behavior, timezone footers, DST distinctions, and overflow controls.

## Task 1 — Define nullable weekly-lesson cutoff storage and schemas

**Domain:** Existing shared events and lesson schemas.
**Type:** schema/migration.
**Dependencies:** None.

### Context and what to do

The [schedule-change contract](../../README.md#изменение-расписания) is
implemented for finite series. Add the storage/schema foundation for the
approved open-ended extension in [models](../../backend/events/models.py),
[shared schemas](../../backend/events/schemas.py),
[lesson schemas](../../backend/lessons/schemas.py), and
[management schemas](../../backend/events/management_schemas.py).

- Add a static, reversible `YYYY-MM-DD_open_ended_weekly_lessons.py` Alembic
  migration under [versions](../../alembic/versions/). Make the cutoff nullable
  without changing existing values. Keep finite `cutoff > start` checks and
  enforce that null is permitted only for weekly lessons, not other event types.
- Distinguish an open-ended recurrent detail from a one-off detail using
  `is_recurrent`, interval, and recurrence mode, not cutoff presence alone.
- Preserve offset-bearing UTC serialization for finite timestamps; serialize
  an absent cutoff as JSON null. Define omitted/null create semantics and
  explicit update semantics without changing cancellation replacement rules.
- Downgrade must refuse while open-ended rows exist, before making changes.
  Never invent cutoffs or delete rows to make downgrade succeed.
- Do not enable null writes in the active router/service path until Tasks 2–3
  support them; sequence the backend release atomically.

### Reuse

`UtcTimestamp`, existing recurrence constraints, domain schema specialization,
and refusal-before-mutation migration patterns already in the repository.

### Acceptance criteria

- [x] Finite values round-trip unchanged; schema tests cover null, omitted
  fields, invalid finite cutoffs, and null rejection for nonweekly/nonlesson writes.
- [x] Fresh/upgrade and finite-only downgrade tests pass on disposable SQLite
  and, when available, PostgreSQL; refused downgrade preserves open-ended data.
- [x] No destructive backfill or synthetic end date is introduced.

### Out of scope / suitable for

No new settings or unrelated model cleanup. Suitable for an AI agent or human.

## Task 2 — Expand and read open-ended recurrences in bounded ranges

**Domain:** Existing events occurrence/range/management reads.
**Type:** service.
**Dependencies:** Task 1.

### Context and what to do

Extend `RecurrenceSeries` and `expand_recurrence` in
[occurrences](../../backend/events/occurrences.py) to allow no series cutoff
while always requiring finite query bounds. Wire
[range service](../../backend/events/range_service.py) and
[management reads](../../backend/events/management.py), including revision
serialization and exact occurrence inspection, to handle null safely.

### Reuse

Existing indexed range search, UTC-step and teacher-local expansion,
cancellation application, automatic-break derivation, viewer authorization,
and schedule/homework consumers of the range service.

### Acceptance criteria

- [x] Day/week and exact-occurrence reads work more than one and ten years
  after an open-ended anchor; finite series still stop at their cutoff.
- [x] DST folds/gaps, cancellation identities, vacation visibility, and
  automatic breaks retain existing behavior.
- [x] Null cutoff serialization/revision hashing is deterministic; two
  otherwise identical finite/open-ended revisions are distinguishable.
- [x] Existing occurrence/query limits still fail explicitly; no unbounded
  iteration or expansion from the anchor merely to reach a distant query.

### Out of scope / suitable for

No cached/materialized future schedule or new API endpoint. No migration.
Suitable for an AI agent or human.

## Task 3 — Validate and mutate genuinely open-ended weekly lessons

**Domain:** Existing events conflict, booking-policy, and mutation services.
**Type:** service.
**Dependencies:** Tasks 1–2.

### Context and what to do

Update [conflicts](../../backend/events/conflicts.py) candidate generation to
use the approved calendar-year validation bound only for open-ended weekly
lessons. Update [event service](../../backend/events/service.py) create/update,
cancellation validation, exact-occurrence cancel/move, and recurrence-field
comparisons to persist and handle null rather than converting it to a timestamp.

### Reuse

`_candidate_occurrences`, `_validate_recurrence_horizon`,
`load_raw_occurrences_for_validation`, `prepare_lesson_write`,
`validate_lesson_work_window`, `validate_lesson_limits`, and existing
transaction/teacher-lock and break-displacement paths. Factor the finite
validation-bound calculation at the shared boundary, not in each router.

### Acceptance criteria

- [x] Open-ended create/update stores null; finite↔open transitions work and
  apply the existing cancellation-preservation/reset rules.
- [x] Work hours, duration permissions, self/teacher conflicts, daily and
  consecutive limits, and automatic-break tails are checked inside the
  exact approved year; leap-day/DST anniversary boundaries are tested.
- [x] Tests explicitly distinguish an occurrence just inside that window from
  one at/outside its boundary; validation bounds never become storage cutoffs.
- [x] Later one-off/finite writes detect existing open-ended lessons and
  automatic breaks outside their original validation year.
- [x] Exact cancel/move and confirmed finite-break displacement work against
  distant open-ended occurrences; rollback, revision, and serialization behavior
  remains unchanged.
- [x] Finite-series ten-year enforcement and nonlesson finite requirements
  remain intact. Resource-limit failures are explicit, not partial validation.

### Out of scope / suitable for

No infinite conflict guarantee, recurring-break scope expansion, or background
revalidation worker. No additional migration. Suitable for an AI agent or human.

## Task 4 — Carry nullable cutoffs through the typed bot client and management

**Domain:** Existing Telegram API client and lesson management.
**Type:** bot.
**Dependencies:** Tasks 1–3.

### Context and what to do

Update recurrent payload/result/detail models in
[the typed client](../../telegram/api.py) and cutoff-dependent
[lesson management](../../telegram/event_management_dialogs.py).
Remove finite-only assertions/conversions, render **No end**, and let a series
keep or change its finite/open-ended choice while editing. Keep optional
cutoff entry separate from mandatory date/time editing. Ensure
[break dialogs](../../telegram/break_dialogs.py) still send finite break
cutoffs despite using shared client models.

### Reuse

Existing `is_recurrent` discriminant, management review/edit actions,
date parsing, local-boundary resolution, and unknown-write-outcome recovery.

### Acceptance criteria

- [x] Typed client accepts null lesson responses and preserves null in writes;
  one-off shape validation still rejects recurrence metadata.
- [x] Open-ended series list/detail/edit/inspect/cancel/move flows work for
  both authorized roles; finite edits retain their cutoff unless changed.
- [x] Optional cutoff date editing can set/clear an end and cancel without
  losing the prior choice. Break creation/editing remains finite.
- [x] No management path formats or parses null as a timestamp.

### Out of scope / suitable for

No account permissions or callback identity changes. No migration.
Suitable for an AI agent or human.

## Task 5 — Skip mandatory weekly-lesson end entry

**Domain:** Existing Telegram lesson creation.
**Type:** bot.
**Dependencies:** Tasks 3–4.

### Context and what to do

In [lesson dialogs](../../telegram/lesson_dialogs.py), change
`start_time_input` to open review directly for both lesson kinds and make
`submit_lesson` send null for an open-ended weekly lesson. Reuse the review's
**Change series end** action for optional editing; provide **No end** and
non-destructive Back behavior. Preserve a chosen cutoff through unrelated edits,
and require correction if a changed start makes that cutoff invalid.

### Reuse

`lesson_data`, `edit_cutoff_date`, `_resolve_cutoff_date`, date confirmation,
existing default-duration omission, and review submission/reload guards.

### Acceptance criteria

- [x] Teacher personal/student-assigned and student-self weekly creation goes
  date → time → review with **No end**, no mandatory cutoff screen.
- [x] Optional finite cutoff round-trips with inclusive-local-date semantics;
  clearing it persists null. One-time creation has no series-end action.
- [x] Date/time/duration/assignee edits preserve the intended cutoff choice.
- [x] Real-backend tests verify stored null rather than a hidden future date.

### Out of scope / suitable for

No equivalent default change for weekly breaks. No migration.
Suitable for an AI agent or human.

## Task 6 — Format teacher workdays, event durations, and healthy home copy

**Domain:** Existing Telegram presentation helpers and screens.
**Type:** bot.
**Dependencies:** None.

### Context and what to do

Add focused shared weekday-range and duration formatting helpers in
[inputs](../../telegram/inputs.py), reusing the existing English weekday
vocabulary. Wire teacher [home](../../telegram/dialogs.py) to grouped workdays
and remove its unconditional availability/status paragraph. Wire event
creation/editing duration and automatic-break labels in
[lesson creation](../../telegram/lesson_dialogs.py),
[lesson management](../../telegram/event_management_dialogs.py), and
[break dialogs](../../telegram/break_dialogs.py) to the same duration helper.

### Reuse

Existing `SessionView`, HTML escaping, saved-profile data, error/recovery
surfaces, and numeric duration values. Do not infer unsaved preferences from
the absence of a success message.

### Acceptance criteria

- [x] Workday tests cover a single day, a pair, a long run, separated runs,
  the supplied example, full week, and unordered valid input.
- [x] Duration tests assert every example in the contract, including singular
  units and zero. Minute-based input and payloads are unchanged.
- [x] Healthy teacher home has no raw ISO list or removed paragraph; actual
  missing-profile and backend/save failures remain explicit.
- [x] Existing invitation, homework notices, and student-home behavior remain
  unchanged; event creation/editing uses consistent duration labels.

### Out of scope / suitable for

No global rewrite of settings/onboarding/reminder units. No migration.
Suitable for an AI agent or human.

## Task 7 — Delete accepted lesson-time input with the date-cleanup policy

**Domain:** Existing Telegram cleanup and lesson input handlers.
**Type:** bot.
**Dependencies:** None; integrate with Tasks 4–5 before final verification.

### Context and what to do

Generalize [date cleanup](../../telegram/date_cleanup.py) only as necessary to
support date/time-specific notices without duplication. Wire successful
`start_time_input` in [creation](../../telegram/lesson_dialogs.py) and
`time_input` in [management](../../telegram/event_management_dialogs.py),
including edit/move and exact-occurrence selection paths. Delete only after
the handler accepts the resolved value and any required backend read succeeds.

### Reuse

Existing expected-delete-failure classification, logging, `ShowMode.EDIT`,
and `BotDriver.deleted_message_ids` in [test support](../../tests/bot_support.py).

### Acceptance criteria

- [x] Accepted lesson times are deleted once without a new bot screen message;
  existing accepted-date cleanup still works across all event dialogs.
- [x] Invalid clocks, DST resolution rejection, and backend failures do not
  delete the user's input.
- [x] Expected Telegram deletion failures log and show an accurate date/time
  notice without losing the accepted draft; unexpected errors propagate.
- [x] Test edit/move/inspect paths as well as creation, not just parsing.

### Out of scope / suitable for

No automatic deletion of commands, duration input, break/vacation times,
homework text/files, or all messages through middleware. No migration.
Suitable for an AI agent or human.

## Task 8 — Remove timezone clutter from event date prompts

**Domain:** Existing Telegram event date-entry windows.
**Type:** bot.
**Dependencies:** None; integrate with Tasks 4–5.

### Context and what to do

Remove visible timezone hints from date-entry getters/windows in
[lessons](../../telegram/lesson_dialogs.py),
[lesson management](../../telegram/event_management_dialogs.py),
[breaks](../../telegram/break_dialogs.py), and
[vacations](../../telegram/vacation_dialogs.py), including end/cutoff dates.
Do not delete timezone data used for parsing or hide actionable errors.

### Reuse

Existing date grammar, `parse_schedule_date`, inferred-year confirmation,
and local-time resolution.

### Acceptance criteria

- [x] Creation/editing date prompts show no IANA timezone label or “date in
  timezone” sentence; inferred-year confirmations still work.
- [x] Different viewer timezones still produce the same correct local-date
  interpretations and inclusive end boundaries.
- [x] Reviews and applicable schedule pages retain their timezone context.

### Out of scope / suitable for

No time-input prompt rewrite or timezone selection changes. No migration.
Suitable for an AI agent or human.

## Task 9 — Capitalize schedule weekdays and simplify event markers

**Domain:** Existing Telegram schedule formatting.
**Type:** bot.
**Dependencies:** None.

### Context and what to do

Capitalize shared short weekdays in [inputs](../../telegram/inputs.py).
In [schedule formatting](../../telegram/schedule_format.py), replace only the
upcoming status display with **⏳** and remove `(read-only)` from both
automatic-break labels. Update plain and HTML rendering together so the
existing UTF-16 budget accounts for the actual marker.

### Reuse

`format_compact_schedule_date`, `_render_item`, `_AUTOMATIC_BREAK_LABELS`,
whole-row packing, and `format_schedule_pages`. Preserve backend status values.

### Acceptance criteria

- [x] Both roles' Today/week screens use capitalized weekdays and **⏳**,
  never `[UPCOMING]` or `(read-only)` in schedule rows.
- [x] Past/ongoing distinctions, NOW ordering, teacher-only student labels,
  HTML safety, empty views, DST/cross-midnight labels, and navigation survive.
- [x] Dense schedule tests count source rows/identities rather than relying
  on the removed status token; every event survives page packing.
- [x] Every rendered page stays within 3,500 UTF-16 units, including emoji
  and astral characters in names. Derived breaks gain no editing actions.

### Out of scope / suitable for

No new status fields, legend screen, cache, or authorization change.
No migration. Suitable for an AI agent or human.

## Task 10 — Verify the integrated contract and update the handoff

**Domain:** Directly related tests and project documentation.
**Type:** bot/service verification and documentation.
**Dependencies:** Tasks 1–9.

### Context and what to do

Update current presentation/recurrence contracts in
[README.md](../../README.md) and
[AGENTS.md](../../AGENTS.md#feature-status-vs-product-spec); link this plan and
record actual verification here. Clearly document null cutoff meaning,
finite-break compatibility, one-year validation limitations, migration deployment
order, and downgrade refusal. Preserve historical execution records.

### Reuse and validation

Use the real ASGI backend, isolated database, and recording Telegram transport:

- Backend: extend existing recurrence expansion/range, recurrent edits,
  occurrence actions, conflicts, lesson-policy, automatic-break, authorization,
  and migration tests. Run affected selectors together first.
- Bot: extend existing lesson creation/management, break/vacation management,
  schedule format/presentation/dialog/integration, and home/flow coverage.
  Reuse [BotDriver](../../tests/bot_support.py); do not introduce a second harness.
- Run `uv run ruff check backend telegram tests`,
  `uv run ruff format --check backend telegram tests`, and
  `uv run mypy backend telegram`. Include migration formatting/lint checks
  consistent with the existing migration test workflow.
- After targeted checks pass, run `uv run pytest -q tests` once for this
  cross-cutting recurrence/client change. PostgreSQL-specific checks require
  a disposable configured database; record unavailable checks rather than
  claiming they passed.

### Acceptance criteria

- [x] Tests verify exact visible copy, navigation, deletion calls, stored
  nulls, bounded validation, distant reads/writes, and unchanged authorization.
- [x] Returning home, settings failures, date/time editing, optional finite
  end changes, restart recovery, and post-write schedule rendering compose.
- [x] Documentation does not imply infinite booking validation or enabled
  notifications. Plan status changes only after recorded verification.
- [x] No unrelated feature-status cleanup or historical test claims.

### Out of scope / suitable for

No new tooling or production database operations. Suitable for an AI agent
or human; real Telegram visual smoke checks can be performed by the owner.

## Out of scope for this release

- Notification delivery, HTMX, offline cache, new preferences/endpoints,
  or scheduling-rule redesign beyond the explicitly approved open-ended policy.
- Open-ended recurring breaks/vacations, conversion of existing finite series,
  or a background recurrence-validation worker.
- Global input deletion, new bot permissions, or removal of actual failure warnings.

## Open questions and risks

- No blocking product questions remain from this planning conversation.
  Durations, upcoming marker, genuinely open-ended storage, and the one-year
  write-validation window were explicitly confirmed.
- One-year validation permits undiscovered conflicts outside that window,
  including with already stored far-future events. A write editing an old anchor
  can validate a wholly historical year. These are consequences of the approved
  start-based policy, not reasons to substitute a hidden cutoff or claim full
  coverage. If unacceptable during implementation, stop for a policy change.
- Deploy backend migration/service support before enabling the bot flow.
  Old clients may reject nullable cutoff responses; deploy the typed client
  and management support together with creation changes.
- Nullability crosses schema inheritance, occurrence lookup, revision hashing,
  cancellation reset, and automatic-break validation. “Skip the screen” alone
  cannot satisfy this request.

## Execution record

- Tasks 1–9 were implemented, verified, and committed separately:
  `4b234d0`, `a91126c`, `6a3b8dc`, `ff63d55`, `8f3658c`, `f516c9a`,
  `0165e9c`, `a8a4165`, and `6d896ce`.
- Task 10 updated the README and agent handoff, corrected the obsolete
  open-ended-write API expectation, and recorded the integrated verification.
- The affected backend/bot regression selection passed: 297 passed, 5 skipped.
- The first full-suite run had two bot homework-flow failures
  (`test_assignment_can_link_exact_upcoming_lesson_occurrence` and
  `test_teacher_upload_recovers_from_interrupted_and_unavailable_storage`).
  Both tests passed in isolation, and the subsequent full-suite run passed:
  429 passed, 5 skipped, with 2,686 warnings.
- `ruff check backend telegram tests`, bot and migration formatting/lint,
  and `mypy telegram` passed. Full `ruff format --check backend telegram
  tests` remains blocked by the pre-existing formatting issue in
  `backend/notifications/models.py`. `mypy backend telegram` reports existing
  errors in `backend/auth/config.py`, `backend/database.py`, and
  `backend/events/dependencies.py`; those files are outside this task's
  changes.
- PostgreSQL-specific migration verification was unavailable because no
  disposable configured PostgreSQL database was established.
