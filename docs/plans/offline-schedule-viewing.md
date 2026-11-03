# Read-only offline schedule viewing in Telegram — Plan

## Goal

Previously authorized users can view bounded, clearly stale schedule snapshots during backend availability failures without enabling offline mutations or leaking another user's calendar.

**Status:** Tasks 00–02 implemented and verified; Tasks 03–05 pending.

## Scope (README.md sections covered)

[Architecture](../../README.md#архитектура): separate bot/backend hosts, read-only cache and circuit-breaker recovery; [schedule viewing](../../README.md#просмотр-расписания): Today/week and week navigation through Telegram.

## Existing foundations and limitations

- [BackendClient](../../telegram/api/client.py) uses shared HTTP, user JWT cache and [circuit breaker](../../telegram/api/circuit_breaker.py); there is no offline schedule store.
- [Schedule dialog](../../telegram/dialogs/schedule/dialog.py) currently shows unavailable on `BackendUnavailable`. Home/restart routing also depends on backend account reads, so merely caching `_load_schedule` will not make offline viewing reachable.
- [Backend schedule service](../../backend/schedule/service.py) authorizes the viewer, filters vacations/cancellations and returns a viewer-shaped snapshot. Cache only that result, never raw teacher occurrence data for student use.
- Current UI has no Refresh control; recovery uses home/reopen or period/week switch. Preserve that approved contract unless this plan's Task 00 explicitly changes it.
- Cached `past/ongoing/upcoming` statuses and NOW markers are from `generated_at`, not necessarily now. Label snapshot semantics; do not re-present an old divider as live.
- No cache may provide homework/files, management details, account editing, booking or mutation replay. Existing JWT-cache memory state is not permission for new offline access.

## Common acceptance criteria

- Strict keying by backend environment, trusted sender/account identity, role, linkage/authorization generation, saved viewer timezone, period and actual local range. Never look up by chat ID alone, and never swap teacher/student shapes.
- Live success is authoritative. Only approved transport/5xx/circuit availability failures can use cache; definitive 400/401/403/404/409/422, schema/format errors and inactive/de-linked accounts must not silently become cached success.
- A remote revocation during outage cannot be known immediately. Approve an absolute authorization freshness bound; do not claim perfect immediate revocation offline. On next successful contact honor invalidation before serving.
- Real isolated DB for backend contract changes, fake clock and recording transport for bot. Applicable Ruff/format/mypy and targeted auth/schedule/circuit/restart regressions. Schema changes only if justified, with static reversible `YYYY-MM-DD_slug.py` migrations and both DB verification.

## Task 00: Approve cache lifetime, identity and offline access policy

**Domain:** Product/backend/bot architecture. **Type:** planning. **Dependencies:** Reconciliation inventory.

**Context:** Stale schedule availability and immediate revocation cannot both be guaranteed during a network partition.

**What to do:** Approve absolute snapshot age and authorization age, memory-only versus persisted store, restart availability, storage/retention bounds, and cached periods (recommend only successfully fetched day/week ranges, no speculative teacher-wide prefetch). Define offline `/start`/home behavior for known versus unknown users, trusted Telegram sender re-identification and restricted navigation. Decide stale NOW/status rendering and whether a manually approved Retry control is wanted.

Specify revocation/role/link/timezone change behavior, cache invalidation generation, definitive auth failure versus JWT-renewal availability failure, and safe no-Telegram/private-chat handling. Approve the remaining risk of stale access within the freshness bound or choose no offline access after auth expires. Offline access is never registration/login success.

**Reuse:** Existing schedule contract, sender extraction, API failure classification and circuit breaker.

**Acceptance Criteria:** [x] Concrete expiration examples and offline screen map are approved. [x] Restart/storage choice and revocation limitations are explicit. [x] Backend fields/API changes needed for authorization generation have a bounded schema decision before Task 01.

**Approved policy (2026-11-03):**

- An identity is eligible only when its last successful active-user
  `GET /api/v1/auth/me` check is less than 24 hours old. For example, a check
  at 2026-11-03 12:00 UTC expires at 2026-11-04 12:00 UTC; it is not valid at
  or after the expiry. A schedule snapshot is eligible only while less than
  one hour has elapsed since its successful fetch, so a snapshot fetched at
  12:00 UTC expires at 13:00 UTC. Both limits must hold.
- Keep snapshots and the last authorized account identity in memory only. A
  process restart clears both, so `/start` cannot establish offline access
  after restart. Cache only successfully fetched day/week ranges; do not
  prefetch. Retain at most the latest one day and one week snapshot per
  account, with a 1,000-snapshot global LRU cap. Eviction means unavailable
  for that range.
- Offline `/start` may enter a schedule-only view for a trusted private-chat
  sender matching an unexpired prior identity. Unknown or expired identities,
  and uncached/expired ranges, remain unavailable; offline access is never
  login, registration, or profile success. Permit navigation only to an
  exact cached day/week range. Do not expose home actions, settings, homework,
  mutations, or callback paths that invoke them.
- Show an explicit offline label and the snapshot time in the viewer's saved
  timezone. Mark event statuses as applying "as of" that snapshot and do not
  render an old NOW marker as live. A prior local-day snapshot is not the
  current Today view after local midnight. Do not add a Retry button; retain
  the existing reopen/switch-period/week recovery flow.
- Permit fallback only for transport failures, HTTP 5xx, or an open circuit,
  and only while both freshness limits hold. No 4xx or malformed/invalid
  response may fall back. Definitive authentication, account, role, or
  relationship access failures suppress and evict the affected identity's
  snapshots. On successful live account validation, immediately invalidate
  old snapshots if account id, role, teacher link, timezone, or backend
  environment changed. Revalidate the account before accepting a new live
  schedule snapshot. A remote revocation during a partition remains possible
  within the 24-hour authorization window and must be documented as such.
- No backend schema/API change or migration is needed for identity generation:
  the existing authenticated `/api/v1/auth/me` response includes account id,
  active state, role, teacher link, and timezone. Its active-user dependency
  rejects inactive accounts. Use those fields plus the trusted Telegram
  sender and backend origin/environment for cache identity; do not persist
  JWTs or service credentials in snapshot storage.

**Out of scope:** Offline booking or silently choosing permanent caching. **Suitable for:** Human/product owner with agent preparation.

## Task 01: Add validated snapshot storage and invalidation identity

**Domain:** Bot API/cache helpers; backend self/schedule contract only if approved. **Type:** service/client. **Dependencies:** 00.

**Context:** Cache entries must preserve who was authorized and which exact local range was returned.

**What to do:** Implement bounded cache with approved lifetime/retention and strict snapshot/key validation. Store viewer-shaped typed data plus fetched/auth timestamps and policy identity; never store user JWTs or service credentials as durable snapshot content. Prevent older racing responses overwriting newer snapshots. If persisted storage is approved, implement safe permissions, atomic writes, versioning, corruption handling and environment namespace separation; otherwise explicitly keep restart cache empty.

Implement only the minimal approved backend authorization/version metadata, if needed, and invalidate after observed role/link/timezone/account changes. Deletion of corrupt entries must be explicit/logged safely, not treated as an empty successful schedule.

**Reuse:** Existing strict `ScheduleResponse`/account models and local date boundaries.

**Acceptance Criteria:** [x] Key collision, expiry boundary, wall-clock changes, monotonic rollback, LRU/bounds and racing-response tests pass; persistent-store corruption handling is not applicable because storage is memory-only. [x] No cross-account/teacher/role/timezone reuse; schema/migration checks pass if applicable.

**Execution (2026-11-03):** Added a bounded in-memory cache keyed by backend namespace, trusted sender, account, role, teacher link, timezone, period and actual local range. Authorization and snapshot freshness use monotonic time; identity changes, environment changes, invalidation and out-of-order results are fenced. The cache keeps one day and one week per identity and a global LRU bound; it revalidates typed responses and stores no credentials. `uv run pytest -q tests/test_telegram_schedule_cache.py` passed (14 tests); Ruff check/format and `uv run mypy telegram` passed. No backend schema or migration was needed. Persistence corruption/restart checks do not apply; restart empties this in-memory cache.

**Out of scope:** Shared Redis/platform abstraction unless approved. **Suitable for:** AI agent.

## Task 02: Integrate live-first reads and precise failure fallback

**Domain:** Existing bot API and schedule read orchestration. **Type:** bot/client. **Dependencies:** 01.

**Context:** `BackendUnavailable` can include invalid response parsing, not just a transport outage; indiscriminate catching would hide real defects.

**What to do:** Introduce typed availability-versus-contract-failure classification as needed without weakening circuit-breaker behavior. Cache only fully validated successful authorized reads. Fall back only for approved failures and a valid authorization/snapshot window; purge/suppress after definitive access errors. Return explicit live/cached source metadata to UI, not an ordinary fresh `ScheduleResponse` disguised as success.

**Reuse:** `BackendClient.read`, `read_schedule_day/week`, safe token renewal and circuit breaker; preserve write uncertainty classes.

**Acceptance Criteria:** [x] Transport/5xx/open-circuit cases can use eligible cache. [x] Permission/validation/malformed response failures never do. [x] Cache miss/expiry gives an explicit unavailable result and live recovery replaces stale data.

**Execution (2026-11-03):** Added strict account/schedule response classification and a live-first schedule-view API that revalidates the active account before each read, returns explicit live/cached source metadata, caches only authorized typed schedule responses, and falls back only on transport/5xx/open-circuit availability. Definitive access failures evict the sender's cache; existing live-only day/week APIs remain unchanged for non-view consumers. `uv run pytest -q tests/test_telegram_schedule_fallback.py tests/test_bot_client.py tests/test_bot_schedule_dialogs.py tests/test_bot_schedule_integration.py tests/test_bot_homework_creation.py tests/test_bot_onboarding.py tests/test_bot_runtime.py` passed (84 tests); Ruff check/format and `uv run mypy telegram` passed.

**Out of scope:** Changing mutation retry behavior or hiding backend schema bugs. **Suitable for:** AI agent.

## Task 03: Make offline schedule entry reachable without offline login

**Domain:** Telegram runtime/home/access helpers and schedule dialogs. **Type:** bot. **Dependencies:** 02.

**Context:** A known user's `/start` currently fails before reaching schedule when backend account reads are unavailable.

**What to do:** Implement Task 00's restricted offline entry using trusted current Telegram sender plus a still-valid previously authorized cache identity. Show only cached schedule navigation and availability status. Unknown/expired users stay unavailable; do not synthesize account/JWT/profile success. Preserve normal recovery for completed onboarding and reset incomplete drafts as before.

Disable all mutation, homework and Settings actions in offline mode even when old callbacks arrive. Return to normal role home only after authoritative account validation.

**Reuse:** Existing `/start`, private-chat filters, per-user isolation, stale callback handling and access helpers.

**Acceptance Criteria:** [ ] Known/unknown/expired/restarted-user cases match policy. [ ] Forged stale callbacks cannot invoke offline mutations or another account's cache. [ ] Home/onboarding regressions pass.

**Out of scope:** Offline registration or role switching. **Suitable for:** AI agent.

## Task 04: Render stale schedules and safe cached-period navigation

**Domain:** Existing schedule formatter/dialogs. **Type:** bot. **Dependencies:** 02, 03.

**Context:** Offline content must be recognizable as a dated snapshot, not an empty or fresh response.

**What to do:** Lead with an explicit stale/offline label, snapshot local date/time and approved status/NOW semantics. Support only cached Today/week/ranges; at local midnight do not label yesterday's day snapshot Today. Uncached previous/next weeks show a specific unavailable message without substituting another week. Keep overflow pagination length-safe after adding warnings and preserve active-message edits and approved recovery controls.

**Reuse:** Existing formatting/page-length bounds, period/week navigation and leading feedback.

**Acceptance Criteria:** [ ] Both roles see correct stale labels/authorized rows, DST folds and cross-midnight dates. [ ] Long schedules retain all rows; no stale NOW marker presented as current. [ ] Reconnect refreshes the selected range and normal home.

**Out of scope:** Arbitrary-date UI or background prefetch beyond approval. **Suitable for:** AI agent.

## Task 05: Verify outage, revocation and restart behavior end to end

**Domain:** Tests/docs/deployment. **Type:** integration. **Dependencies:** 01–04.

**Context:** A cache-hit unit test does not prove users can safely reach offline schedules.

**What to do:** Use real authorized backend responses to populate cache, inject outages without interrupting live services, navigate as both roles, expire auth/snapshots, reconnect after teacher removal/timezone/role changes and test approved restart behavior. Verify definitive failures evict/suppress snapshots, physical retention/bounds where applicable and environment separation. Update README/AGENTS with actual guarantees and privacy trade-off.

**Reuse:** Existing schedule/dispatcher/circuit fixtures and student-removal revalidation once implemented.

**Acceptance Criteria:** [ ] Exact recipient/range/source/timestamp/UI behavior is asserted through the dispatcher. [ ] No cached file/mutation/account-success path exists. [ ] Operational cleanup and remaining revocation window are documented; common checks pass.

**Out of scope:** HTMX or generic offline client synchronization. **Suitable for:** AI agent; human for approved live outage-free UX demonstration.

## Open questions

Task 00's freshness/storage/revocation choices are hard gates. If approved privacy requirements forbid stale authorization, document the resulting availability limit rather than circumventing it with unbounded cached access.
