# Homework backend and Telegram UI — Plan

## Goal

Teachers can assign text/files to their own students, students can open materials and submit solutions, and teachers can review, mark complete, or delete assignments through Telegram.

**Status:** Tasks 00–09 complete; implementation in progress.

## Scope (README.md sections covered)

- [Homework](../../README.md#домашние-задания): text/file assignment, material download, submission, automatic completion after submission, teacher completion and deletion.
- [Bot interface](../../README.md#интерфейс-бота): role-specific navigation, editable screens, file/text inputs.
- [Notification requirements](../../README.md#уведомления): define the homework-state handoff for a later delivery milestone; this plan does not claim to send scheduled or push notifications.

## Baseline and prerequisites

- [AGENTS.md](../../AGENTS.md#feature-status-vs-product-spec) and the actual [homework package](../../backend/homework/) show no homework models/router/service. Do not invent a reusable homework store.
- [Notification model](../../backend/notifications/models.py) is only a placeholder (`receivers`, `message`, `is_sent`); no delivery service/worker exists. Do not call it an outbox or rely on it for retries.
- [Auth models](../../backend/auth/models.py) supply teacher/student linkage and Telegram identity/routing. User IDs are the domain identities; the backend must own homework state independently of Telegram message IDs.
- [Bot client](../../telegram/api.py), [onboarding](../../telegram/onboarding.py), [runtime](../../telegram/runtime.py) and [recording transport](../../tests/bot_support.py) provide HTTP, dialog, private-chat, isolation and test patterns; attachment transfer is new.
- Schedule foundation provides exact `(source kind, source ID, UTC occurrence start)` identities and vacation/cancellation filtering. Use these if homework is linked to lessons; do not use a series UUID alone or create homework as a fake scheduling event.
- Recommended implementation order: [scheduling/settings UI](./telegram-scheduling-and-settings-ui.md), [automatic breaks](./automatic-between-lesson-breaks.md), then this milestone. Homework's core backend does not technically depend on automatic breaks, but Telegram home/client edits should be coordinated.

## Task dependencies

| Task | Prerequisites |
|---|---|
| 00 Approve homework and attachment contract | None |
| 01 Homework/submission metadata migration | 00 |
| 02 Authorized attachment storage/transfer | 00, 01 |
| 03 Teacher assignment service/API | 01, 02 |
| 04 Authorized list/detail/download reads | 03 |
| 05 Student submission/completion service/API | 02, 03 |
| 06 Teacher review/manual completion/delete | 04, 05 |
| 07 Typed bot homework and attachment client | 04, 05, 06 |
| 08 Teacher assignment dialogs | 07 |
| 09 Student materials/submission dialogs | 07 |
| 10 Teacher review/completion/deletion dialogs | 07, 08, 09 |
| 11 Integration, docs and notification handoff | 08, 09, 10 |

## Common acceptance criteria

- Use async domain-scoped services, Pydantic v2 schemas, user JWTs and `Annotated` dependencies. Derive teacher identity from the authenticated account and verify active linkage/assignment ownership at every boundary; the bot must not use the registration service credential for homework.
- Every upload/download is authorized independently; attachment UUIDs and Telegram callbacks are not access tokens. Never accept arbitrary filesystem paths or remote URLs from clients as download sources. Do not expose Bot API tokens or Telegram file download URLs in backend responses/logs.
- Stream bounded transfers without blocking the event loop. Enforce approved byte/count/name/type rules while streaming, not only from declared headers. Avoid loading large files into dialog state or HTTP buffers.
- Keep ordinary text/inline screens editable; downloading actual materials may necessarily send media/documents separately. Explicitly test/document that exception rather than promising file transfer in the same text message.
- Reuse isolated real-DB fixtures and simulated Bot API transport. Run targeted tests, applicable `uv run ruff check backend telegram tests`, touched-file format checks, and strict mypy for backend/bot; report pre-existing failures.
- Model changes require static reversible `YYYY-MM-DD_slug.py` migrations and disposable SQLite/PostgreSQL checks, model discovery and configured storage persistence. No live user files/DBs in automated tests.

## Task 00: Approve homework semantics, file storage, and UI contract

**Domain:** product/backend/Telegram contract. **Type:** planning. **Dependencies:** None.

**Context:** README defines outcomes, but not assignment-to-lesson linkage, file storage, or submission multiplicity.

**What to do:** Approve whether homework is student-scoped, tied to an exact upcoming lesson occurrence, or optionally linked; reminders need an unambiguous lesson association before later scheduling. Define cancellation/move/series-delete behavior without deleting student work implicitly. Agree text-only, files-only and mixed assignments/solutions; explicit Finish submission versus first-file completion; resubmission/version retention; manual teacher completion and delete semantics; behavior when teacher linkage changes.

Choose backend-owned private file storage appropriate to separate bot/backend hosts (recommend authenticated streamed HTTP upload/download to persistent backend storage, not Telegram `file_id` as the sole durable source). Approve concrete byte/count/type limits, filename rules, retention/deletion, aborted-upload cleanup, and how user deletion interacts with homework foreign keys. Approve teacher/student screen map and upload timeout/duplicate reconciliation contract.

Distinguish existing `notification_homework` lead-days from future assignment/submission push toggles; do not repurpose it as a push toggle or add new settings without the notification milestone's decision.

**Reuse:** Current roles, linkage, exact occurrence identity, backend-only domain ownership.

**Acceptance:** [x] Choices and concrete input/output/error examples are recorded before schema implementation. [x] Attachment limits, retention and solution completion point are measurable. [x] Notification delivery remains explicitly deferred.

**Approved contract (2026-10-26):**

- Homework belongs to one teacher and one student. Creation and access require
  an active teacher/student link. Assignments remain bound to their original
  user IDs and are never reassigned or deleted when the link changes, but both
  original parties lose access while that link is inactive; a newly linked
  teacher also gets no access. Access returns only if the original pair is
  actively linked again.
- An assignment may optionally reference one exact upcoming lesson occurrence.
  Moving, cancelling, or deleting that occurrence or its series never deletes
  or reassigns homework. The reference is historical and is ignored by future
  reminders when the occurrence is no longer active.
- Assignments and solutions require text, one or more files, or both. Teachers
  explicitly publish assignments; students explicitly finish submissions.
  Completion occurs only after every selected upload succeeds. Resubmissions
  are retained as versions, with the latest current; student submission
  completion is not reopened. Teachers may manually complete only incomplete
  work; this records teacher provenance and prevents later student submission.
- Files use private persistent backend-owned storage and authenticated
  streaming; Telegram file IDs are not the durable copy. Each assignment and
  each submission permits at most 5 files, 20 MiB per file, and 50 MiB total.
  Arbitrary file contents are opaque attachments, never rendered or executed
  inline. Downloads are attachments. Filenames are reduced to a basename,
  control/path characters are removed, and names are capped at 128 characters.
  Transfers time out after 120 seconds; mutation retries reuse a client UUID
  operation key. Staged uploads expire after 24 hours and are cleaned daily;
  cancellation removes drafts promptly.
- Ordinary Telegram navigation/text screens edit the active message.
  Downloaded materials and other file transfers are sent as separate media
  messages.
- Teacher deletion immediately revokes access and removes the assignment,
  submissions, and files. Failed physical cleanup is an explicit retryable
  failure; a hidden cleanup tombstone remains until a repeated delete succeeds.
  There is no age-based deletion. Account deletion removes associated homework
  and files through the same cleanup path.
- No scheduled/push notification delivery is part of this milestone. The
  existing homework lead-days preference is unchanged; notification delivery
  remains deferred.

**Contract examples (payload shapes are illustrative; route names remain for
implementation):**

- Teacher publishes text plus one previously staged file:
  `{"student_id":"<student UUID>","text":"Read chapter 3","attachment_ids":["<staged UUID>"],"lesson_occurrence":{"kind":"EVENT","source_id":"<event UUID>","starts_at":"2026-11-02T09:00:00Z"}}`
  → `201 {"id":"<homework UUID>","status":"INCOMPLETE","material_count":1}`.
  Assignment creation requires `Idempotency-Key: <client UUID>`; reuse it for
  retries and the same request returns the original assignment.
  Text-only omits `attachment_ids` and the optional lesson; file-only omits
  `text`. The lesson `kind` identifies the source record (`EVENT` or
  `RECURRENT_EVENT`), and the record must be a `LESSON` for the selected
  student. A student not actively linked to the teacher returns `403`; a file
  exceeding the approved limit returns `413`; neither creates visible work.
- Student finishes text plus staged solution file with
  `{"text":"My answer","attachment_ids":["<staged UUID>"],"operation_key":"<client UUID>"}`
  → `201 {"version":1,"assignment_status":"COMPLETE","completion_source":"STUDENT_SUBMISSION"}`.
  Retrying the same operation key returns the original result without a second
  version. An upload/storage failure returns a non-success response and leaves
  the assignment incomplete; an empty final solution returns `422`.
- Teacher manual completion returns
  `200 {"status":"COMPLETE","completion_source":"TEACHER"}`; repeating it is
  idempotent. Confirmed deletion returns `204`; storage cleanup failure returns
  `503`, leaves the assignment hidden and its files inaccessible, and a retry
  continues cleanup.
- Authorized file download streams the opaque attachment with a sanitized
  attachment filename. A non-owner or a user guessing another attachment UUID
  receives `404`; a staged file owned by another user cannot be attached to an
  assignment or submission.

**Out of scope:** Choosing unspecified behavior during implementation. **Suitable for:** Human/product owner with agent preparation.

## Task 01: Add homework and submission metadata with migrations

**Domain:** new models/schemas in existing `backend/homework/`; Alembic. **Type:** schema/migration. **Dependencies:** 00.

**Context:** No homework persistence exists.

**What to do:** Implement only approved assignment text, teacher/student ownership, completion source/state, solution records and attachment metadata (separate assignment materials from submitted solutions). If approved, store exact lesson association with explicit lifecycle handling. Add schema constraints/indexes justified by teacher/student list/detail queries and safe foreign-key deletion behavior. Register models using existing discovery patterns.

**Reuse:** Shared `Base`/`TimestampMixin`, existing user IDs and UTC conventions; no duplicate account tables.

**Acceptance:** [x] Required text-or-files/content constraints and role ownership are representable without fake events. [x] Upgrade/downgrade/schema-drift checks pass on SQLite and PostgreSQL with downgrade refusal documented below. [x] Targeted tests and lint/type/format checks were run; pre-existing repository-wide mypy/format failures are recorded below.

**Migration behavior:** The downgrade refuses before changing schema if any
homework, submission, or attachment metadata remains. Export or remove records
and committed files through the approved cleanup path before retrying. Empty
tables downgrade and upgrade cleanly. Both refusal and no-data round trips were
verified on isolated SQLite and PostgreSQL databases.

**Verification note:** `ruff check backend telegram tests`, touched-file format
checks, `mypy backend/homework`, `mypy telegram`, and `tests/test_smoke.py`
passed. Repository-wide `ruff format --check backend telegram tests` reports
pre-existing formatting in `backend/notifications/models.py`; `mypy backend`
reports pre-existing errors in `backend/auth/config.py`,
`backend/database.py`, and `backend/events/dependencies.py`.

**Out of scope:** Delivery queues, grading/rubrics or a general learning-management system. **Suitable for:** AI agent.

## Task 02: Implement authorized attachment storage and transfer

**Domain:** existing `backend/homework/`, domain config and deployment storage. **Type:** service/router/config. **Dependencies:** 00, 01.

**Context:** Separate hosts require actual transfer; Telegram file IDs alone do not provide a universal backend API.

**What to do:** Implement approved private storage with domain-scoped configuration, authenticated streaming upload/download and staged-file ownership. Use opaque server-generated storage keys; sanitize display/download filenames, enforce limits and safe content disposition, and reject path traversal or executable serving. Persist metadata only for valid transfers; compensate bounded failed uploads and define cleanup for abandoned staging and failed file deletion. If using filesystem storage, configure persistent volume and safe async/threadpool I/O, not blocking `open()` in async routes.

**Reuse:** Existing auth dependencies, domain config pattern and async HTTP conventions.

**Acceptance:** [x] Unauthorized reads, traversal names, oversize/chunked uploads, disconnects, timeout and storage failure are tested. [x] Committed materials remain readable across storage configuration recreation; Compose uses a persistent volume, and staged/orphan leftovers have a documented daily cleanup path. [x] Focused lint, format and type checks pass; existing repository-wide mypy/format failures are recorded above.

**Verification (2026-10-26):** `uv run pytest -q tests/test_homework_attachments.py tests/test_smoke.py` (11 passed); `uv run ruff check backend telegram tests`; touched-file `ruff format --check`; `uv run mypy backend/homework`; `uv run mypy telegram`; and both Compose manifests validated with `.env.example`. Tests cover private file/directory permissions, owner-only staged downloads, unauthorized/cross-user reads, filename traversal sanitization, idempotent retry and stale-lock recovery, ordinary and chunked oversize rejection, multipart file count, 120-second transfer timeout behavior, storage failure, disconnect cleanup, and daily staged-file cleanup. Repository-wide format checking still reports pre-existing formatting in `backend/notifications/models.py`; `mypy backend` still reports pre-existing issues in `backend/auth/config.py`, `backend/database.py`, and `backend/events/dependencies.py`.

**Out of scope:** Public URLs, third-party file ingestion or a generic storage platform. **Suitable for:** AI agent after storage choice.

## Task 03: Implement teacher assignment creation

**Domain:** existing `backend/homework/`. **Type:** service/router. **Dependencies:** 01, 02.

**Context:** A teacher may assign text and/or uploaded materials only to their own student.

**What to do:** Add the minimal teacher-only creation endpoint with validated content and owned staged attachment references. Resolve active teacher/student linkage and approved lesson association before committing. Attach only the teacher's correct staged files, reject empty assignments/foreign uploads, and follow the approved duplicate/unknown-outcome contract. Keep file finalization/database state consistent without claiming distributed filesystem/DB atomicity.

**Reuse:** Existing auth/assignment-scope patterns and attachment service.

**Acceptance:** [x] Text/files/mixed creation work per contract. [x] Foreign students/uploads, invalid lesson identity and failed finalization leave no visible partial assignment. [x] Common checks pass for touched code; repository-wide mypy/format failures are recorded above.

**Verification (2026-10-26):** `uv run pytest -q tests/test_homework_assignments.py tests/test_homework_attachments.py tests/test_smoke.py` (18 passed); `uv run ruff check backend telegram tests`; touched-file `ruff format --check`; `uv run mypy backend/homework`; `uv run mypy telegram`. Tests exercise text-only/file-only/mixed assignment, active student linkage, teacher-only authorization, staged attachment ownership and transitions, exact 50 MiB/5-file limits, duplicate and unknown commit-outcome reconciliation, failed-finalization rollback, and exact upcoming one-off/recurring lesson identity including cancelled, wrong-student, wrong-kind, non-lesson, and past references. Repository-wide `ruff format --check backend telegram tests` still reports pre-existing formatting in `backend/notifications/models.py`; `mypy backend` reports the three pre-existing issues in `backend/auth/config.py`, `backend/database.py`, and `backend/events/dependencies.py`.

**Out of scope:** Batch assignments, templates, notification sending. **Suitable for:** AI agent.

## Task 04: Add authorized homework list/detail/material reads

**Domain:** existing `backend/homework/`. **Type:** service/router. **Dependencies:** 03.

**Context:** Teachers need their assigned work; students need only their own materials/status.

**What to do:** Add bounded paginated role-scoped list and detail reads with approved status filters and attachment metadata. Authorize every assignment/material/solution read, including after linkage or assignment deletion. Include exact completion state and lesson association if approved; do not return local storage keys or another student's details.

**Reuse:** Homework ownership service, attachment download authorization, existing pagination patterns.

**Acceptance:** [x] Empty/multiple-page lists and complete material retrieval work. [x] Forged IDs, de-linked users and cross-teacher/student reads fail as approved. [x] Query count is bounded; common checks pass.

**Verification (2026-10-26):** `uv run pytest -q tests/test_homework_reads.py tests/test_homework_assignments.py tests/test_homework_attachments.py tests/test_smoke.py` (22 passed); `uv run ruff check backend telegram tests`; touched-file `ruff format --check`; `uv run mypy backend/homework`; and `uv run mypy telegram`. Tests cover role-scoped paginated reads, status filtering, completion and exact lesson fields, material metadata without storage keys, forged/cross-owner IDs, active-link changes and download revocation, deleted-assignment hiding, and a fixed two-query teacher list regardless of page size.

**Out of scope:** Full teacher student profile/history. **Suitable for:** AI agent.

## Task 05: Implement student solution submission and automatic completion

**Domain:** existing `backend/homework/`. **Type:** service/router. **Dependencies:** 02, 03.

**Context:** README considers homework complete once a solution is submitted.

**What to do:** Implement approved draft/final submission and resubmission policy for text/files, rechecking ownership and assignment existence at commit. Atomically persist the finalized submission and mark assignment complete; an incomplete/failed upload must not complete it. Apply the approved duplicate/operation-key behavior and resolve concurrent delete/manual-complete/submission without orphan metadata or false success.

**Reuse:** Assignment scope checks, attachment service and transaction patterns; no need to take the teacher schedule guard for independent homework unless approved lesson mutation requires it.

**Acceptance:** [x] Text/files/mixed solutions finalize exactly as approved; failed/empty submissions do not change completion. [x] Duplicate/concurrent requests and unauthorized student/teacher submissions have deterministic results. [x] Common checks pass.

**Verification (2026-10-26):** `uv run pytest -q tests/test_homework_submissions.py tests/test_homework_reads.py tests/test_homework_assignments.py tests/test_homework_attachments.py tests/test_smoke.py` (29 passed); `uv run ruff check backend telegram tests`; touched-file `ruff format --check`; `uv run mypy backend/homework`; and `uv run mypy telegram`. Tests cover text/file/mixed submissions, versioned resubmissions, completion provenance, 5-file/50 MiB limits, staged ownership/expiry, empty/unauthorized/manual-completed cases, commit failure and unknown-outcome reconciliation, and concurrent same-key submission without duplicate versions.

**Out of scope:** Grading, teacher approval as a prerequisite for student completion. **Suitable for:** AI agent.

## Task 06: Implement teacher review, manual completion, and deletion

**Domain:** existing `backend/homework/`. **Type:** service/router. **Dependencies:** 04, 05.

**Context:** Teachers must inspect submitted materials, mark complete without a solution, and delete homework.

**What to do:** Expose teacher-only review reads and idempotent manual completion with distinguishable completion provenance. Add deletion under the approved retention policy, revoking attachment access immediately and cleaning stored files reliably; failed physical cleanup must be explicit/recoverable, not a success-shaped hidden failure. Preserve unrelated lessons and student work. Define conflict behavior when deletion races upload/finalization.

**Reuse:** Existing homework ownership/attachment services, transaction and error conventions.

**Acceptance:** [x] Teacher can inspect own solutions, manually complete and delete. [x] Students/unrelated teachers cannot forge actions; repeated/racing deletes are safe and revoked files are inaccessible. [x] Common checks pass.

**Verification (2026-10-26):** `uv run pytest -q tests/test_homework_*.py tests/test_smoke.py` (37 passed); `uv run ruff check backend telegram tests`; touched-file `ruff format --check`; `uv run mypy backend/homework`; and `uv run mypy telegram`. Tests cover versioned submission review and attachment metadata, teacher-only idempotent manual completion, conflicts with student completion, the manual-completion/submission race, deletion cleanup and retry after storage failure, immediate access revocation, unlink authorization, and concurrent delete/submission requests without orphaned assignments or submissions.

**Out of scope:** General audit history or student deletion UI. **Suitable for:** AI agent.

## Task 07: Add typed Telegram homework and file-transfer client

**Domain:** existing `telegram/api.py` plus narrowly scoped client helpers. **Type:** bot/client. **Dependencies:** 04, 05, 06.

**Context:** Existing bot reads JSON; homework requires authenticated streamed multipart/download flows and safe retry semantics.

**What to do:** Add typed homework requests/results and bounded file transfer. Stream Telegram-provided bytes to backend and backend bytes to Telegram without sending bot tokens/file URLs to the backend. Treat advertised Telegram sizes as advisory and enforce actual limits. Handle 204, authorization, validation, stale/deleted assignments, transport/circuit failures, and lost mutation responses through the approved reconciliation contract.

**Reuse:** Scheduling UI client write/204/error handling, shared HTTP client/circuit breaker and user JWT cache.

**Acceptance:** [x] Transfer limits/timeouts, JWT renewal, malformed responses and unknown outcomes are tested with no duplicate final submissions. [x] No backend imports, secrets or file bytes in logs/dialog drafts; common checks pass.

**Verification (2026-10-26):** `uv run pytest -q tests` (346 passed, 5 skipped); `uv run ruff check backend telegram tests`; touched-file `ruff format --check`; and `uv run mypy telegram`. Real-backend client tests cover streamed material upload/download, typed assignment/submission/review/completion/deletion calls, JWT renewal, and same-operation-key submission reconciliation after a lost response without duplicate versions. Mock-transport tests cover actual transfer limits, upload/download timeouts, malformed mutation responses, safe stale-resource errors, and credential separation. The client imports no backend code and does not log or retain file bytes.

**Out of scope:** Local bot disk as the durable homework store. **Suitable for:** AI agent.

## Task 08: Build teacher homework-assignment dialogs

**Domain:** Telegram teacher home and homework dialogs. **Type:** bot. **Dependencies:** 07.

**Context:** Backend creation is not usable without student selection, material input and review.

**What to do:** Wire Homework from teacher home, own-student picker, assignment text/files, optional lesson selection only if approved, review, remove draft material, Submit and Cancel. Show upload progress/failure and limits; reconcile ambiguous sends before allowing a new submit. Handle albums/multiple updates using runtime isolation and approved Finish rules; cleanup abandoned draft uploads.

**Reuse:** Student picker from scheduling UI, input/edit patterns, typed attachment client.

**Acceptance:** [x] Teacher creates text/file/mixed work end to end and it appears in the student's list. [x] Back/Cancel and upload failures do not publish partial assignments; ordinary screens edit the active message. [x] Common checks pass.

**Verification (2026-10-26):** `uv run pytest -q tests` (352 passed, 5 skipped); `uv run ruff check backend telegram tests`; touched-file `ruff format --check`; and `uv run mypy telegram`. Real-backend/simulated-Bot-API tests cover text-only, file-only and mixed assignments, own-student pagination and role visibility, document albums, exact upcoming one-off and recurring lesson occurrences, the five-file and per-file limits, staged-file cleanup on cancel, upload failure without publication, and retry of ambiguous upload/publish responses with the same idempotency keys. Ordinary inputs and navigation edit the active message. Abandoned uploads remain subject to the backend's approved 24-hour staging cleanup.

**Out of scope:** Automatically messaging the student at creation. **Suitable for:** AI agent.

## Task 09: Build student material download and solution dialogs

**Domain:** Telegram student home and homework dialogs. **Type:** bot. **Dependencies:** 07.

**Context:** Students need reachable lists, materials and explicit solution-completion feedback.

**What to do:** Add Homework list/detail, status, safe text/material viewing and document download. Add text/files solution draft, review/Finish, Cancel and approved resubmission behavior. Explain when a successful solution marks work complete; retain draft state on correctable errors. Distinguish deliberate media sends from normal edit-in-place navigation.

**Reuse:** Typed homework client, existing pagination, runtime edit/isolation and draft patterns.

**Acceptance:** [x] Student downloads actual materials and submits an actual solution through simulated Bot API + real backend; persisted completion is asserted. [x] Deleted/stale assignments, oversized files, repeated callbacks and backend failure do not report false completion. [x] Common checks pass.

**Verification (2026-10-26):** `uv run pytest -q tests` (361 passed, 5 skipped); `uv run ruff check backend telegram tests`; touched-file `ruff format --check`; and `uv run mypy telegram`. Real-backend/simulated-Bot-API tests assert paginated/status-filtered lists, actual streamed material bytes sent as a separate document, text-and-file submission persistence and completion, versioned resubmission without reopening completion, retry of an ambiguous Finish with the same payload/key, stale deletion refresh, teacher-completion race rejection, oversized upload rejection before Telegram retrieval, cancellation cleanup, and retry after backend outage. Ordinary text/navigation stays in the editable screen. The separate-document download UX was explicitly approved.

**Out of scope:** Offline file cache or grading. **Suitable for:** AI agent.

## Task 10: Build teacher review, mark-complete and delete dialogs

**Domain:** Telegram teacher homework dialogs. **Type:** bot. **Dependencies:** 07, 08, 09.

**Context:** A teacher needs the complete assignment workflow, not only creation.

**What to do:** Add own-student/status navigation, fresh detail and submitted-material downloads, manual Mark complete, and explicit Delete confirmation. Reflect student-submitted versus teacher-marked completion. Refresh authoritative state after actions and recover cleanly from stale/deleted work or outcome-unknown writes.

**Reuse:** Existing teacher homework list, typed completion/deletion client and confirmation patterns.

**Acceptance:** [x] Teacher can assign → inspect student solution → mark another assignment complete → cancel/confirm deletion. [x] No cross-student/teacher leakage; media-send exceptions and edit counts are tested. [x] Common checks pass.

**Verification (2026-10-26):** `uv run pytest -q tests` (364 passed, 5 skipped); focused `uv run pytest -q tests/test_homework_reads.py tests/test_bot_homework_management.py` (7 passed); `uv run ruff check backend telegram tests`; `uv run ruff format --check telegram tests`; `uv run mypy backend/homework`; and `uv run mypy telegram`. Real-backend/simulated-Bot-API tests verify student/status filtering, current and earlier submission review, actual streamed assignment/solution bytes, failed Telegram document sends without replacing the active message, teacher completion provenance, deletion cancellation/confirmation, and authoritative reconciliation after ambiguous completion/deletion responses. Tests assert unrelated students and teachers' assignments are not exposed.

**Out of scope:** Feedback messaging, marks or action-log screens. **Suitable for:** AI agent.

## Task 11: Verify end-to-end homework and document notification handoff

**Domain:** tests, deployment and docs. **Type:** integration/documentation. **Dependencies:** 08, 09, 10.

**Context:** Completion requires durable files, correct homework state and reachable bot flows on separate clients.

**What to do:** Run teacher/two-students/unrelated-teacher real-DB dispatcher scenarios for assignment, materials, solutions, manual completion, deletion, restart, auth renewal, storage outage, interrupted uploads and concurrent operations. Verify both metadata and committed files on isolated storage, plus migration paths and storage deployment configuration. Update README/AGENTS only for shipped functionality.

Document later notification consumers: assignment-created and finalized-solution transitions, completion state/provenance, authorized lesson association if approved, cancellation/vacation handling, and recipient resolution. Do not add a scheduler/outbox or claim delivery. The separate notifications milestone must define reliable delivery/idempotency and the currently unspecified assignment/submission push toggles; existing homework lead-days alone cannot cover those.

**Reuse:** Existing bot integration fixtures, new isolated storage fixture, shared occurrence/visibility services for lesson association tests.

**Acceptance:** [ ] Every README homework action is reachable and verified from Telegram, with exact data/file assertions. [ ] Storage persists across restart; failure cleanup and ownership boundaries are tested. [ ] Common checks and migration evidence are recorded; notification limitations are explicit.

**Out of scope:** Morning digests, reminder scheduling, broadcast delivery, HTMX, full student history. **Suitable for:** AI agent; human for optional real-Telegram file/readability acceptance.

## Open questions / execution gates

- Task 00's storage, retention, resubmission, completion and lesson-association choices must be approved before implementation. Avoid speculative deadline, grade, or assignment-template fields.
- File downloads necessarily differ from editable text screens; approve and document that UX explicitly.
- Homework state and UI can ship without notifications, but the bot must not promise notifications until the delivery milestone exists.
