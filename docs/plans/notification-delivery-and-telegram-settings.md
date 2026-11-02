# Reliable notifications and Telegram settings — Plan

## Goal

Deliver authorized schedule/homework notifications to Telegram reliably, with user-controlled preferences and explicit handling of delivery failures.

**Status:** Tasks 00–04 complete; Tasks 05–07 planned.

## Scope (README.md sections covered)

- [Notifications](../../README.md#уведомления): nonempty morning digest, student pre-lesson reminders, assignment-created and solution-submitted notifications.
- [Settings](../../README.md#старт): incomplete-homework lead-day reminders and persisted digest/reminder preferences.
- [Homework handoff](../../README.md#передача-для-будущих-уведомлений): committed/idempotent transitions, exact lesson association and completion provenance.
- Teacher broadcasts are a separate [plan](./teacher-broadcasts.md), reusing this delivery foundation.

## Existing foundations and dependencies

- [Notification model](../../backend/notifications/models.py) has only string receivers/message and a boolean. Inventory existing rows before any approved migration; do not silently discard or reinterpret them.
- [Auth models](../../backend/auth/models.py) already store digest local minute, student lesson-reminder minutes, homework lead days and Telegram routing. Preserve null=disabled and existing validated units.
- [Schedule service](../../backend/schedule/service.py) `get_schedule` applies viewer authorization and vacation visibility. Reuse its policy/formatting inputs and [occurrence services](../../backend/events/range_service.py); exclude automatic breaks from lesson counts/reminders.
- Homework assignment/submission use cases in [services](../../backend/homework/services/) own operation-key reconciliation and commits. New notification intents must be committed with the original domain transition, not after a response or on a replay.
- The bot is the separate Telegram client. Keep Bot API credentials and Telegram sending out of backend domain services. Backend owns durable intents/scheduling; an authenticated bot delivery worker claims work through an approved narrow service API.
- Begin after [completion reconciliation](./completion-record-reconciliation.md) establishes the current contracts. Core design is independent of UI closeout, but coordinate shared settings/client changes.

## Common acceptance criteria

- Durable queue/outbox with bounded polling, atomic claims/leases, stable dedupe keys, observable retry/terminal state and restart recovery; no `BackgroundTasks` for reliable scheduling.
- Telegram sends cannot be atomically committed with the DB. Do not promise exactly-once delivery: document the ambiguous-send policy, possible duplicates/loss and reconciliation choice.
- Recheck recipient activity, current linkage/preferences and applicable occurrence/assignment validity before dispatch; suppress deleted, completed, canceled, hidden or stale items as approved.
- Tests use real isolated SQLite/PostgreSQL and fake clocks/Bot API transport. Run focused tests plus applicable Ruff/format/mypy for backend, bot and touched tests. Migration filenames: static reversible `YYYY-MM-DD_slug.py`, both DB round trips, schema-drift and safe downgrade tests.
- No tokens, chat routing values, homework contents or complete schedules in operational logs. Domain-scoped configuration, explicit worker health/failure counters, bounded retention and documented deployment.

## Task 00: Approve scheduling, delivery and preference contract

**Domain:** Product/backend/bot contract. **Type:** planning. **Dependencies:** Reconciliation inventory.

**Context:** Units exist, but DST timing, missed deadlines and delivery semantics are unspecified.

**What to do:** Approve local digest timing on gaps/folds, homework "days" semantics (calendar days versus elapsed hours and send time), empty-digest suppression, late-start catch-up/staleness windows, reminder behavior after lesson/timezone/preference changes, terminal retry limits and no-Telegram recipients. Define new independent assignment-created/student and solution-submitted/teacher toggles, defaults and migration policy; do not repurpose homework lead-days. Define suppression scope for homework push versus lesson reminders, retention and repeated versions.

Approve backend outbox plus bot-worker trust boundary, scoped service credential (never broaden registration key access by accident), claims/acks/leases, API shapes, rate limits and ambiguous-send policy. Specify multi-worker safety, deployment topology and shutdown.

**Reuse:** Existing notification units, homework idempotency keys, stable occurrence identities and service-auth patterns.

**Acceptance Criteria:** [x] Concrete event/dedupe key, DST/catch-up examples, retry/terminal/unknown states and defaults are approved. [x] No implicit exactly-once promise or guessed scheduler infrastructure.

**Execution (2026-11-01):** The owner approved this contract:

- Digest and homework reminders use the recipient's saved local time. A nonexistent DST minute moves to the first valid minute; an ambiguous minute sends once at its first occurrence. Homework lead days are calendar days before the lesson, at digest time. Empty digests are suppressed.
- Catch-up sends digests only on the same local date, lesson/homework reminders at most 15 minutes late, and assignment/submission pushes within 24 hours. Every send revalidates current preferences and source state; moved, canceled, hidden, completed, or otherwise stale items are suppressed. Enabling a preference late does not backfill.
- Dedupe by recipient and local digest date; recipient, notification type, and exact occurrence identity/start for schedule notices; and recipient plus assignment or finalized submission version for homework pushes. Each finalized submission version is distinct; reconciled repeats are not.
- Assignment-created student pushes and solution-submitted teacher pushes have separate, enabled-by-default toggles. A missing Telegram recipient creates no intent and linking later does not backfill.
- One backend producer runner polls every 30 seconds; database-level deduplication protects against accidental duplicate runner processes.
- Assignment-created and submission-version alerts may still send after the homework becomes complete while the assignment and active teacher/student link remain valid. Incomplete-homework reminders are suppressed after completion. Deletion or loss of the active link suppresses all affected alerts.
- Store source references and render current safe text at send time. Retain sent intents 30 days and suppressed/terminal intents 90 days.
- Use a separate least-privilege notification credential, atomic bounded claims with 60-second leases, and one serial sender per bot token. Honor Telegram 429 `retry_after`; retry transient and ambiguous outcomes exponentially up to 8 attempts. Blocked/invalid routing is terminal. Ambiguous sends may duplicate; exactly-once delivery is not promised.

The reconciled completion inventory’s Tasks 00 and 04 establish the prerequisite contracts; its separate external auth acceptance remains unrelated to this task.

**Out of scope:** Implementing unresolved behavior. **Suitable for:** Human decision with agent preparation.

## Task 01: Add durable intents, delivery state and preference migrations

**Domain:** Existing notifications/auth domains; Alembic. **Type:** schema/migration. **Dependencies:** 00.

**Context:** Current boolean Notification cannot express claims, retries or deduplication.

**What to do:** Add approved per-recipient notification identity/state, scheduled time, unique dedupe key, attempt/lease fields, minimal source references and new push toggles. Separate logical occurrence/transition identity from current transport address. Define immutable versus re-rendered payload policy and existing-row treatment. Keep settings updates owner-scoped.

**Reuse:** Shared Base/timestamps, user IDs, validated settings and operation IDs.

**Acceptance Criteria:** [x] DB uniqueness blocks duplicate logical intents. [x] Preference omissions/null/defaults preserve approved compatibility. [x] Both DB migrations/schema-drift/common checks pass; downgrade refuses loss where necessary.

**Execution (2026-11-01):** Added a separate source-reference-based outbox table, preserving the legacy `notification` table and its rows, plus independent enabled-by-default student assignment and teacher submission toggles. Focused API/model and migration tests passed (18 tests with auth/smoke regressions). Full isolated SQLite and PostgreSQL migration upgrade, `alembic check`, downgrade, and re-upgrade passed. `ruff check`, `ruff format --check`, and `mypy backend` passed.

**Out of scope:** Broadcast schema or arbitrary payload storage. **Suitable for:** AI agent.

## Task 02: Implement claims, acknowledgements and bounded delivery recovery

**Domain:** Existing notifications domain plus narrow service router. **Type:** service/router. **Dependencies:** 01.

**Context:** Separate bot/backend hosts need durable transport-neutral work ownership.

**What to do:** Implement paginated atomic claims, expiring leases, recipient/source revalidation, bounded retry scheduling and typed acknowledgement of sent/retryable/terminal/unknown outcomes. Reject stale lease acknowledgements and unauthorized callers; do not expose routing through ordinary user APIs. Expose safe health/count diagnostics, not private payloads. Add a bounded producer runner entrypoint using approved scheduler configuration.

**Reuse:** Existing credential-validation conventions, database transaction patterns and async APIs.

**Acceptance Criteria:** [x] Concurrent claimers never own one active lease; crash/restart recovers as approved on both DBs. [x] 429 deadlines, poison jobs, disabled accounts and lease-expiry races have deterministic tests.

**Execution (2026-11-01):** Added a separately authenticated delivery API for bounded claim/ack and safe health reporting; claims revalidate recipient routing, account/link, preferences, current homework status and exact visible lesson occurrence. Leases recover after expiry, reject stale acknowledgements, respect Telegram retry deadlines, and transition exhausted/terminal outcomes explicitly. Added a 30-second bounded producer-runner loop. Focused notification/schema/boundary tests passed (22 passed, 2 PostgreSQL-only cases skipped in the regular run); the skipped concurrency and runner cases passed against both SQLite and an ephemeral PostgreSQL 17 database (4 passed). `ruff check backend tests`, `ruff format --check backend tests`, and `mypy backend` passed.

**Out of scope:** Telegram calls inside FastAPI or a new general job framework. **Suitable for:** AI agent.

## Task 03: Produce daily digests and pre-lesson reminders

**Domain:** Notifications/schedule services. **Type:** service/scheduler. **Dependencies:** 02.

**Context:** A server-time cron or infinite recurrence expansion would give wrong local schedules.

**What to do:** Use bounded time windows and approved DST/catch-up rules to create due teacher/student digests and student pre-lesson reminders. Compose authorized viewer-local reads with exact raw occurrence/cancellation/visibility policies. Include student homework status in digests as required, using bounded bulk queries; include student names for teacher lessons only. Revalidate near dispatch and avoid duplicate intent when preferences or poll windows change.

**Reuse:** `get_schedule`, local-day bounds, occurrence identity and homework completion reads.

**Acceptance Criteria:** [x] Empty day sends nothing; teacher/student contents remain isolated. [x] Exact UTC tests cover both DST transitions, midnight, open-ended series, cancellation/move/vacation, disabled prefs and worker restart. [x] No auto-break reminder or unbounded per-user expansion.

**Execution (2026-11-02):** Added local-day digest and exact occurrence reminder producers using authorized viewer schedules, first-fold/gap-shifted local clock resolution, same-day digest catch-up, 15-minute reminder catch-up, stable dedupe keys, and conflict-safe insertion. Added separate persisted change timestamps for digest/routing and lesson-reminder preferences so unrelated profile edits do not suppress valid catch-up; the reversible schema migration backfills existing users. Student digest context bulk-loads only the viewer's current homework statuses for visible lesson occurrences. Tests cover empty days, local DST gap/fold, midnight, open-ended recurrences, cancellations, vacations, moved lessons, disabled/late settings and routing, automatic-break exclusion, restart deduplication, and student-only homework context. Focused SQLite/PostgreSQL/schedule/time tests and SQLite/PostgreSQL migration round trips/schema checks passed. `ruff check backend tests`, `ruff format --check backend tests`, and `mypy backend` passed.

**Out of scope:** Broadcasts. **Suitable for:** AI agent.

## Task 04: Produce homework pushes and incomplete-work reminders

**Domain:** Existing homework use cases and notifications. **Type:** service. **Dependencies:** 02, 03.

**Context:** Assignment creation and finalized solution versions already have idempotent committed transitions.

**What to do:** Insert approved intent transactionally on assignment publication and new solution version finalization; reconciled repeats do not emit again. Add bounded due incomplete-work reminders using lead days and exact active visible upcoming lesson identity. Respect teacher-complete versus student-submitted provenance, current linkage, deletion and settings. Do not move homework associations when lessons move or infer new links.

**Reuse:** `create_assignment`, `create_submission`, existing completion/operation reconciliation and occurrence validation.

**Acceptance Criteria:** [x] Rollback emits no intent; replay emits none extra; new solution version follows approved notification policy. [x] Complete/deleted/de-linked or canceled/hidden linked lessons suppress reminders. [x] File cleanup/submission regressions and common checks pass.

**Execution (2026-11-03):** Assignment publication and each newly finalized submission version now add their push intent within the same commit, only when the approved toggle and Telegram route are available. Idempotent reconciliation paths do not add another intent. Homework reminders use calendar lead days and the recipient's digest time, resolve DST with the approved gap/fold policy, catch up for at most 15 minutes, and are created only for exact active visible lesson occurrences with incomplete, nondeleted homework and unchanged preferences/routing. Dispatch revalidates the current lead/time settings, active link, completion/deletion state, and exact occurrence. Submission finalization now conditionally locks the still-active homework row before persisting the submission, preventing concurrent deletion from leaving orphaned submissions. Added a reversible preference-change timestamp migration.

The full repository suite passed (518 passed, 9 skipped); focused PostgreSQL producer tests passed (10 passed). Full SQLite and PostgreSQL migration chains passed upgrade, `alembic check`, one-step downgrade, re-upgrade, and final schema check. `ruff check backend tests`, `ruff format --check backend tests`, and `mypy backend` passed.

**Out of scope:** Adding homework features or delivering file bytes in push text. **Suitable for:** AI agent.

## Task 05: Add the bot delivery worker and deployment wiring

**Domain:** Top-level Telegram client and deployment. **Type:** bot/worker. **Dependencies:** 02.

**Context:** Polling updates and scheduled sends must share rate limits without starting duplicate pollers.

**What to do:** Implement typed claim/ack transport and bounded Telegram sender according to Task 00 topology. Reuse one lifecycle-managed Bot session where appropriate, handle 429 retry-after, blocked chat and invalid routing distinctly, and apply agreed unknown-send behavior. Keep backend outages from crashing update handling; verify worker readiness, graceful shutdown, lease recovery and secret separation. Document one-poller rule independently of delivery-worker replicas.

**Reuse:** Existing bot HTTP/config/runtime lifecycle and recording Bot API transport; no backend imports.

**Acceptance Criteria:** [x] Sends/acks/retries are measured under failures and restart. [x] Empty queue and shutdown do not busy-loop or drop unacknowledged claims. [x] Worker is responsive in approved disposable startup checks; no live unsolicited messages.

**Out of scope:** Webhooks or scaling polling bots. **Suitable for:** AI agent.

**Execution (2026-11-03):** Added a separately typed notification claim/ack client using `X-Notification-Service-Key`; bot startup rejects reuse of the registration credential. The serial sender runs beside the sole poller and shares a 25-request/second outbound Bot-session limiter. It maps Telegram 429s to bounded retry deadlines, blocked/invalid chats to distinct terminal outcomes, and network/server ambiguity to `unknown`; backend errors back off without terminating polling. Empty claims wait five seconds, readiness follows successful claims, and shutdown leaves unacknowledged leases for expiry recovery. Compose now starts the 30-second backend producer only after the backend health endpoint is ready; bot delivery does not start another poller.

Focused worker/backend tests passed (36 passed, 2 skipped), and the full repository suite passed (534 passed, 9 skipped; the PostgreSQL-only tests were not run because `SCHEDULE_TEST_DATABASE_URL` was unset). `ruff check backend telegram tests`, `ruff format --check backend telegram tests`, and `mypy backend telegram` passed. Both Compose manifests validated. A disposable PostgreSQL Compose stack reached healthy app/producer states and was stopped/removed; the bot was exercised with the recording Bot API transport and no live Telegram messages were sent.

## Task 06: Expose Telegram settings and safe notification text

**Domain:** Existing settings dialogs, API models and notification formatting. **Type:** bot. **Dependencies:** 01, 03, 04, 05.

**Context:** Existing settings collect preferences but currently say delivery is unavailable.

**What to do:** Preserve current digest/time, explicit lesson hours/minutes and homework-days editors; add only approved separate push toggles in both profiles. Explain disabled/unsendable settings accurately. Format outgoing text with escaped names/homework excerpts and complete safe pagination/length bounds; do not route notifications through active dialog edits or corrupt an in-progress wizard. If navigation links are included, authorize on opening and recover expired context.

**Reuse:** Existing Settings, shared feedback/input utilities and schedule formatting conventions.

**Acceptance Criteria:** [ ] Both roles can save approved toggles without overwriting other fields. [ ] Notification delivery during an active dialog leaves drafts and callback state intact; sent text is length-safe and authorized. [ ] Existing settings/bot tests pass.

**Out of scope:** New channels, free-form quiet-hour settings or notification history UI. **Suitable for:** AI agent.

## Task 07: Verify release and publish delivery handoff

**Domain:** Tests/docs/deployment. **Type:** integration. **Dependencies:** 03, 04, 05, 06.

**Context:** Green producer tests alone do not prove delivery across processes.

**What to do:** Exercise preferences → due intent → claim → actual recorded Telegram send → acknowledgement, including downtime, repeats, concurrent workers, linkage changes and unknown outcomes. Verify both DBs and disposable worker deployment. Update README/AGENTS, migration/run/monitoring instructions and broadcast integration contract.

**Reuse:** Existing dispatcher/ASGI fixtures, fake clocks and isolated homework storage.

**Acceptance Criteria:** [ ] All five notification types have exact recipient/content/time assertions. [ ] Claimed guarantees and known duplicate/loss windows match tested behavior. [ ] Broadcast can reuse public domain operations without its own sender.

**Out of scope:** HTMX or real-user messaging without consent. **Suitable for:** AI agent; human for approved live acceptance.

## Open questions

Task 00 is a required approval gate. Use the simplest bounded worker solution meeting durability requirements; introducing Celery/Redis or another scheduler is not pre-approved.
