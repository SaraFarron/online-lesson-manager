# Teacher broadcasts through Telegram — Plan

## Goal

A teacher can compose, review and confirm a one-time message to their own students, with durable delivery and honest per-recipient outcome reporting.

**Status:** In progress — Tasks 00–04 complete.

## Scope (README.md sections covered)

[Teacher notification](../../README.md#уведомления): one-time broadcast to all the teacher's students; Telegram composition, confirmation and results. No scheduled campaigns or arbitrary recipient selection.

## Foundations and execution order

- Implement after the core [notification delivery plan](./notification-delivery-and-telegram-settings.md), particularly its durable intents, leases, acknowledgements and bot worker. Do not build a parallel sender.
- [Auth roster service](../../backend/auth/service.py) `get_teacher_students` already filters active, linked students with pagination. A paginated UI roster is not an authoritative fan-out snapshot.
- Reuse existing [bot API](../../telegram/api/), role home, escaped leading feedback and uncertain-write operation-key patterns. Ordinary profile owner checks must not be relaxed.
- No broadcast implementation currently exists. Text-only is the smallest scope satisfying README; attachments are not assumed.

## Common acceptance criteria

- JWT-scoped teacher operations; no global user list or client-supplied teacher identity. Bot callbacks never grant access.
- Transactionally persist request, recipient decision and deduplicated delivery intents, or use approved recoverable fan-out with visible incomplete state. Unknown publish responses reconcile via the same operation key; do not resend to everyone.
- Revalidate active linkage/preferences at dispatch using the notification foundation. Existing possible Telegram send/ack duplicates remain documented; no exactly-once claim.
- Real isolated DB and recording Bot API tests, focused Ruff/format/mypy for touched backend/bot, existing auth/notification regressions. Schema changes require static reversible `YYYY-MM-DD_slug.py` migrations and both DB checks.

## Task 00: Approve broadcast confirmation and recipient contract

**Domain:** Product/backend/bot. **Type:** planning. **Dependencies:** Approved notification contract.

**Context:** README does not define recipient snapshot timing, opt-outs, text limits or result visibility.

**What to do:** Approve text length/format, all-student scope, treatment of inactive/unroutable students, and whether broadcasts have an independent opt-out (do not reuse lesson/homework toggles). Define snapshot-at-confirm versus at-queue behavior, roster-change handling before sending, operation-key retention, request limits and outcome categories. Recommend confirm text plus eligible/unsendable counts, no message send on preview, and per-recipient suppression after unlinking.

**Reuse:** Existing roster and notification worker policy.

**Acceptance Criteria:** [x] Counts/eligibility, confirmation freshness and retry semantics are approved with examples. [x] No new setting was chosen, so no preference migration/UI task is required.

**Out of scope:** Unapproved marketing features, attachments or schedules. **Suitable for:** Human with agent preparation.

**Execution (2026-11-02):** The owner approved this contract:

- Broadcasts are plain-text, 1–4096 characters. Preview sends nothing and
  reports active linked students with routable Telegram chats separately from
  unsendable students.
- Confirmation binds the exact text and current roster. Any content or roster
  change before confirmation requires a fresh preview. Publish snapshots every
  active linked student at confirmation; students linked later are excluded.
- No independent broadcast opt-out is added and lesson/homework preferences
  are not reused. At claim time, current activity, linkage and routing are
  revalidated; invalid recipients are suppressed.
- A durable per-teacher operation key is retained indefinitely for safe
  reconciliation. A deliberate new broadcast requires a fresh preview and key.
  Fan-out uses bounded backend pagination and existing worker limits without
  truncating the roster.
- Results use `pending`, `retrying`, `sent`, `suppressed`, `failed`, and
  `unknown`. Retries use the existing notification worker policy; ambiguous
  sends can duplicate, so exactly-once delivery is not promised.

## Task 01: Persist broadcasts and expose preview/publish/reconciliation

**Domain:** Existing notifications domain plus auth setting if approved. **Type:** schema/service/router. **Dependencies:** 00; notification schema/claim API.

**Context:** The teacher needs one durable publication identity, not a series of client-side student sends.

**What to do:** Add minimal broadcast metadata/recipient relationships and operation-key uniqueness. Add authenticated preview and confirmation-bound publish with freshness validation, bounded recipients and idempotent reconciliation. Bind preview to reviewed content and approved roster/count semantics; changed input cannot reuse confirmation blindly. Enqueue through notification domain helpers without sending in the API transaction. Use explicit error/status shapes.

**Reuse:** `get_teacher_students`, notification intent creation and operation-key conventions.

**Acceptance Criteria:** [x] Preview creates no delivery. [x] Confirm includes all approved eligible students once across roster pages; duplicate/concurrent/lost-response requests yield one publication. [x] Cross-teacher/student calls fail; migrations/common checks pass.

**Out of scope:** Another durable queue or direct Telegram calls. **Suitable for:** AI agent.

**Execution (2026-11-02):** Added teacher-owned preview, publish and
operation-key reconciliation. Preview tokens bind the text and current paged
roster; publication and all recipient intents commit atomically through the
existing notification queue. Verified 105-recipient fan-out, stale previews,
authorization, repeat/lost-response reconciliation, and concurrent publish on
PostgreSQL. SQLite and PostgreSQL migration upgrade/downgrade checks passed;
focused notification regressions, Ruff, and backend/bot mypy passed.

## Task 02: Add delivery eligibility and teacher-scoped result reads

**Domain:** Notifications domain. **Type:** service/router. **Dependencies:** 01; notification worker.

**Context:** A successful enqueue is not successful delivery.

**What to do:** Integrate broadcast intents with rate-limited dispatch and revalidation. Expose bounded teacher-owned aggregate/per-recipient results using sent, pending, retrying, suppressed, failed and unknown as approved. Do not translate unknown into failed or silently retry already sent recipients. Avoid exposing transport credentials/routing IDs.

**Reuse:** Existing notification claim/ack/retry services and minimal roster display labels.

**Acceptance Criteria:** [x] Blocked chats/429s, worker crash, unlink and empty roster have correct counts and states. [x] Another teacher cannot inspect recipient results; common checks pass.

**Out of scope:** Read receipts or promises of human delivery. **Suitable for:** AI agent.

**Execution:** Added teacher-owned, bounded result pages with aggregate counts
and minimal recipient labels. Retryable sends report `retrying`; ambiguous
sends remain `unknown`, including exhausted ambiguous attempts. Verified
delivery acknowledgements, suppression after unlinking, empty rosters,
cross-teacher denial, and paging. Focused notification regressions passed
(42 passed, 3 environment-dependent skips); Ruff and backend mypy passed.

## Task 03: Add typed bot broadcast operations

**Domain:** Existing Telegram API package. **Type:** bot/client. **Dependencies:** 01, 02.

**Context:** Publication needs preview tokens and safe unknown-outcome reconciliation.

**What to do:** Add strict preview, publish and result models and methods, safe auth renewal, operation-key retention and typed stale-confirmation errors. Keep receiver enumeration/fan-out in backend. An uncertain publish must reconcile before another publication is allowed.

**Reuse:** Existing write/204/error/circuit breaker and homework operation patterns.

**Acceptance Criteria:** [x] Auth expiry, malformed results, timeout-after-commit, repeated keys and stale previews have explicit safe outcomes. [x] Bot mypy and client tests pass.

**Out of scope:** Bot-side durable sending/fan-out. **Suitable for:** AI agent.

**Execution:** Added strict typed preview, publish, reconciliation and paginated
result calls, including the broadcast notification type. Expired-token writes
renew once; stale previews raise a typed error. Ambiguous publishes retain the
same key and payload, reconcile before retry, and block a different operation
until resolved. Verified stale previews, timeout-after-commit reconciliation,
malformed result rejection, and authentication renewal. Bot client/worker tests
passed (53 passed); Ruff, formatting and bot mypy passed.

## Task 04: Wire compose, review, confirm and results

**Domain:** Telegram teacher home/settings/dialogs. **Type:** bot. **Dependencies:** 03.

**Context:** A backend broadcast API alone does not satisfy the feature.

**What to do:** Add teacher-only Broadcast entry, text input, escaped length-safe preview, recipient counts, Back/Cancel, explicit Send and result screen with reload. Clearly distinguish queued from sent. Restore drafts on correctable failures, reject stale callbacks and reconcile uncertainty. Add student opt-out UI only if approved, with owner-safe partial updates.

**Reuse:** Existing dialog pagination, `ShowMode.EDIT`, leading feedback and private-chat isolation.

**Acceptance Criteria:** [x] Cancel/preview sends no broadcast. [x] Confirmation enqueues once; result screen matches recorded deliveries and does not mutate active student dialogs. [x] Both roles see only approved actions; common bot checks pass.

**Out of scope:** Campaign editing or recall after send. **Suitable for:** AI agent.

**Execution:** Added a teacher-only composition and confirmation flow with
escaped, UTF-16-safe paginated previews, roster counts, explicit confirmation,
and bounded result paging/refresh. Preview and cancel leave publication and
delivery tables untouched. A lost publish response is reconciled by the same
operation key; stale previews return to the editable draft, and unresolved
operations retain their original key and request. Verified through the real
backend and recording Bot API, including student-role visibility, a
4096-character escaped preview, roster changes, 12-recipient pagination, and
recorded delivery refresh. Dialog/client tests passed (40 passed); Ruff,
formatting, and bot mypy passed.

## Task 05: Verify end to end and document limits

**Domain:** Tests/docs. **Type:** integration. **Dependencies:** 02, 04.

**Context:** Test all-student scope through backend and Telegram, not mocked fan-out.

**What to do:** Exercise teacher/two students/unrelated teacher with a roster spanning pages, unavailable backend, changed eligibility, blocked chat and restart. Assert exact queue rows, recorded sends and aggregate results. Update README/AGENTS and worker operations guidance.

**Reuse:** Notification worker tests and real dispatcher/ASGI fixtures.

**Acceptance Criteria:** [ ] No unauthorized recipient or duplicate publication; visible partial failure remains recoverable. [ ] Common checks pass with guarantees/limits recorded.

**Out of scope:** HTMX and unrelated notification types. **Suitable for:** AI agent; human for opt-in live acceptance.

## Open questions

Task 00 decisions require approval. Public broadcast API requests must remain independent of Bot API routing; recipient changes during delivery follow the notification foundation rather than inventing a second policy.
