# Teacher student profiles, activity history and removal — Plan

## Goal

Teachers can view their own students' identity, association date, homework status and last ten actions, then explicitly confirm safe student removal from Telegram.

**Status:** Policy approved 2026-11-03; implementation in progress.

## Scope (README.md sections covered)

[Student profile](../../README.md#профиль-ученика): username/display name, date added, homework status, ten recent actions, confirmed removal. Backend ownership, history persistence and Telegram UI are included.

## Existing foundations and gaps

- [User model](../../backend/auth/models.py) has full name, account timestamp, teacher linkage and Telegram ID/chat routing, but no Telegram username or teacher-link-added timestamp. Account creation is not necessarily "date added to this teacher".
- [Trusted Telegram sender schemas](../../backend/telegram/schemas.py) currently assert numeric sender/chat identity, not a stored username. A username must be sourced from actual sender metadata and never used as account identity.
- `get_teacher_students` in [auth service](../../backend/auth/service.py) supplies an authorized active roster; [owner-profile dependencies](../../backend/auth/dependencies.py) intentionally disallow teachers reading arbitrary owner settings.
- Homework status/provenance and recoverable file cleanup exist in [homework services](../../backend/homework/services/). Reuse them rather than rebuilding completion logic.
- Existing user deletion is an admin operation, not a teacher removal contract. Many events/homework/attachments reference users. Do not call generic deletion from a new teacher button.
- Notification/caching plans must revalidate/invalidate on removal. Coordinate with [notifications](./notification-delivery-and-telegram-settings.md) and [offline schedules](./offline-schedule-viewing.md); do not delay correctness until those features ship.

## Common acceptance criteria

- Teacher-owned profile read must be a dedicated minimal projection, not permission to read student's credentials, routing IDs or private settings.
- History is immutable scoped metadata recorded with successful domain mutations; no password/token/file-content snapshots. No fictional historical backfill.
- All removal effects follow an approved transaction/cleanup contract with lock ordering, stale confirmation and repeat/unknown outcome protection. Never cascade-delete user data speculatively.
- Use real isolated DB/storage and dispatcher/recording transport. Run focused tests plus applicable Ruff/format/mypy and auth/scheduling/homework regressions. Static reversible `YYYY-MM-DD_slug.py` migrations, both DB round trips and documented safe downgrade.

## Approved policy (2026-11-03)

Removal detaches a student from the current teacher only. It leaves the account
active and does not globally delete the student. The student may later join any
active teacher through the normal invitation flow. A rejoin starts a new
association date and history scope; records from an earlier relationship remain
scoped to the original teacher and student and never transfer to a new teacher.

| Data/effect | Approved behavior |
|---|---|
| One-off lessons | At the removal transaction's UTC commit time, delete lessons whose end is later than the cutoff, including lessons in progress. Retain lessons that have ended by the cutoff. The former teacher may view retained lessons; the detached student and other teachers may not. |
| Recurring lessons | Preserve occurrences ending at or before the cutoff and stop later occurrences. Retain the series only when it has a past occurrence to preserve, truncating it to the last such occurrence. If there is no past occurrence, remove the series. Remove cancellation rows after the cutoff; preserve earlier cancellation rows. |
| Homework | Retain assignments, submissions, completion state, and committed files. The student and original teacher retain access; another teacher does not gain access, including after rejoin. |
| Staged homework uploads | Revoke uncommitted staged uploads at removal and queue their files for existing recoverable cleanup. Do not remove committed homework files. |
| Notifications | Cancel unsent notifications for the removed teacher/student relationship and retain delivered notification history. Revalidate recipients before sending. |
| Schedule caches | Invalidate affected cached views on removal and require authorization before rebuilding them. No offline schedule cache currently exists. |
| Account and association | Clear the current teacher association only. Keep the student active and permit ordinary rejoin to any active teacher. |

Keep one relationship record per link, with its teacher, student, link time,
and optional removal time. Removal closes that relationship; rejoin creates a
new one. Current `User.teacher_id` remains the active-link authority. Seed
legacy active relationships without a link time and show “Unknown”; do not
reconstruct closed relationships that are not evidenced by stored data.
Teachers can reopen closed relationships from a read-only “Former students”
list. A former teacher may read that relationship's profile, history, retained
homework and past lessons, but cannot act on the former student through it.

Action history is teacher-visible only. Record every successful,
student-initiated state change while the student is linked to that teacher,
including unrelated account/settings and credential changes. Do not record
reads, sign-ins, token/session operations, failed or rolled-back mutations, or
secret/private value snapshots. Each immutable entry contains action kind,
actor/subject, relationship scope, occurrence time, and a stable resource
reference only. Retain entries indefinitely with the associated records, order
by occurrence time descending with a stable ID tie-break, and show a generic
“item no longer available” label when a referenced resource is gone. A teacher
can read their own relationship's history after detachment; a student and a
new teacher cannot read that teacher-only profile/history, and no new history
is attributed to an old teacher while the student is unlinked. History and
other records never transfer to a new teacher.

For profile identity, record the time of each successful link. Rejoin gets a
new date; legacy links without a demonstrable link time display “Unknown” and
are not backfilled from account creation. Store only the latest optional
Telegram username from authenticated actual sender metadata, including clearing
it when absent; display “No username” when missing. Missing display names use
“Name unavailable”. These fields never establish account identity.

The removal preview is read-only. Confirmation must be fresh, recheck the
teacher/student link and current impact under the approved lock order, and
reconcile retries by operation key. If relevant state changes after preview,
reject confirmation and require a new preview. UI copy must enumerate the
effects above rather than say only “delete student”.

## Task 00: Approve identity, history and removal policy

**Domain:** Product/backend/bot. **Type:** planning. **Dependencies:** Reconciliation inventory.

**Context:** "Delete student" could mean detach, deactivate or globally delete the account; these have very different consequences.

**What to do:** Approve exact meaning, rejoin behavior, personal-data retention and whether removal deletes/hides future lessons, recurrences, cancellations, homework and files. Specify handling of past data, pending uploads/submissions, queued notifications and cached schedules. Decide date-added semantics and honest legacy display when unknown.

Define ten-action scope: proposed scheduling creates/moves/cancels/deletes, homework assignment/submission/completion/deletion and settings changes, with actor and occurrence time but no secrets. Approve actor/teacher/student scope, retention, deterministic ordering, old-link visibility and missing Telegram username behavior. Avoid inventing a general analytics system.

**Reuse:** Existing identities/linkage, homework completion and schedule guard.

**Acceptance Criteria:** [x] Data-effect matrix and lifecycle/rejoin examples are approved before migrations. [x] UI confirmation effects, legacy date/username behavior, and the action catalogue are concrete.

**Execution (2026-11-03):** The owner approved detach-only removal, the data effects and access boundaries above, student-only mutation history with the documented expanded action catalogue, current-link date and username semantics, and rejoin behavior. The user-facing action history remains teacher-only as specified in the README. No implementation or migration began before approval.

**Out of scope:** Implementing a guessed delete policy. **Suitable for:** Human/product owner with agent preparation.

## Task 01: Add minimal profile metadata and action persistence

**Domain:** Existing auth/Telegram adapter and new scoped history persistence. **Type:** schema/migration. **Dependencies:** 00.

**Context:** Required identity/date/history fields do not all exist.

**What to do:** Add relationship records for each link/removal, optional Telegram username representation, and action records/indexes for last-ten queries. Keep `User.teacher_id` as the active-link authority. Preserve numeric identity; sync username only from authenticated actual Telegram sender metadata, with explicit absence/change behavior. Backfill active legacy links with an unknown link time; do not invent closed links. Define deleted-target references consistent with removal retention.

**Reuse:** Shared Base/timestamps, trusted sender/session flow and existing uniqueness rules.

**Acceptance Criteria:** [ ] Rename/missing username never changes identity/linkage. [ ] Legacy data is not assigned invented association dates. [ ] Constraints/indexes/migrations/common checks pass.

**Out of scope:** Account merging or new user-identification mechanisms. **Suitable for:** AI agent.

## Task 02: Add the history writer and record auth actions

**Domain:** Auth and scoped history service. **Type:** service. **Dependencies:** 01.

**Context:** A history query is useless without complete, correct mutation coverage.

**What to do:** Add a small history writer that flushes within the caller's transaction, then wire every supported student-initiated state-changing auth/account operation while linked, including unrelated settings and credential changes. Scope entries to the relationship active at mutation time. Include actor/subject/teacher, action kind, stable resource reference and event time without value snapshots. Do not log reads, sign-ins, token/session operations, teacher/admin/system actions or unlinked mutations. Avoid after-response logging or a second commit. Idempotent replay emits no second action; failed operations emit none.

**Reuse:** Existing operation-key reconciliation and guarded event writes; explicit cross-domain module imports.

**Acceptance Criteria:** [x] Approved auth actions map to use cases/tests. [x] Rollback and duplicate requests yield correct history without changing registration/settings behavior.

**Out of scope:** Event/homework wiring, logging reads or retrospective history reconstruction. **Suitable for:** AI agent.

**Execution (2026-11-03):** Added a flush-only history writer and recorded linked student account/profile edits, student preference creation and updates, invitation linking, and Telegram student registration/linking. The self-service password-change operation does not exist; admin credential changes remain excluded. History stores action/resource references only, never submitted values. No-op profile/settings replays, reads, unlinked changes, teacher/admin changes, duplicate linking/registration, and rollback do not leave extra actions. Verified with 13 passing focused/regression tests, Ruff, formatting, and mypy.

## Task 03: Record student-initiated scheduling actions

**Domain:** Existing event mutation use cases. **Type:** service. **Dependencies:** 02.

**Context:** History includes only changes initiated by the student; teacher changes to a student's schedule are not student actions.

**What to do:** Wire only student-initiated one-off, recurring, and occurrence schedule mutations to the shared history writer. Teacher/admin/system mutations, including break displacement, do not create student history entries. Preserve parent/occurrence identities and the teacher guard; a preview, rolled-back mutation or replay must not add an action.

**Reuse:** History writer and existing serialized event mutations.

**Acceptance Criteria:** [x] Student-initiated one-off/series/occurrence successes and rollbacks have exact history assertions; teacher displacement creates no student action. [x] No extra commit, duplicate action or changed booking behavior; common checks pass.

**Out of scope:** New schedule semantics. **Suitable for:** AI agent.

**Execution (2026-11-03):** Added transaction-bound history for student-initiated one-off lessons, recurring lessons, exact occurrence cancellation/moves, and vacations. Teacher/system operations, including confirmed break displacement, are excluded; failed mutations and no-op/replayed updates do not add actions. Focused schedule/history and Telegram regression tests passed; the full suite passed (573 passed, 10 skipped). Ruff check/format and backend mypy passed.

## Task 04: Record homework actions in owning transactions

**Domain:** Existing homework use cases. **Type:** service. **Dependencies:** 02.

**Context:** Assignment/submission operations already reconcile operation keys; history must follow those guarantees.

**What to do:** Wire student-initiated submission versions, completion and any other supported student homework mutations to the shared history writer before the owning commit. Teacher assignment creation, review/manual completion/deletion, and physical cleanup retries are not student actions. Keep submission text/file bytes out of history and handle retained deleted-resource labels according to Task 00.

**Reuse:** Homework operation-key reconciliation and recoverable cleanup services.

**Acceptance Criteria:** [ ] Replay, rollback and cleanup retry do not add actions; new solution versions do. [ ] Existing completion/file persistence behavior and common checks pass.

**Out of scope:** Notification delivery. **Suitable for:** AI agent.

## Task 05: Expose teacher-owned profile and history reads

**Domain:** Existing auth/student-profile orchestration and homework reads. **Type:** service/router. **Dependencies:** 01, 02, 03, 04.

**Context:** Profile summary must combine scoped data without broadening owner-only access.

**What to do:** Add bounded active-roster/detail and read-only former-student projections with approved username/name/date, homework completion summary and last ten actions. Scope each read to the exact active or historical teacher/student relationship before joins/serialization, including retained homework and past lessons. Escape in UI, not by returning pre-rendered HTML; omit credentials, transport routing and private values. Define historical unknown/empty labels.

**Reuse:** `get_teacher_students`, homework status/provenance and history query indexes.

**Acceptance Criteria:** [ ] Active/former teacher, student, new teacher and unrelated teacher tests show exact summaries, access boundaries and ten-action order. [ ] Missing metadata, no homework and longer history work; query count is bounded and owner-profile rules remain unchanged.

**Out of scope:** Admin dashboards or analytics endpoints. **Suitable for:** AI agent.

## Task 06: Implement confirmation-bound student removal

**Domain:** Auth and affected scheduling/homework use cases. **Type:** service/router. **Dependencies:** 00, 01, 05.

**Context:** Removal races creation, uploads and delivery; a stale button is not authorization.

**What to do:** Add non-mutating impact preview and explicit fresh confirmation with operation-key reconciliation. Recheck linkage/activity after acquiring approved locks and apply Task 00's data-effect matrix atomically where possible; use existing recoverable physical file cleanup beyond DB commit. Revoke relevant access immediately; invalidate/suppress future notification work and expose cache-generation invalidation if cache exists. Define global lock ordering and avoid deadlocks with schedule guard/homework row locks.

**Reuse:** Existing authorization, event service guard, homework deletion/cleanup and notification recipient revalidation.

**Acceptance Criteria:** [ ] Preview/Cancel change nothing. [ ] Confirm affects only the approved student/scope, works on both DBs and preserves unrelated lessons/files. [ ] Concurrent booking/submission/rejoin and unknown-response retries cannot bypass removal or repeat destruction.

**Out of scope:** Calling admin user deletion as a shortcut. **Suitable for:** AI agent after explicit owner approval.

## Task 07: Wire Telegram student profile and removal screens

**Domain:** Bot API models and teacher dialogs/home. **Type:** bot. **Dependencies:** 05, 06.

**Context:** The teacher needs a discoverable student screen, not just another API.

**What to do:** Reuse roster navigation; add active and read-only former-student lists, profile identity/date/homework summary and complete last-ten history with safe pagination. Link existing homework detail flows without bypassing auth. Add Remove → impact review → Confirm/Cancel → authoritative result; preserve drafts, stale-revision/reload and uncertain-outcome handling. Refresh affected pickers/views after removal.

**Reuse:** Existing typed client, escaped leading feedback, `ShowMode.EDIT` and confirmation patterns.

**Acceptance Criteria:** [ ] Every README profile item and approved former-student flow is reachable, including missing-data labels. [ ] Students cannot forge teacher actions; no extra normal screen sends; removal cancel/confirm/stale/error paths are tested.

**Out of scope:** Teacher transfer or chat messaging. **Suitable for:** AI agent.

## Task 08: Verify lifecycle and publish retention contract

**Domain:** Tests/docs/deployment. **Type:** integration. **Dependencies:** 03, 04, 06, 07.

**Context:** A successful HTTP delete does not prove correct data retention and downstream revocation.

**What to do:** Exercise teacher/student actions → profile/history → preview/cancel/confirm removal → restart and approved rejoin behavior. Verify files/DB state and queued/cached access if those systems exist. Document legacy identity limits, retention/cleanup, migration and rollback constraints in README/AGENTS.

**Reuse:** Auth/scheduling/homework integration fixtures and recording transport.

**Acceptance Criteria:** [ ] Exact data-effect matrix is verified on both DBs and Telegram. [ ] No false historical data, unauthorized access or success-shaped cleanup failure; common checks pass.

**Out of scope:** HTMX. **Suitable for:** AI agent; human for approved live acceptance.

## Open questions

Task 00 is a hard gate, especially detach versus delete and treatment of historical files/events. Do not infer this decision from the existing admin API.
