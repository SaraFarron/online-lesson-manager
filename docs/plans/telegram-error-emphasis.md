# Noticeable Telegram errors — Plan

## Goal

Make errors immediately noticeable on every bot screen without losing the
current draft, obscuring recovery instructions, or sending extra chat messages.

**Status:** Complete; Tasks 1–6 verified.

## Scope (README.md sections covered)

- [Bot interface](../../README.md#интерфейс-бота): edit-in-place navigation,
  visible validation failures, and explicit recovery.
- [Existing flows](../../README.md#implemented-telegram-flows-and-limits):
  registration, settings, scheduling, and homework error presentation.
- Applies to all bot screens with errors, as confirmed during planning.
  Backend admission rules and API error contracts remain unchanged.

## Baseline and reusable building blocks

- [AGENTS.md Feature Status](../../AGENTS.md#feature-status-vs-product-spec)
  records working scheduling, settings, onboarding, and homework flows.
  There is no dedicated Known Issues / Tech Debt section. Do not treat
  stale feature descriptions elsewhere as a reason to rebuild these flows.
- [The API client](../../telegram/api.py) already maps scheduling policy
  failures to safe user-facing copy through `POLICY_ERROR_MESSAGES` and
  `BackendClient._backend_error`. The observed HTTP 422 correctly reports
  a lesson ending after the teacher's work hours. Presentation, not backend
  validation, is the problem.
- [Lesson creation](../../telegram/lesson_dialogs.py) puts `{error}` below
  the review. Settings, event management, onboarding, and homework commonly
  follow the same pattern. Their getters already escape error strings.
- [Schedule dialogs](../../telegram/schedule_dialogs.py) compose unavailable
  text directly rather than using the same `{error}` window pattern.
  [Home](../../telegram/dialogs.py) renders `{home}` without an error slot.
  A solution must cover these exceptions, not just replace common templates.
- HTML is the bot's configured parse mode in
  [the entrypoint](../../telegram/__main__.py). Use supported Telegram HTML,
  not Markdown, ANSI color codes, CSS, or images.
- [Runtime](../../telegram/runtime.py) already preserves edit-in-place input
  and shows alerts for expired/orphan callbacks. Keep that recovery behavior.
- `error` is not always a failed operation:
  [date cleanup](../../telegram/date_cleanup.py) stores accepted-input
  cleanup warnings there; `submit_lesson` stores `BackendOutcomeUnknown`
  there. Homework and management flows also have unknown-outcome and
  committed-write/read-failure notices. Audit those assignments before
  assigning a severity or heading.
- Reuse [BotDriver and RecordingSession](../../tests/bot_support.py), existing
  dialog tests, and the real ASGI backend. No new UI framework is needed.
- No dead backend validator/dependency needs to be activated for this work.
  Do not change `backend/events/` or duplicate its admission logic in the bot.
- Local debugging edits were present in the API client and lesson dialog
  during planning. Preserve them; do not remove or commit them incidentally.

## Recommended presentation contract

### Persistent, first-in-message block

Telegram cannot color message text. Use position, a recognizable icon, a bold
heading, and whitespace instead:

```text
❌ Error
That time is after the teacher's working hours.

Review lesson for Alice
...
```

- Render the heading as `<b>❌ Error</b>` and the reason as escaped plain
  text. The error block is the first visible text, before titles, summaries,
  notices, lists, and input instructions.
- Separate the reason from normal content with exactly one blank line.
  Preserve multiline reasons. Do not duplicate the reason at the bottom.
- Render no heading, icon, or extra leading whitespace when no error exists.
  Keep the healthy-screen body unchanged.
- Use one shared presentation helper or text widget, following the simplest
  existing aiogram-dialog composition pattern. Keep raw error text in dialog
  state; escape it exactly once at the presentation boundary. Do not infer
  severity from English substring matching.
- Keep current safe API-to-user messages. Do not show HTTP status codes,
  raw response JSON, tokens, tracebacks, or new numeric work-hour claims.
- Preserve existing correction/recovery buttons and the user's draft.
  In the lesson example, **Change time** remains available. Do not
  automatically open Settings, alter working hours, or resubmit the write.
- Reuse existing error clearing on successful input and recovery. Fix stale
  error retention only where directly exposed by this presentation change;
  navigating or retrying must not silently erase an unresolved outcome.

### Warnings are not errors

- Use `<b>⚠️ Attention</b>` with the same top-block layout for an unknown
  outcome, a committed write followed by a failed reload, or accepted input
  that Telegram could not delete. Preserve the exact uncertainty/recovery
  message and existing retry guards.
- Classify these cases using their exception branches or explicit state
  flags, not by guessing from text. If mixed error/warning content currently
  shares one string, separate only the state needed to render it accurately.
  A true error precedes a warning if both are present; show each once.
- Existing routine success notices, draft summaries, destructive-action
  confirmations, and mandatory safety instructions remain normal screen
  content, not red error blocks. Do not restyle every `notice` as a warning.
- Missing-profile warnings on home should use the attention treatment while
  retaining the account/profile information that is available.

### Interaction and size constraints

- No new chat messages, callback pop-ups, sounds, or forced acknowledgements
  for ordinary errors. A persistent block works for both callbacks and text
  input; a disappearing toast alone would not.
- Retain existing expired-screen alerts and replacement-message handling;
  do not intercept failures globally or suppress unexpected exceptions.
- Keep complete rendered messages within Telegram's 4,096-unit UTF-16 text
  limit, including the block and existing content. Preserve the schedule's
  stricter 3,500-unit page budget. Do not silently truncate errors, homework,
  schedule rows, or recovery text to make them fit.
- Error layout must not change authorization, backend request shapes,
  unknown-outcome reconciliation, input deletion rules, or write counts.

## Task 1 — Add a shared, safely escaped feedback block

**Domain:** Existing Telegram presentation.
**Type:** bot.
**Dependencies:** None.

### Context and what to do

Implement a small shared error/attention presentation helper or widget in
the Telegram package. Before editing, inventory error-bearing getters/windows
and exceptional warning branches in the modules named below. Use the HTML
contract above; integrate at render time, not inside `BackendClient`.

### Reuse

Existing `html.escape`, aiogram-dialog `Format`/conditional text composition,
and recording transport. Leave safe error mapping in the API client.

### Acceptance Criteria

- [x] Empty errors render nothing; errors and warnings use their exact heading,
  escaped reason, and blank-line separation.
- [x] Tests cover multiline text, `<`, `>`, `&`, quotes, emoji, and repeated
  rendering without double escaping or nested duplicate blocks.
- [x] Feedback never changes the raw stored reason or derives severity from
  message wording. A combined error/warning has deterministic ordering.
- [x] No backend change, dependency, migration, or global exception handler.

### Out of scope / Suitable for

No general notification/severity framework. Suitable for an AI agent or human.

## Task 2 — Highlight lesson creation and management failures

**Domain:** Existing lesson creation and lesson management.
**Type:** bot.
**Dependencies:** Task 1.

### Context and what to do

Wire all error-bearing windows/getters in
[lesson creation](../../telegram/lesson_dialogs.py) and
[management](../../telegram/event_management_dialogs.py).
Audit `submit_lesson`, edit/cancel/move/inspect handlers, and unavailable screens.
Present shared [date/time cleanup](../../telegram/date_cleanup.py) failures
as attention without changing when deletion is attempted or accepted input
is retained.

### Reuse

`lesson_data`, management getters, `submit_lesson`, existing `BackendError`,
`BackendOutcomeUnknown`, stale-revision/reload flags, and cleanup policy.

### Acceptance Criteria

- [x] A real-backend weekly lesson ending after saved work hours shows the
  mapped reason in the very first block, above the review, exactly once.
- [x] The failed create persists no lesson, retains the draft and correction
  controls, and edits the existing message. Correcting and submitting succeeds
  without stale error text or an extra write.
- [x] Invalid date/time/duration, conflicts, failed inspection, and unavailable
  reads use the same prominent treatment across creation and management.
- [x] Unknown write outcomes and cleanup failures use attention, never imply
  a definite rejection, and preserve existing reconciliation/retry guards.
- [x] One-off/weekly, teacher/student, optional cutoff, edit/move/cancel,
  accepted-input deletion, and post-write navigation behavior remain intact.

### Out of scope / Suitable for

No work-hour bypass or extra API calls. No migration. Suitable for an AI agent
or human.

## Task 3 — Highlight break, vacation, and schedule failures

**Domain:** Existing other scheduling dialogs.
**Type:** bot.
**Dependencies:** Tasks 1–2 (shared cleanup warning integration).

### Context and what to do

Wire [breaks](../../telegram/break_dialogs.py),
[vacations](../../telegram/vacation_dialogs.py), and
[schedule dialogs](../../telegram/schedule_dialogs.py), including their list,
detail, input, review, confirmation, and unavailable screens.
Keep error blocks separate from displacement and destructive-action warnings.

### Reuse

Existing getters, `schedule_data`, `_load_schedule`, shared cleanup,
break-displacement confirmation, and schedule page formatting.

### Acceptance Criteria

- [x] Failed scheduling inputs/writes and unavailable day/week views start
  with one shared feedback block and retain recovery controls.
- [x] Break displacement previews/confirmations, vacation visibility, and
  successful-write/failed-reload semantics remain unchanged.
- [x] Healthy schedule pages and navigation remain unchanged and retain their
  3,500-unit UTF-16 budget. Error views also fit the message limit.
- [x] No lost schedule rows, changed write counts, or new chat sends.

### Out of scope / Suitable for

No schedule pagination redesign or backend policy change. No migration.
Suitable for an AI agent or human.

## Task 4 — Highlight onboarding, home, and settings failures

**Domain:** Existing account, registration, and settings dialogs.
**Type:** bot.
**Dependencies:** Task 1.

### Context and what to do

Wire [onboarding](../../telegram/onboarding.py),
[home/account dialogs](../../telegram/dialogs.py), and
[settings](../../telegram/settings_dialogs.py).
Cover error slots that are currently absent from home, field-specific getters,
and unavailable screens. Keep missing-profile warnings distinct from errors.

### Reuse

`wizard_data`, `screen_data`, settings getters and merged-settings validation,
existing error clearing, and runtime `/start` recovery.

### Acceptance Criteria

- [x] Invalid invitation/timezone/registration input and rejected settings
  saves appear first; drafts, Back/Cancel, and field choices are preserved.
- [x] Backend login/settings outages and reload failures remain explicit;
  successful-save/failed-reload states do not look like an unsaved rejection.
- [x] Home errors are visible; missing-profile warnings use attention.
  Healthy home gains no warning or success banner.
- [x] Success notices, invitations, notification-preference semantics, and
  expired-screen callback alerts retain their existing behavior.

### Out of scope / Suitable for

No auth, onboarding copy overhaul, or enabled notification delivery.
No migration. Suitable for an AI agent or human.

## Task 5 — Highlight teacher and student homework failures

**Domain:** Existing homework Telegram dialogs.
**Type:** bot.
**Dependencies:** Task 1.

### Context and what to do

Wire [teacher creation](../../telegram/homework_teacher_dialogs.py),
[student browsing/submissions](../../telegram/homework_student_dialogs.py),
and [teacher review/management](../../telegram/homework_teacher_management_dialogs.py).
Include picker, text/file input, review, detail, delete confirmation, and
unavailable screens.

Owner-approved implementation adjustment: use inline pagination within the
existing edit-in-place screens for long assignment/solution text. Preserve all
text and keep feedback and recovery controls visible; do not truncate or send
additional messages.

### Reuse

Existing getters, typed homework errors, pending-upload state, streamed
downloads, versioned submission guards, and unknown-outcome reconciliation.

### Acceptance Criteria

- [x] Validation, upload/download, authorization, stale-version, and backend
  failures are prominent and escaped; existing correction actions remain.
- [x] Unknown publish/submit/delete/upload outcomes use attention and retain
  safe retry/reconciliation behavior without duplicate files or assignments.
- [x] Error blocks do not conceal submission version, attachment retry
  controls, or deletion confirmations.
- [x] Long existing text/attachment views plus feedback fit the message
  limit; no silent truncation or new document/message sends for errors.
- [x] Successful flows clear resolved errors and preserve routine notices.

### Out of scope / Suitable for

No new storage/API behavior, backend error exposure, or homework feature.
No migration. Suitable for an AI agent or human.

## Task 6 — Verify cross-flow consistency and document the contract

**Domain:** Directly related tests and handoff documentation.
**Type:** bot verification/documentation.
**Dependencies:** Tasks 2–5.

### Context and what to do

Verify every bot dialog builder's error-bearing surfaces, including direct
string composition and errors stored outside a generic `error` key.
Update README bot behavior and the relevant AGENTS handoff only after
verification. Record actual execution and any unavailable checks here.

### Reuse

Existing simulated updates, real ASGI/SQLite backend, `BotDriver`,
`RecordingSession.calls`, and current flow/integration tests.

### Acceptance Criteria

- [x] Tests check exact first-block placement, HTML safety, no duplicated
  reason, no empty banner, and error/attention distinctions.
- [x] Cover the reported work-hours rejection end to end, correction,
  repeated rejection, recovery, unavailable state, and unknown write outcome.
- [x] Recording transport confirms edits rather than extra sends, preserved
  message identity/buttons, no new ordinary-error alerts, and unchanged
  request counts/authorization.
- [x] Check UTF-16 length of complete rendered boundary cases, not only
  helper output. Existing schedule packing remains lossless.
- [x] Run affected test selectors together, then `uv run pytest -q tests`.
  Run `uv run ruff check telegram tests`,
  `uv run ruff format --check telegram tests`, and `uv run mypy telegram`.
- [x] No backend/schema changes means no migration or backend lint sweep.
  Preserve user debugging edits and report pre-existing blockers separately.
- [x] Record simulated checks as simulated. A real Telegram visual smoke
  check on desktop/mobile is optional and requires owner access; do not claim
  it occurred merely because recording-transport tests passed.

### Out of scope / Suitable for

No unrelated documentation/status cleanup or production data operations.
Suitable for an AI agent or human; owner may perform the visual smoke check.

## Out of scope for this release

- Callback alerts for ordinary validation failures, additional chat messages,
  colored text, new error-dismiss buttons, or automatic navigation.
- New error codes, endpoints, settings, migrations, backend validation changes,
  notification delivery, or raw diagnostic output in user-facing messages.
- Global message/error interception, generic logging infrastructure, and
  removal of local debugging edits.

## Open questions and risks

- No blocking scope question remains: the owner selected all bot error screens.
- Telegram client rendering varies. Bold heading plus icon and top placement
  is the chosen persistent treatment, not a promise of text color.
- Shared `error` strings currently mix failures and warnings. Classification
  must follow actual control flow; do not mislabel a successful or uncertain
  write as failed, or encourage an unsafe retry.
- Long homework screens may already approach Telegram's limit. If fitting a
  feedback block requires changing pagination/content policy beyond existing
  helpers, stop for a scoped decision instead of silently truncating content.
