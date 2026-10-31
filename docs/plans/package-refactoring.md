# Telegram, events, and homework package refactoring — Plan

## Goal

Make the implemented bot and backend easier to navigate and maintain, without
changing API contracts, scheduling rules, stored data, or Telegram behavior.

**Status:** Tasks 1–13 complete.

## Scope (README.md sections covered)

- [Bot interface](../../README.md#интерфейс-бота) and
  [implemented Telegram flows](../../README.md#implemented-telegram-flows-and-limits):
  package organization, dependency direction, and typed client boundaries.
- [Schedule changes](../../README.md#изменение-расписания) and the
  [scheduling handoff](../../README.md#scheduling-foundation-and-schedule-viewing-handoff):
  organize management reads and mutation orchestration, preserving the shared
  event abstraction used by lessons, breaks, and vacations.
- [Homework](../../README.md#домашние-задания): group existing services and remove
  one verified duplication in staged-attachment validation.
- This is maintenance of implemented features, not another implementation of
  scheduling, homework, settings, or onboarding. Notification delivery, HTMX,
  offline cache, and new product behavior remain outside this plan.

## Inspection findings and reusable building blocks

- [Feature Status](../../AGENTS.md#feature-status-vs-product-spec) records the
  relevant backend flows as implemented and the bot as partially implemented
  overall. There is **no Known Issues / Tech Debt section** in the inspected
  instructions; do not infer missing behavior from that absent section.
- At plan creation, the bot had 23 top-level Python files. The approximately
  1,900-line API module has since been split into the [API package](../../telegram/api/),
  containing client models, exceptions, circuit breaking, HTTP handling, and
  streaming. Several dialogs exceeded 1,000 lines. Size is a navigation warning,
  not proof that their behavior needs rewriting.
- Scheduling, settings, and homework dialogs import `backend(manager)` from
  the shared [onboarding dialog](../../telegram/dialogs/onboarding.py). This
  shared dependency lookup does not belong to a registration flow.
- [Runtime](../../telegram/runtime.py) is already a useful composition root:
  it constructs and registers dialogs explicitly, isolates updates per user,
  and handles expired callbacks. Keep that design.
- Event reads are in [management/reads.py](../../backend/events/management/reads.py);
  [revision generation](../../backend/events/revisions.py) is shared by readers
  and [mutations](../../backend/events/mutations/).
- The approximately 890-line event service mixes database serialization,
  one-off writes, recurrence writes, and occurrence actions. Extract these
  cohesive units without duplicating
  [validators](../../backend/events/validators.py),
  [conflicts](../../backend/events/conflicts.py),
  [booking policy](../../backend/events/booking_policy.py),
  [range reads](../../backend/events/range_service.py),
  [recurrence expansion](../../backend/events/occurrences.py), or
  [visibility](../../backend/events/visibility.py).
- The former ambiguously named homework service is now split between
  [attachment transfer](../../backend/homework/services/attachments.py),
  [staged attachment handling](../../backend/homework/services/staged_attachments.py),
  and the [cleanup command](../../backend/homework/cleanup.py). Assignment,
  read, submission, and management services remain separate; group and rename
  them rather than merging them.
- `assignment_service._load_staged_attachments` and
  `submission_service._load_staged_attachments` duplicate the owner-scoped locked
  query, expiry, and byte-limit checks. Their assignment/submission error text
  differs and must remain distinct.
- Do **not** indiscriminately merge homework authorization queries: read,
  review, deletion-tombstone recovery, and download paths have different
  requirements. Similar SQL alone does not establish interchangeable policy.
- Existing domain-specific lesson/break/vacation dependencies and validators
  are imported by their routers. The generic
  [event dependency aliases](../../backend/events/dependencies.py) have no
  consumers in the inspected backend/tests. Do not activate them, remove the
  working domain wrappers, or launch unrelated dead-code cleanup.
- Existing tests are substantial: real ASGI/SQLite integration,
  [recorded bot updates](../../tests/bot_support.py),
  [cross-flow coverage](../../tests/test_bot_cross_flow.py),
  [concurrency coverage](../../tests/test_schedule_concurrency.py), and storage
  recovery. Extend gaps rather than creating a parallel test harness.
- Tests patch module-local clocks, transfer constants, and filesystem failures.
  Moving an import without moving its patch target can silently stop exercising
  the intended failure. Patch the actual defining/lookup module after each move.

## Recommended organization

Use feature-first grouping for dialogs and responsibility-first grouping for
backend internals. Keep ORM models, router, configuration, and primary schemas
at their established domain roots. Do not create a directory for every file.

The following is the intended destination, not code that already exists.
Every new package needs an explicit `__init__.py`.

```text
telegram/
  __init__.py
  __main__.py                   # unchanged executable module
  config.py
  runtime.py                    # explicit dispatcher composition
  invitations.py
  api/                          # replaces api.py atomically
    __init__.py                 # explicit public client/model exports
    client.py                   # one BackendClient, same public methods
    errors.py
    circuit_breaker.py
    messages.py                 # existing context-specific error mappings
    transfer.py                 # constants and multipart helpers
    models/
      __init__.py
      common.py                 # Token, NoContent
      accounts.py               # profiles, registration, session, student picker
      events.py                 # management, writes, local resolution, previews
      homework.py
      schedule.py
  shared/
    __init__.py
    access.py                   # backend(manager), moved from onboarding
    inputs.py
    feedback.py
    date_cleanup.py
  dialogs/                      # replaces dialogs.py atomically
    __init__.py                 # no eager import/registration of all flows
    home.py
    onboarding.py
    settings.py
    breaks.py
    vacations.py
    schedule/
      __init__.py
      dialog.py
      formatting.py
    lessons/
      __init__.py
      creation.py
      management.py
    homework/
      __init__.py
      teacher_creation.py
      student.py
      teacher_management.py
      text.py
      transfer.py

backend/events/
  # Existing models.py, schemas.py, router.py, validators.py, policy,
  # recurrence, range, time, visibility, and authorization helpers stay here.
  revisions.py
  management/                   # replaces management.py
    __init__.py                 # list_managed_events/get_managed_event exports
    reads.py
    schemas.py                  # replaces management_schemas.py
  mutations/
    __init__.py
    serialization.py
    displacement.py
    one_off.py
    recurring.py
    occurrence_actions.py
  service.py                    # explicit stable mutation entry-point exports

backend/homework/
  # Existing models.py, schemas.py, router.py, middleware.py, config.py,
  # constants.py, and executable cleanup.py stay here.
  services/
    __init__.py                 # empty; explicit submodule imports
    assignments.py
    reads.py
    submissions.py
    management.py
    attachments.py             # former service.py, including cleanup functions
    staged_attachments.py      # shared claim-validation helper, Task 12
```

### Dependency and compatibility rules

- The bot must remain independently importable without `backend` imports.
  Shared UI helpers can depend on client contracts, never on a feature dialog.
- Model modules must not import `BackendClient` or dialog modules. Schedule
  contracts may reuse event contracts where already needed. Errors may depend
  on preview contracts; contracts must not depend on errors.
- Keep one HTTP client, token cache, pending-registration set, and circuit
  breaker per `BackendClient`. No per-feature clients, inheritance/mixin
  framework, service locator, plugin discovery, or new dependency is needed.
- Runtime imports dialog builders explicitly. Navigation may import another
  flow's public opener; avoid feature-wide barrel imports that introduce cycles.
- Event mutation modules import shared primitives directly, never the service
  facade. Revision generation imports no read or mutation service.
  Occurrence movement may reuse the internal noncommitting one-off creator.
- Keep `backend.events.service` as a small, explicit public entry point because
  auth, routers, and tests already use it. It must re-export the same functions,
  not wrap them with different transaction behavior.
- Keep `from telegram.api import BackendClient` and public contract/exception
  imports working through explicit exports of the same class objects.
  This does **not** promise compatibility for monkeypatching old module-local
  implementation globals; migrate those tests to canonical lookup locations.
- Move internal dialog/homework imports and all repository consumers together;
  do not leave forwarding files for every historical filename. No dynamic
  `sys.modules` aliases or wildcard re-exports.
- No schema changes or Alembic migration are expected in any task. Preserve
  existing model import paths, table/constraint definitions, migration history,
  private storage keys, filenames, and executable module paths.

## Verification gates

### G0 — baseline and contract characterization

Before moving code, run existing lint, formatting, strict typing, and the full
test suite. Record exact commands, counts, skips, and any pre-existing failures
in this plan's implementation evidence. Do not call an unrun baseline green.

Use existing test tooling only. Add focused characterization tests where
coverage is missing:

- Fresh-process imports of bot runtime and backend application in both relevant
  orders, without starting polling or making network requests; verify importing
  the bot does not load backend modules. Use dummy environment values rather
  than real credentials. Do not test by importing the bot's executable module.
- Representative route/method/status/response contracts and serialized public
  payloads, including explicit UTC offsets, null recurrence cutoffs, cancellation
  identity, revision and displacement tokens, homework pagination/completion,
  and attachment headers.
- Fixed revision fixtures for one-off, finite recurring, and open-ended events
  with cancellations, fixed IDs/timestamps, and known expected hashes. Preserve
  the current cancellation ordering behavior; do not introduce new sorting.
- Dispatcher construction, existing FSM state strings, and representative
  callback widget IDs remain stable. Exercise real updates, not only imports.
- Add an AST-based architecture check, using the standard library, that rejects
  backend imports from the bot and imports of feature dialogs from shared
  helpers. Cover regular imports, `from` imports, and relative imports; keep
  the check narrow rather than introducing a general architecture framework.

Keep behavior assertions independent of implementation filenames except for
deliberate public import and architecture checks. Do not regenerate expected
responses or hashes from the refactored implementation to make tests pass.

### G1 — each implementation task

- Run the task's relevant existing tests and added characterization tests.
  Multiple related test files should use one pytest invocation.
- Run `uv run ruff check backend telegram tests`,
  `uv run ruff format --check backend telegram tests`, and
  `uv run mypy backend telegram` after each coherent extraction.
- Update imports, failure-injection targets, executable consumers, and directly
  relevant documentation in the same task. Keep test assertions and simulated
  transport failures at least as strong as before.
- Inspect the diff for accidental logic, copy, schema, callback, and transaction
  changes. Mechanical relocation and deduplication are separate tasks.
- If a task causes a new failure, stop and repair it before continuing. Record
  unrelated baseline failures separately rather than fixing them incidentally.

### G2 — final release gate

Run the complete suite once after integration, plus the conditional PostgreSQL
checks described in Task 13. Passing import tests or SQLite alone is not proof
that mutation serialization still works on PostgreSQL. Record limitations
explicitly; no claim of absolute zero regression is possible.

## Tasks

Each numbered section is a standalone implementation handoff. Execute in the
dependency order below, keeping every completed task runnable. General gates
above are part of every task's acceptance criteria.

## Task 1 — Establish behavior and import safeguards

**Domain:** Existing bot, event, and homework tests  
**Type:** service / bot verification  
**Depends on:** None  
**Migration:** None

### Context

The README flows work today; the refactor must preserve their contracts, not
reconstruct them from new code.

### What to do

1. Establish G0 using the existing suite and static checks.
2. Audit existing tests against G0's characterization list; add only missing
   cases in focused test files using current fixture patterns.
3. Keep test collection/import checks offline. Use temporary storage and
   existing database overrides, never developer data or a live bot token.

### Reuse

[conftest fixtures](../../tests/conftest.py), `BotDriver`, `RecordingSession`,
[bot client tests](../../tests/test_bot_client.py),
[runtime tests](../../tests/test_bot_runtime.py), and existing API tests.

### Acceptance Criteria

- [x] G0 results and missing prerequisites are recorded.
- [x] New characterization checks pass against the original implementation.
- [x] Tests assert stable behavior, not arbitrary line counts or directory size.

### Out of scope

Production file moves, new test frameworks, performance claims, and unrelated
test cleanup.

**Suitable for:** AI agent.

### Task 1 execution evidence

Initial baseline on 2026-10-29:

- `uv run pytest -q tests`: 442 passed, 2 failed, 5 skipped, 2743 warnings
  (54.36s). The failures were
  `test_expired_token_recovers_and_inactive_account_gets_schedule_error` and
  `test_registered_student_can_open_week_and_return_home` in
  `tests/test_bot_schedule_dialogs.py`. Both stopped in the shared registration
  helper while the bot remained on its review screen. Each test passed alone,
  and the full module passed (8 passed); these failures did not reproduce in the
  post-change full-suite run.
- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 150 files already
  formatted.
- `uv run mypy backend telegram`: passed; no issues in 101 source files.

Added focused guards for both fresh-process import orders, bot independence from
backend modules, AST-based bot/shared-helper import boundaries, fixed hashes for
one-off/finite/open-ended revisions, and live dispatcher state/widget IDs.
Existing tests already cover UTC offsets, recurrence cutoffs and cancellation
identity, displacement/revision tokens, homework pagination/completion, and
attachment response headers; no further gaps in those areas were identified.

Verification after the additions:

- `uv run pytest -q tests/test_package_boundaries.py
  tests/test_event_revision_contract.py tests/test_bot_runtime.py`: 9 passed.
- `uv run pytest -q tests`: 450 passed, 5 skipped, 2743 warnings (61.53s).
- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 152 files already
  formatted.
- `uv run mypy backend telegram`: passed; no issues in 101 source files.

Task 1 has no unmet prerequisites. Disposable PostgreSQL availability for the
conditional final release checks remains unverified and is still a Task 13
item.

## Task 2 — Separate bot API contracts from HTTP implementation

**Domain:** Existing `telegram` API client  
**Type:** bot  
**Depends on:** Task 1  
**Migration:** None

### Context

The API client supports all existing flows, including deliberately different
retry rules for definite rejection and uncertain writes.

### What to do

1. Replace the API module with the proposed package. Move existing models by
   feature, errors, circuit breaker, error mappings, and transfer helpers.
2. Keep `BackendClient` and its HTTP orchestration together in `client.py`;
   retain method signatures, timeout defaults, and state ownership.
3. Export existing public classes/types/constants explicitly from `api/__init__`.
   Resolve forward model annotations after moving contracts; no duplicate model
   or exception definitions.
4. Update tests patching transfer size/timeouts to the module that now reads
   those values. Verify that over-limit and timeout injections still trigger.

### Reuse

`BackendClient.request`, `session`, `read`, `write`, `delete`,
`_backend_error`, `CircuitBreaker`, and existing multipart/streaming functions.

### Acceptance Criteria

- [x] G1 passes with `test_bot_client.py`, bot onboarding tests, and G0 checks.
- [x] One renewed-token retry occurs only after definitive 401 rejection;
  transport/5xx/malformed-success writes remain unknown and are not replayed.
- [x] Registration reconciliation, same-key homework retry, bounded streaming,
  file-size limits, cancellation, and half-open breaker probing remain covered.
- [x] Public API imports resolve to the canonical classes, not compatibility copies.

### Out of scope

Splitting the client into feature clients, changing retry policy, changing
request/response models, or sharing backend Pydantic models with the bot.

**Suitable for:** AI agent.

### Task 2 execution evidence

Moved the bot client into `telegram/api/`, separating the HTTP client,
circuit breaker, errors, error mappings, transfer helpers, and account/event/
homework/schedule contracts. `telegram.api` explicitly exports canonical class
objects. Homework contract constants are defined with the contracts; transfer
and client code depend on them, not the reverse. Transfer timeout and byte
limit tests now patch `telegram.api.transfer`, the module that reads them.

Verification:

- `uv run pytest -q tests/test_bot_client.py tests/test_bot_onboarding.py
  tests/test_bot_runtime.py tests/test_package_boundaries.py
  tests/test_event_revision_contract.py`: 56 passed.
- This includes definite-401 renewal, ambiguous write non-replay, registration
  reconciliation, same-key homework retry, bounded streaming, upload/download
  size limits, timeout behavior, cancellation, breaker probing, public export
  identity, and Task 1 safeguards.
- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 163 files
  already formatted.
- `uv run mypy backend telegram`: passed; no issues in 112 source files.

## Task 2 status: complete

## Task 3 — Introduce shared bot support and account dialog grouping

**Domain:** Existing bot runtime, home, onboarding, and settings  
**Type:** bot  
**Depends on:** Task 2  
**Migration:** None

### Context

Client lookup is shared behavior currently owned by onboarding; home and
registration are otherwise already separate builders.

### What to do

1. Move the existing `backend(manager)` lookup to `shared/access.py`.
2. Move inputs, feedback, and accepted-input cleanup helpers to `shared/`.
   Update all consumers, including flows not yet relocated.
3. Replace the old dialog module with a package containing `home.py`,
   `onboarding.py`, and `settings.py`. Move home/registration/settings imports
   in runtime and remaining flows atomically.
4. Preserve class names, state declarations, widget IDs, text, and build order.
   Keep invitation utilities at the bot root.

### Reuse

Existing `Screens`, `Wizard`, `build_dialog`, `build_wizard`,
`build_settings_dialog`, `feedback_block`, and input/cleanup functions.

### Acceptance Criteria

- [x] G1 passes with onboarding, flows, settings, runtime, scheduling-input,
  feedback, and bot client tests.
- [x] No scheduling/settings/homework flow imports onboarding for client access.
- [x] `/start`, invitation entry, Back/Cancel, settings partial updates,
  edit-in-place behavior, and stale-callback handling remain unchanged.

### Out of scope

New generic dialog controllers, new draft schemas, or onboarding/settings UX changes.

**Suitable for:** AI agent.

### Task 3 execution evidence

Moved shared access, inputs, feedback, and accepted-input cleanup into
`telegram/shared/`; grouped home, onboarding, and settings dialog builders under
`telegram/dialogs/`. Updated every bot and test consumer in the same change.
Runtime keeps the same explicit builder order, and the moved helper no longer
depends on the onboarding dialog.

Verification:

- `uv run pytest -q tests/test_bot_*.py tests/test_scheduling_inputs.py
  tests/test_telegram_feedback.py tests/test_package_boundaries.py
  tests/test_event_revision_contract.py`: 203 passed.
- Repository search confirmed there are no remaining imports from the moved
  legacy modules and no scheduling/settings/homework dialog imports of
  `telegram.dialogs.onboarding.backend`.
- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 166 files
  already formatted.
- `uv run mypy backend telegram`: passed; no issues in 115 source files.

## Task 3 status: complete

## Task 4 — Group bot scheduling dialogs by feature

**Domain:** Existing bot schedule, lesson, break, and vacation dialogs  
**Type:** bot  
**Depends on:** Task 3  
**Migration:** None

### Context

These are working clients of shared backend policy. Grouping should make the
flows discoverable without introducing a second scheduling implementation.

### What to do

1. Move schedule dialog/formatting and lesson creation/management to the
   proposed feature subpackages.
2. Move break/vacation dialogs into the dialog package as single modules.
   Their size alone does not justify splitting callback orchestration yet.
3. Update runtime, cross-flow navigation, tests, and module-local clock patch
   targets. Preserve the existing runtime registration order.
4. Keep local form validation and parsing calls intact; backend policy stays
   authoritative.

### Reuse

Existing builders/openers, shared inputs, local-interval resolution, schedule
formatting, displacement previews, and API error/reconciliation behavior.

### Acceptance Criteria

- [x] G1 passes with all bot lesson/break/vacation/schedule tests,
  `test_schedule_format.py`, `test_schedule_presentation.py`,
  `test_scheduling_inputs.py`, and `test_bot_cross_flow.py`.
- [x] Day-first input, year confirmation, DST labels, nullable weekly cutoff,
  overflow pagination, automatic breaks, and exact occurrence actions persist.
- [x] No widget IDs, state names, button order, or recovery flags change.

### Out of scope

Unifying lesson/break/vacation state machines, changing date rules, or arbitrary
file-size thresholds.

**Suitable for:** AI agent.

### Task 4 execution evidence

Moved schedule viewing/presentation into `dialogs/schedule/`, lesson creation
and management into `dialogs/lessons/`, and break/vacation dialogs into
`dialogs/breaks.py` and `dialogs/vacations.py`. Updated runtime and cross-flow
imports without changing builder registration order; no legacy scheduling
module imports remain.

Verification:

- `uv run pytest -q tests/test_bot_*.py tests/test_schedule_format.py
  tests/test_schedule_presentation.py tests/test_scheduling_inputs.py
  tests/test_package_boundaries.py tests/test_event_revision_contract.py`:
  219 passed.
- This covers date parsing/year confirmation, DST rendering, nullable weekly
  cutoffs, pagination overflow, automatic breaks, occurrence actions, and
  dispatcher/widget behavior.
- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 168 files
  already formatted.
- `uv run mypy backend telegram`: passed; no issues in 117 source files.

## Task 4 status: complete

## Task 5 — Group bot homework dialogs and transfer support

**Domain:** Existing bot homework flows  
**Type:** bot  
**Depends on:** Task 4  
**Migration:** None

### Context

Teacher creation, student submission, and teacher review are distinct flows
with important unknown-outcome and restart safeguards.

### What to do

1. Move the three dialogs and homework text/transfer helpers into the proposed
   homework subpackage without merging their state machines.
2. Update runtime, home navigation, tests, clocks, and transfer imports.
3. Verify that API package transfer helpers handle HTTP uploads/downloads,
   while `dialogs/homework/transfer.py` retains the aiogram `InputFile` adapter.
   Do not duplicate the streaming implementation.

### Reuse

Existing homework builders, `HomeworkDownloadFile`, text pagination helpers,
operation-key management, and `BackendClient` homework methods.

### Acceptance Criteria

- [x] G1 passes with all bot homework tests, `test_telegram_homework_text.py`,
  `test_bot_client.py`, and `test_bot_cross_flow.py`.
- [x] Assignment/submission keys survive uncertain outcomes; deletion is
  reconciled rather than blindly replayed.
- [x] Text is not truncated; document downloads remain separate intentional
  sends, while normal navigation edits the active screen.

### Out of scope

Backend contract changes, persistent bot drafts, notification delivery, and
generic teacher/student homework controllers.

**Suitable for:** AI agent.

### Task 5 execution evidence

Moved teacher creation, student submission, teacher management, homework text
helpers, and the aiogram download adapter into `telegram/dialogs/homework/`.
Updated runtime/home imports and the teacher-creation clock patch target.
The HTTP transfer implementation remains in `telegram.api.transfer`; the
dialog transfer module retains only the aiogram `InputFile` adapter.

Verification:

- `uv run pytest -q tests/test_bot_homework_*.py
  tests/test_telegram_homework_text.py tests/test_bot_client.py
  tests/test_bot_cross_flow.py tests/test_package_boundaries.py
  tests/test_event_revision_contract.py`: 68 passed.
- This covers uncertain-outcome operation keys, deletion reconciliation,
  complete text pagination, distinct document sends, and cross-flow behavior.
- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 169 files
  already formatted.
- `uv run mypy backend telegram`: passed; no issues in 118 source files.
- No imports from the replaced top-level homework dialog/helper modules remain.

## Task 5 status: complete

## Task 6 — Group event management and extract shared revisions

**Domain:** Existing event management reads and schemas  
**Type:** service  
**Depends on:** Task 1  
**Migration:** None

### Context

Management reads are shared across lesson/break/vacation routers; revision
generation is also needed by mutation code.

### What to do

1. Extract `build_event_revision` and its timestamp serialization helper into
   `events/revisions.py`, preserving payload fields, order, and hash encoding.
2. Replace management module/schema siblings with `management/reads.py` and
   `management/schemas.py`; explicitly export the two read entry points.
3. Update every router, event mutation import, and test consumer. Include
   `events/router.py`, whose local-time resolution contracts currently come
   from the management-schema module.
4. Keep management scope, authorization, inspection, cancellation counts, and
   visibility logic unchanged.

### Reuse

`list_managed_events`, `get_managed_event`, `assert_owns_event`,
`expand_recurrence`, existing visibility/range helpers, and revision fixtures.

### Acceptance Criteria

- [x] G1 passes with management-read, authorization, occurrence-action,
  recurrent-edit, event-time API, and automatic-break lifecycle tests.
- [x] Fixed revision hashes and optimistic concurrency behavior are identical.
- [x] Writers no longer import read orchestration to calculate revisions.

### Out of scope

Changing pagination, optimizing queries, changing hash ordering, or relocating
all event schemas into a new hierarchy.

**Suitable for:** AI agent.

### Task 6 execution evidence

Moved management reads and schemas into `events/management/`, with explicit
package exports for `list_managed_events` and `get_managed_event`. Extracted
revision generation and its timestamp serializer into `events/revisions.py`;
updated event mutation code, all affected routers, and test consumers. The
event time-resolution router now imports its contracts from the new schema
location. Fixed revision hashes and read authorization/visibility behavior
remain unchanged.

Verification:

- `uv run pytest -q tests/test_event_management_reads.py
  tests/test_event_authorization.py tests/test_occurrence_actions.py
  tests/test_recurrent_event_edits.py tests/test_event_time_api.py
  tests/test_auto_break_lifecycle.py tests/test_event_revision_contract.py
  tests/test_open_ended_recurrence_schema.py`: 50 passed.
- This covers management scopes, authorization and visibility, occurrence
  actions, recurrence edits, local-time resolution, automatic breaks, and
  stable revision hashes.
- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 171 files
  already formatted.
- `uv run mypy backend telegram`: passed; no issues in 120 source files.
- No imports from the replaced `events.management` module or
  `events.management_schemas` module remain.

## Task 6 status: complete

## Task 7 — Isolate schedule mutation serialization

**Domain:** Existing event mutation guard  
**Type:** service  
**Depends on:** Task 6  
**Migration:** None

### Context

The persistent per-teacher guard is correctness-critical and used outside
event CRUD, including teacher preference updates.

### What to do

1. Move `run_serialized_schedule_mutation`, database guard acquisition, and
   SQLite contention classification to `mutations/serialization.py`.
2. Keep the existing public service import as an explicit re-export; preserve
   all external callers and keyword defaults.
3. Preserve lock lifetime, dialect handling, commit/rollback behavior on domain
   errors, cancellation, and `rollback_domain_errors=True`.

### Reuse

Existing `TeacherScheduleGuard` model and SQLite/PostgreSQL guard statements.

### Acceptance Criteria

- [x] G1 passes with concurrency, booking policy, occurrence-action,
  recurrent-edit, and bot settings tests.
- [x] SQLite concurrency tests pass; PostgreSQL variants run when a disposable
  database is available, otherwise the missing gate is recorded for Task 13.
- [x] No nested serialization, shortened guard lifetime, or extra commit appears.

### Out of scope

Replacing the database guard with an in-memory lock or adding another transaction layer.

**Suitable for:** AI agent.

### Task 7 execution evidence

Moved schedule guard acquisition, SQLite contention classification, and
`run_serialized_schedule_mutation` into `events/mutations/serialization.py`.
The existing service module explicitly re-exports the same function for
existing callers. The guard acquisition, operation, commit/rollback handling,
and cancellation behavior remain in the same transaction scope; event writes
continue to use the service-level re-export without adding another
serialization layer.

Verification:

- `uv run pytest -q tests/test_schedule_concurrency.py
  tests/test_lesson_booking_policy.py tests/test_occurrence_actions.py
  tests/test_recurrent_event_edits.py tests/test_bot_settings_dialogs.py`:
  52 passed, 5 skipped.
- SQLite concurrency and teacher preference serialization passed. PostgreSQL
  variants were skipped because `SCHEDULE_TEST_DATABASE_URL` is not configured;
  that conditional release assurance remains a Task 13 gate.
- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 173 files
  already formatted.
- `uv run mypy backend telegram`: passed; no issues in 122 source files.

## Task 7 status: complete

## Task 8 — Extract one-off event mutations and shared displacement

**Domain:** Existing one-off event writes  
**Type:** service  
**Depends on:** Task 7  
**Migration:** None

### Context

Lessons, breaks, and vacations share one-off mutation orchestration; occurrence
movement also reuses creation without committing.

### What to do

1. Move one-off create/update/delete entry points and their implementation
   helpers into `mutations/one_off.py`.
2. Move `_apply_displacements` to `mutations/displacement.py` and have both
   extracted and remaining recurring writes import the same function.
3. Keep `events/service.py` entry points as explicit exports; update internal
   callers to the defining modules to prevent cycles.
4. Preserve the noncommitting creator path and saved automatic-break snapshot
   argument used during occurrence moves.

### Reuse

`prepare_lesson_write`, `EventValidator`, shared serialization, authorization,
revision generation, and displacement implementation.

### Acceptance Criteria

- [x] G1 passes with smoke, authorization, conflicts, booking policy,
  automatic-break lifecycle, occurrence-action, and concurrency tests.
- [x] Rejected writes leave no partial displacement; deleting a break does not
  restore displaced lessons.
- [x] The public service and canonical module expose the same entry-point objects.

### Out of scope

Changing admission rules, commit ownership, automatic-break calculations, or event models.

**Suitable for:** AI agent.

### Task 8 execution evidence

Moved one-off create/update/delete orchestration and helpers into
`events/mutations/one_off.py`, and moved the shared displacement application
into `events/mutations/displacement.py`. The service module explicitly
re-exports the canonical entry-point objects; recurring writes reuse the
shared displacement function. The occurrence-move path calls the defining
one-off module's transaction-level creator with its original
`commit=False` and saved automatic-break snapshot arguments. Moved the
optimistic revision assertion to the shared revisions module for both mutation
paths.

Verification:

- `uv run pytest -q tests/test_smoke.py tests/test_event_authorization.py
  tests/test_event_conflicts.py tests/test_lesson_booking_policy.py
  tests/test_auto_break_lifecycle.py tests/test_occurrence_actions.py
  tests/test_schedule_concurrency.py tests/test_event_visibility.py
  tests/test_recurrent_event_edits.py tests/test_package_boundaries.py`:
  89 passed, 5 skipped.
- Existing preview tests verify rejected displacement leaves lessons and
  recurrence cancellations unchanged; added a regression proving that
  deleting a confirmed displacing break does not restore deleted lessons.
- Public service exports are asserted identical to the canonical one-off and
  serialization implementations.
- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 175 files
  already formatted.
- `uv run mypy backend telegram`: passed; no issues in 124 source files.
- The five PostgreSQL concurrency variants remain skipped because no disposable
  database URL is configured; they remain a Task 13 release gate.

## Task 8 status: complete

## Task 9 — Extract recurring event mutations

**Domain:** Existing recurring event writes  
**Type:** service  
**Depends on:** Task 8  
**Migration:** None

### Context

Recurring writes have separate cancellation-replacement and timezone-snapshot
rules that must not be reduced to generic CRUD.

### What to do

1. Move recurring create/update implementations and `_validate_cancellations`
   into `mutations/recurring.py`.
2. Reuse shared serialization, displacement, revisions, recurrence expansion,
   and validators directly; keep existing public entry-point exports.
3. Keep omitted versus explicit cancellation-list semantics and null cutoff
   handling exactly as implemented.

### Reuse

`create_recurrent_event`, `update_recurrent_event`, `expand_recurrence`,
`EventValidator`, and the existing shared temporal/booking policy.

### Acceptance Criteria

- [x] G1 passes with recurrence expansion/schema, recurrent edits, conflicts,
  booking policy, automatic-break lifecycle, and concurrency tests.
- [x] Teacher-local DST recurrence and legacy UTC-step behavior are unchanged.
- [x] Null cutoff remains open-ended only for weekly lessons; the one-year
  write-validation bound is not persisted as a series end.
- [x] Finite limits and cancellation replacement/rollback behavior are unchanged.

### Out of scope

Changing recurrence algorithms, date horizons, or cancellation persistence.

**Suitable for:** AI agent.

### Task 9 execution evidence

Moved recurring creation/update and cancellation validation into
`events/mutations/recurring.py`. The module directly reuses the established
serialization guard, displacement helper, revision builder, recurrence
expansion, booking policy, and event validator. The service module explicitly
re-exports the canonical recurrent entry points; cancellation omission versus
an explicit empty list, timezone snapshots, and open-ended cutoff behavior
were not altered.

Verification:

- `uv run pytest -q tests/test_event_range_service.py
  tests/test_open_ended_recurrence_schema.py tests/test_recurrent_event_edits.py
  tests/test_event_conflicts.py tests/test_lesson_booking_policy.py
  tests/test_auto_break_lifecycle.py tests/test_schedule_concurrency.py
  tests/test_package_boundaries.py tests/test_event_revision_contract.py`:
  104 passed, 5 skipped.
- These tests cover recurrence expansion (including teacher-local and legacy
  UTC-step series), schema/open-ended limits, finite-series edits, explicit
  cancellation replacement, booking policy, displacement, and concurrency.
- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 176 files
  already formatted.
- `uv run mypy backend telegram`: passed; no issues in 125 source files.
- The five PostgreSQL concurrency variants remain skipped without a
  disposable database URL and are recorded as a Task 13 gate.

## Task 9 status: complete

## Task 10 — Extract occurrence actions and finish the event facade

**Domain:** Existing recurring lesson cancellation/movement  
**Type:** service  
**Depends on:** Task 9  
**Migration:** None

### Context

An occurrence move atomically creates a cancellation and replacement lesson;
failure must not strand only one half.

### What to do

1. Move cancel/move entry points, occurrence lookup, and cancellation guards to
   `mutations/occurrence_actions.py`.
2. Move stale-revision checking to `revisions.py` if shared by multiple mutation
   modules; retain exact exception codes and details.
3. Reuse the canonical noncommitting one-off creator, without re-entering the
   public serialized creation entry point.
4. Reduce `events/service.py` to its deliberate explicit public exports.
   Remove moved implementation code, not the public entry-point module.

### Reuse

`cancel_recurrent_lesson_occurrence`, `move_recurrent_lesson_occurrence`,
`_load_occurrence_action`, recurrence identity checks, and shared revisions.

### Acceptance Criteria

- [x] G1 passes with occurrence actions, recurrent edits, authorization,
  automatic-break lifecycle, concurrency, and bot lesson management tests.
- [x] Failed movement rolls back the cancellation and replacement together.
- [x] Exact UTC occurrence identity, stale revision, duplicate cancellation,
  and inherited automatic-break snapshot checks remain effective.
- [x] No mutation implementation imports the public event service facade.

### Out of scope

Changing lesson-only occurrence actions or consolidating all mutation paths
into a generic command framework.

**Suitable for:** AI agent.

### Task 10 execution evidence

Moved occurrence cancel/move entry points, occurrence lookup, and duplicate
cancellation guards into `events/mutations/occurrence_actions.py`. The
occurrence move calls the canonical transaction-level one-off creator and
retains its atomic cancellation/replacement transaction. The stale-revision
assertion is shared from `events/revisions.py`. Reduced `events/service.py` to
explicit re-exports with `__all__`; all service entry points are identity
checked against their defining modules.

Verification:

- `uv run pytest -q tests/test_occurrence_actions.py
  tests/test_recurrent_event_edits.py tests/test_event_authorization.py
  tests/test_auto_break_lifecycle.py tests/test_schedule_concurrency.py
  tests/test_bot_lesson_management.py tests/test_package_boundaries.py`:
  59 passed, 5 skipped.
- Regression coverage includes failed move rollback, exact occurrence identity,
  stale revisions, duplicate cancellation, inherited automatic-break
  snapshots, and bot lesson management.
- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 177 files
  already formatted.
- `uv run mypy backend telegram`: passed; no issues in 126 source files.
- No `backend.events.service` imports exist in `events/mutations/`; the
  PostgreSQL concurrency variants remain a Task 13 gate.

## Task 10 status: complete

## Task 11 — Group homework services without changing ownership

**Domain:** Existing homework services  
**Type:** service  
**Depends on:** Task 1  
**Migration:** None

### Context

Service responsibilities already exist; the main issue is a flat namespace
and an attachment service whose filename hides its purpose.

### What to do

1. Move assignment/read/submission/management modules to `services/` under the
   proposed names; move the old generic service to `services/attachments.py`.
2. Update router, cleanup command, inter-service imports, and all test patch
   targets. Keep `python -m backend.homework.cleanup` unchanged.
3. Keep models/config/constants/schemas/middleware at their domain roots.
   Avoid a new all-services barrel import or repository/service abstraction.
4. Preserve attachment cleanup functions together for this mechanical stage;
   do not combine this task with helper deduplication.

### Reuse

Existing `create_assignment`, `list_homework`, `get_homework_detail`,
`create_submission`, `list_submission_reviews`, `complete_homework`,
`delete_homework`, and attachment/storage functions.

### Acceptance Criteria

- [x] G1 passes with all backend homework tests and all bot homework/client tests.
- [x] Idempotency, active-link authorization, versioned submissions, completion
  provenance, cleanup tombstones, private downloads, and persisted files survive.
- [x] Existing storage failure/timeout patches still hit the implementation.
- [x] Cleanup import remains safe; tests never run it against configured real storage.

### Out of scope

Schema/migration changes, storage rewrites, combining services, or changing the
executable cleanup interface.

**Suitable for:** AI agent.

### Task 11 execution evidence

Moved the homework assignment, read, submission, management, and attachment
services into `backend/homework/services/` using the planned module names.
Updated the router, management-to-attachment dependency, cleanup command
import, tests, and failure-injection targets. The services package has no
barrel exports; `python -m backend.homework.cleanup` remains the executable
path. Added a cleanup import-safety check that imports but never invokes
cleanup.

Verification:

- `uv run pytest -q tests/test_homework_*.py tests/test_bot_homework_*.py
  tests/test_bot_client.py tests/test_bot_cross_flow.py
  tests/test_package_boundaries.py`: 101 passed.
- This covers backend and Telegram homework flows, idempotency, authorization,
  versioned submissions, cleanup tombstones, private file transfers, persisted
  storage, and the moved timeout/storage failure patch targets.
- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 178 files
  already formatted.
- `uv run mypy backend telegram`: passed; no issues in 127 source files.
- No imports from the replaced flat homework service module paths remain.
  Cleanup was imported only; it was not run against any configured storage.

## Task 11 status: complete

## Task 12 — Deduplicate staged-attachment claim validation

**Domain:** Existing homework assignment/submission services  
**Type:** service  
**Depends on:** Task 11  
**Migration:** None

### Context

Two verified copies of staged-attachment loading enforce the same storage
ownership, freshness, and aggregate-size rules.

### What to do

1. Extract only that common query/validation into
   `services/staged_attachments.py`, used by assignment and submission creation.
2. Accept the owner ID and retain operation-specific limit/error wording with
   the smallest explicit interface; no generic policy engine.
3. Preserve row locks, expiry timing, supplied-ID count behavior, HTTP status
   codes, and total-byte checks. The helper must not commit, attach files, or
   take over idempotency reconciliation.
4. Add parameterized behavior tests for both callers where existing cases do
   not already cover the boundaries.

### Reuse

Existing `_load_staged_attachments` behavior, `normalize_utc`,
`MAX_ASSIGNMENT_ATTACHMENTS`, and `MAX_ASSIGNMENT_ATTACHMENT_BYTES`.

### Acceptance Criteria

- [x] G1 passes with assignment, submission, attachment, management, and bot
  homework tests.
- [x] Exactly five attachments and exactly 50 MiB pass this helper's aggregate
  limits; six attachments and total bytes above 50 MiB fail.
- [x] Preserve the separate 20 MiB per-upload limit in attachment staging.
- [x] Foreign, expired, missing, already-claimed, cleanup-pending, and duplicate
  IDs retain their current outcomes for both operations.
- [x] Assignment/submission transaction rollback and same-key retry still work.

### Out of scope

Merging authorization, idempotency, completion, or tombstone-recovery logic.

**Suitable for:** AI agent.

### Task 12 execution evidence

Extracted the shared owner-scoped, row-locked staged-attachment query and
validation into `services/staged_attachments.py`. Assignment and submission
callers pass their operation-specific count and aggregate-size messages; the
helper does not attach files, commit, or reconcile idempotency outcomes.
Added parameterized tests for missing, foreign, expired, claimed, and
cleanup-pending IDs through both callers, plus schema duplicate-ID checks and
operation-specific count-error checks.

Verification:

- `uv run pytest -q tests/test_homework_assignments.py
  tests/test_homework_submissions.py tests/test_homework_attachments.py
  tests/test_homework_management.py tests/test_homework_reads.py
  tests/test_homework_staged_attachments.py tests/test_bot_homework_*.py
  tests/test_bot_client.py tests/test_bot_cross_flow.py`: 109 passed.
- Both caller limits passed at exactly five attachments and 50 MiB; six
  attachments and sizes above 50 MiB were rejected. Existing tests also
  preserve the 20 MiB per-upload limit, finalization rollback, and same-key
  reconciliation.
- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 180 files
  already formatted.
- `uv run mypy backend telegram`: passed; no issues in 128 source files.

## Task 12 status: complete

## Task 13 — Verify integrated behavior and document the new boundaries

**Domain:** Existing backend, bot, tests, packaging, and project documentation  
**Type:** service / bot verification  
**Depends on:** Tasks 5, 10, and 12  
**Migration:** None

### Context

Localized green tests do not establish that the independently moved bot,
backend services, and package discovery still compose correctly.

### What to do

1. Run G2:

   ```shell
   uv run ruff check backend telegram tests
   uv run ruff format --check backend telegram tests
   uv run mypy backend telegram
   uv run pytest -q tests
   ```

2. Run concurrency tests with `SCHEDULE_TEST_DATABASE_URL` pointed only at an
   explicitly disposable PostgreSQL database. The fixture uses temporary schemas;
   never use a production/developer database without approval. Record any
   PostgreSQL skips as an incomplete release assurance, not a pass.
3. Use the existing packaging backend to build a wheel and verify the new
   packages are included (`uv build --wheel`, if available in the implementation
   environment). Import the installed wheel outside the checkout, without
   polling/network access, so source-tree imports cannot hide omitted packages.
4. Verify backend route registration and the complete dispatcher cross-flow:
   returning accounts, lesson creation/edit/move, break displacement, vacation
   visibility, homework create/submit/review/delete, settings, and recovery.
   Reuse existing automated scenarios; add a scenario only for an uncovered seam.
5. Update the README's internal module references and the architecture/project
   structure guidance in AGENTS.md. Keep executable commands and feature status
   unchanged. Update active documentation/code references to moved paths;
   historical verification narratives may explicitly retain their old paths.
6. Record task results, exact commands, skips, and packaging/PostgreSQL limitations
   in this plan. No live Telegram poller, credential rotation, data migration,
   or deployment is necessary to verify the refactor.

### Reuse

Existing Hatch wheel configuration, ASGI/SQLite fixtures, conditional PostgreSQL
concurrency fixtures, restart/storage tests, and `BotDriver` cross-flow tests.

### Acceptance Criteria

- [x] All new packages import without cycles; independent bot/backend imports work.
- [x] Full tests and static checks show no newly introduced failures.
- [x] Public API/state/callback/revision contracts match Task 1 characterizations.
- [x] Installed-wheel and PostgreSQL concurrency checks pass.
- [x] No schema/model/migration drift, storage-key change, or new dependency.
- [x] Documentation describes the actual final structure and verification evidence.

### Out of scope

Live rollout, infrastructure redesign, new features, or unrelated debt cleanup.

**Suitable for:** AI agent.

### Task 13 execution evidence

- `uv run ruff check backend telegram tests`: passed.
- `uv run ruff format --check backend telegram tests`: passed; 180 files
  already formatted.
- `uv run mypy backend telegram`: passed; no issues in 128 source files.
- `uv run --env-file .env pytest -q -rs tests`: 473 passed, 2860 warnings,
  no skips. This includes the PostgreSQL concurrency variants, route smoke,
  and bot cross-flow scenarios.
- `uv run --env-file .env pytest -q -rs tests/test_schedule_concurrency.py`:
  15 passed, no skips (SQLite and PostgreSQL variants).
- The Compose app container successfully queried its configured database with
  `SELECT current_database(), 1` (`app`, `1`), and `GET /docs` returned HTTP 200.
  The PostgreSQL service was healthy and both app/database ports bind only to
  localhost.
- Packaging commands:

  ```shell
  uv build --wheel --out-dir /tmp/olm-package-refactoring-wheel-8b8a66fe-c16e-424f-aaa2-572258dac64e
  uv pip install --target /tmp/olm-package-refactoring-install-8b8a66fe-c16e-424f-aaa2-572258dac64e --no-deps /tmp/olm-package-refactoring-wheel-8b8a66fe-c16e-424f-aaa2-572258dac64e/olm-0.1.0-py3-none-any.whl
  ```

  The build produced `olm-0.1.0-py3-none-any.whl`; `unzip -l` confirmed the
  new event, homework, and Telegram packages. From `/tmp`, independent backend
  and Telegram Python processes imported the moved modules with `PYTHONPATH`
  set to the install target; assertions confirmed every imported module
  resolved to the installed wheel, not the checkout.
- README plan status and `AGENTS.md` package/architecture guidance now reflect
  the refactor. No stale moved-module paths remain in those active documents.
- No model, schema, migration, dependency, or storage-key changes were made;
  existing attachment and cleanup regressions passed in the full suite.

## Task 13 status: complete

## Execution and rollback strategy

- Independent tracks after Task 1: bot (2–5), events (6–10), homework (11–12).
  They converge at Task 13. Sequential execution is fine; parallel edits need
  coordination because bot API tests and documentation are shared.
- Keep each task as a coherent, verified checkpoint. Commit only when explicitly
  requested by the owner; do not mix unrelated working-tree changes.
- Preserve behavior before improving internals. If a necessary extraction
  reveals a behavior defect, document it and seek a decision before expanding
  this maintenance scope, unless the defect was introduced by the refactor.
- No migration means rollback is a code rollback to a verified checkpoint,
  not a database downgrade or storage rewrite. Do not discard unrelated edits.
- Keep package initializers small. Reassess the tree if a move creates a cycle;
  fix dependency direction instead of adding local-import workarounds everywhere.

## Out of scope for this release

- Rewriting large dialogs simply to meet a line-count target. Further
  per-dialog handler/view splits can follow a demonstrated maintenance need
  after these boundaries and tests are established.
- Changing Telegram copy, callback IDs, state names, registration order,
  settings semantics, date parsing, or retry behavior.
- Changing event policy, recurrence math/horizons, authorization, transaction
  ownership, model paths, migrations, or private storage behavior.
- New notifications, arbitrary-date UI, HTMX work, offline caching, broad
  repository cleanup, or new architecture/testing dependencies.

## Open questions and stop conditions

- No product decision is required to begin the behavior-preserving moves.
  The proposed package tree is the recommended implementation, not a new
  configurable architecture.
- Disposable PostgreSQL availability is not established by this planning pass.
  Arrange it for final mutation assurance; otherwise leave that release gate open.
- If external consumers require historical internal module paths beyond the
  two deliberate public facades, identify them before removing those paths.
  Do not invent compatibility aliases for hypothetical consumers.
- If baseline failures prevent reliable comparison, stop and report their exact
  scope before refactoring the affected surface.
- No exploitable defect or runtime failure was established during inspection.
  The findings above describe organization/coupling, not proof of unsafe code.
