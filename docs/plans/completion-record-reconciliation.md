# Completion-record reconciliation and feature closeout — Plan

## Goal

Establish which disputed features really work, finish verified gaps, and publish one consistent, evidence-backed completion record.

**Status:** Tasks 00–02 complete; Task 03 blocked/incomplete pending owner-provided external acceptance; Task 04 complete under explicit user authorization while Task 03 remains blocked.

## Scope (README.md sections covered)

- [Telegram interface](../../README.md#интерфейс-бота), scheduling/settings, homework, and backend/bot deployment.
- Reconcile [AGENTS.md](../../AGENTS.md#feature-status-vs-product-spec) with the original plans and current refactored implementation.
- No new product feature beyond existing approved acceptance criteria.

## Evidence and boundaries

At planning time, the working tree is clean. The following conflicts were identified before execution; their current disposition is in the execution inventory below.

| Record | Conflict or unresolved check |
|---|---|
| [Scheduling/settings UI](./telegram-scheduling-and-settings-ui.md) | Header says Tasks 09–11 pending, while feature status describes vacations, settings, and integrated flows as implemented. |
| [Homework](./homework-backend-and-telegram-ui.md) | Plan says Tasks 00–11 complete; AGENTS handoff still calls Task 11 verification pending. |
| [Event-editor consistency](./telegram-event-editor-consistency.md) | Only Task 01 recorded complete; latest commits include open-ended break work. Determine actual remaining criteria, including unavailable PostgreSQL verification. |
| [Telegram auth](./TELEGRAM-AUTH-PLAN.md) | Full Compose startup and real separate-server HTTPS acceptance remain unchecked. These cannot be inferred from unit tests. |
| [Auth UX fixes](./AUTH-UX-FIXES.md) | Unchecked criteria may be historical; mobile rendering and code rotation need evidence, not guessed completion. |
| Refactoring | Old links name removed monolithic files. Use the actual paths under [bot dialogs](../../telegram/dialogs/), [bot API](../../telegram/api/), and [homework services](../../backend/homework/services/). Historical counts are not current-suite results. |

The schedule router's empty-file note is not proof of missing schedule APIs: inspect wiring in [main](../../backend/main.py) and existing schedule reads before proposing work.

## Execution inventory (2026-10-31)

| Disputed item | Evidence-backed status | Evidence and owning check |
|---|---|---|
| Scheduling/settings Tasks 09–11 | Verified; the stale status header is now reconciled. | [Scheduling plan](./telegram-scheduling-and-settings-ui.md) records completed acceptance and execution for vacations, settings, and cross-flow behavior; Task 01 rerun passed 77 focused tests across real-dispatcher/ASGI flows. Current code is under `telegram/dialogs/vacations.py`, `settings.py`, `lessons/`, and `breaks.py`. Tests cover restart/stale callbacks, permissions and uncertain writes. Live Telegram readability remains unverified; no code defect was established. |
| Homework Task 11 | Verified historically and by the reconciliation rerun; README/AGENTS handoffs now agree. | [Homework plan](./homework-backend-and-telegram-ui.md) records 366 passed/5 skipped, isolated SQLite upgrade/downgrade/re-upgrade and `alembic check`, persisted file/restart/retry/outage/ownership coverage (2026-10-26). Task 02 rerun passed 87 focused tests, including deletion revocation/retry, interrupted uploads, and expiry cleanup. Current code is under `telegram/dialogs/homework/`, `telegram/api/transfer.py`, and `backend/homework/services/`; standalone cleanup CLI model loading was fixed and verified on disposable SQLite. |
| Event-editor consistency | Open/partially verified; not a scheduling/settings closeout item. | [Original event-editor plan](./telegram-event-editor-consistency.md) records Task 01 complete on SQLite and PostgreSQL unavailable; remaining criteria are unchecked. It owns the open UI work. Current client owners are `telegram/dialogs/lessons/`, `breaks.py`, and `vacations.py`, with shared inputs in `telegram/shared/`; regression owners include `test_bot_lesson_creation.py`, `test_bot_lesson_management.py`, `test_bot_break_management.py`, `test_bot_vacation_management.py`, `test_bot_cross_flow.py`, `test_bot_runtime.py`, `test_open_ended_recurrence_schema.py`, `test_event_conflicts.py`, and `test_recurrent_event_edits.py`. Task 04 links the tracked plan without claiming its criteria complete. |
| Auth, deployment, and auth UX | Automated onboarding/code behavior and the default local Compose stack are verified; separate-server HTTPS, narrow-mobile rendering, and approved live code-rotation acceptance remain blocked/unverified. | [Auth plan](./TELEGRAM-AUTH-PLAN.md) now records the isolated PostgreSQL/backend stack, migrations, readiness, private persistent storage, graceful shutdown, and restart; its separate-server HTTPS checkbox remains unchecked. [Auth UX plan](./AUTH-UX-FIXES.md) records 30 passing tests and checked automated criteria, while mobile readability and live second-account acceptance remain unchecked. HTTPS requires an owner-approved target and local secret provisioning; mobile verification requires a real Telegram client. |
| Refactoring path references | Current package ownership is split and affected plan links now point to current paths. | Use `telegram/dialogs/`, `telegram/api/`, `backend/events/management/`, `backend/events/mutations/`, and `backend/homework/services/`; historical test counts remain dated evidence. No broad historical rewrite is warranted. |

## Common execution and validation rules

- Inspect actual code and existing tests before fixing anything. Use the original criterion, not endpoint existence as a proxy for a functioning Telegram flow.
- Run smallest relevant existing tests using isolated real databases/storage and `BotDriver`/`RecordingSession` in [bot support](../../tests/bot_support.py). Never use developer data for experiments.
- For code changes: applicable `uv run ruff check backend telegram tests`, touched-file format checks, `uv run mypy backend`, `uv run mypy telegram`, and relevant regression tests. Record unrelated baseline failures separately.
- No migration expected. A genuine schema gap requires a revised scoped task and a static reversible `YYYY-MM-DD_slug.py` migration with disposable SQLite/PostgreSQL checks.
- Never rotate real invitation codes, start a competing poller, interrupt live services, or inspect production data as part of this audit. Get explicit permission for live acceptance.

## Task 00: Build an acceptance-to-evidence inventory

**Domain:** Existing plans, code and tests. **Type:** verification/planning. **Dependencies:** None.

**Context:** Header status, checkboxes and feature summaries disagree.

**What to do:** Map every disputed criterion above to current code, focused tests and existing execution evidence. Record verified complete, implemented but unverified, missing/defective, superseded by an approved later contract, or externally blocked. Update an execution section in this plan, not a duplicate implementation backlog. Map old package paths to current paths when editing affected links. Do not blanket-check historical tasks.

**Reuse:** Original plans, [package refactoring record](./package-refactoring.md), existing test suites.

**Acceptance Criteria:** [x] Every disputed item has evidence or a named check still needed. [x] Each actual defect gets a bounded repair criterion and owning task; unrelated opportunities stay excluded.

**Execution (2026-10-31):** Inspected the current dialog, homework-service, cleanup, and invitation-code implementations and confirmed the named test files and historical plan evidence. The only confirmed discrepancy was stale completion/handoff documentation, reconciled in Task 04. No behavioral defect was established from the original status conflicts. Scheduling and homework regression checks passed; auth local checks passed, while Task 03 remains blocked on owner-provided external acceptance. Event-editor criteria remain with the linked original plan.

**Out of scope:** Broad redesign or declaring success from unchecked claims. **Suitable for:** AI agent.

## Task 01: Verify and close scheduling/settings gaps

**Domain:** Existing bot scheduling/settings and backend APIs. **Type:** bot/service/verification. **Dependencies:** 00.

**Context:** Original Tasks 09–11 are marked pending but likely implemented.

**What to do:** Exercise teacher/student vacations, saved settings, lesson/series/occurrence actions, break displacement and recovery through the real dispatcher/client/backend. Assert exact persisted changes and same-message editing. Repair only confirmed gaps in the existing contract. Delegate outstanding event-form consistency criteria to the linked continuation plan, not a competing implementation.

**Reuse:** [Scheduling UI plan](./telegram-scheduling-and-settings-ui.md), current dialogs and existing booking/bot tests.

**Acceptance Criteria:** [x] Original Tasks 09–11 are closed or individually blocked with evidence. [x] Restart, stale callbacks, permissions and uncertain writes are covered; regressions/common checks pass.

**Execution (2026-10-31):** `uv run pytest -q tests/test_bot_vacation_management.py tests/test_bot_settings_dialogs.py tests/test_bot_cross_flow.py tests/test_bot_schedule_integration.py tests/test_bot_lesson_creation.py tests/test_bot_lesson_management.py tests/test_bot_break_management.py tests/test_occurrence_actions.py tests/test_recurrent_event_edits.py tests/test_bot_runtime.py` passed (77 passed). `uv run ruff check telegram tests`, `uv run ruff format --check telegram tests`, and `uv run mypy telegram` passed. The focused real-dispatcher/ASGI tests cover creation/management, settings, vacations, break displacement, permissions, restart/stale callbacks and uncertain writes. No live Telegram readability check was performed; it was not claimed by the original Task 11.

**Out of scope:** Notifications or new scheduling rules. **Suitable for:** AI agent.

## Task 02: Verify and close homework/restart/storage gaps

**Domain:** Existing homework backend/bot. **Type:** integration/service. **Dependencies:** 00.

**Context:** Final homework verification is simultaneously marked done and pending.

**What to do:** Recheck Task 11 evidence; run focused combined teacher/student assignment, actual file transfer, versioned submission, teacher review/completion/delete, restart, interrupted upload and cleanup recovery. Inspect isolated persisted files and rows. Fix tightly scoped defects only; verify storage volume/config and cleanup command without touching live storage.

**Reuse:** [Homework plan](./homework-backend-and-telegram-ui.md), existing homework fixtures/services and Bot API recorder.

**Acceptance Criteria:** [x] Data/file persistence, access revocation and cleanup outcomes are demonstrated. [x] Task 11 and AGENTS handoff agree; no duplicate implementation of working homework.

**Execution (2026-10-31):** Focused backend/dispatcher coverage passed (87 passed) across `test_homework_*.py`, teacher/student homework dialogs, cross-flow, Telegram homework text, and package-boundary tests. Assertions cover actual bytes across bot restart, role/teacher isolation, inaccessible deleted files, retryable deletion tombstones, interrupted uploads, and expiry cleanup. An isolated SQLite database was migrated to head and `uv run python -m backend.homework.cleanup` exited successfully against a separate temporary storage path (0 failures); the cleanup-service test separately confirmed removal of an expired file and row. The first CLI attempt exposed missing SQLAlchemy model registration (`User.events` could not resolve `Event`); the CLI now imports the auth, event, and homework model modules, matching the test model-registration pattern. Ruff check, touched-file format check, `mypy backend`, and `mypy telegram` passed. Local and production Compose manifests were inspected for persistent `homework_data` mounts; no live storage was accessed.

**Out of scope:** Homework notification delivery. **Suitable for:** AI agent.

## Task 03: Close auth/deployment acceptance or record exact blockers

**Domain:** Existing onboarding, deployment and docs. **Type:** verification/deployment. **Dependencies:** 00.

**Context:** Real HTTPS, whole-stack operation and mobile UX need distinct evidence.

**What to do:** Verify auth UX/code-format criteria using existing tests and disposable data; retain manual mobile checks as manual. With configured container tooling, exercise a disposable full stack, migrations, backend readiness, private persistent storage and graceful shutdown. Do not start a live-token poller without approval. Ask for an approved HTTPS deployment target/credentials outside source control for separate-host acceptance; otherwise record the blocker and exact procedure. Never label local ASGI checks as remote deployment verification.

**Reuse:** [Auth plan](./TELEGRAM-AUTH-PLAN.md), [UX fixes](./AUTH-UX-FIXES.md), existing Compose/run documentation.

**Acceptance Criteria:** [ ] Automated/local/live results are separated. [ ] Unavailable external checks remain unchecked with owner prerequisites; genuine deployment defects have bounded fixes and checks.

**Execution (2026-10-31; external gate pending):** Auth/onboarding/code tests passed (30 passed); repository Ruff check/format and backend/Telegram mypy passed. Local and production Compose configs validated; an isolated local Compose project migrated PostgreSQL to head, served `/docs` (HTTP 200), created homework storage on the named persistent volume with mode 0700, shut down cleanly (exit 0), and recovered after restart (an immediate probe was early; a retry returned HTTP 200 and storage remained mode 0700). The disposable project/volumes were removed; the pre-existing development stack remained responsive. No bot poller or live code rotation was started. The real separate-server HTTPS check remains blocked because no approved HTTPS target or test access was provided. Exact follow-up: provide a disposable HTTPS backend target with a valid certificate and arrange test credentials through an ignored local `.env` or secret manager; then run the compatible backend and bot against that host and verify a real HTTPS auth/session flow. Narrow-screen registration review also remains a manual owner check in a real Telegram client; verify teacher and student setting labels, correct edit destinations, and same-message navigation. These checks are not claimed complete.

**Out of scope:** Unsolicited live messages, production rotation or infrastructure redesign. **Suitable for:** AI agent; human approval/access for live acceptance.

## Task 04: Publish reconciled status and handoffs

**Domain:** README, AGENTS and affected plans. **Type:** documentation. **Dependencies:** 01, 02, 03 and inventory results for event-editor work.

**Context:** Historical baselines must not be mistaken for present functionality.

**What to do:** Update statuses, checkboxes and affected relative links consistently, preserve dated historical evidence, and explicitly cross-link superseding contracts. Reference the [original event-editor plan](./telegram-event-editor-consistency.md) for still-open UI work. Add the remaining feature sequence without marking plans implemented. Check local links and whitespace.

**Reuse:** Existing status conventions and canonical `docs/plans/` location.

**Acceptance Criteria:** [x] No remaining contradictory completion claims for audited criteria. [x] A blocked check stays blocked, not complete. [x] Every follow-up has one owning plan.

**Execution (2026-10-31; completed under explicit authorization while Task 03 remains blocked):** Reconciled README/AGENTS and the scheduling, homework, authentication, and package-refactoring handoffs with the verified evidence. Updated the relevant local links to current package paths and linked open event-editor UI work to its tracked plan; remaining feature plans are listed as future work, not completed features. A local-link check across eight reconciled documents found all targets, and `git diff --check` passed. Task 03 remains blocked on owner-provided separate-server HTTPS access and manual narrow-screen Telegram review.

**Out of scope:** Rewriting all historical prose or fixing unrelated docs. **Suitable for:** AI agent.

## Open questions

- Deployment targets, live-bot permission and mobile UX access are owner-provided gates, not assumed resources.
- Execute repairs only after Task 00 establishes a real gap. This plan is not permission to rerun destructive operational procedures.
