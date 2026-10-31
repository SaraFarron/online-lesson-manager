# Telegram registration and login — Implementation plan

## Goal

A teacher or student can register through a separate aiogram bot, configure the settings described in the README, and return through `/start` without entering a password. Inline navigation edits the current bot message wherever Telegram permits.

This is a task backlog with approved decisions and execution status. Give an implementation agent one numbered task and its completed prerequisites, not the entire milestone.

## Scope (README sections covered)

- [Bot interface](../../README.md#интерфейс-бота): aiogram, edit-in-place screens, flexible time input.
- [Architecture](../../README.md#архитектура): separate bot/backend processes; backend owns accounts and settings; HTTP boundary.
- [Start](../../README.md#старт): teacher/student onboarding, teacher-code invitations, timezone, working preferences, notification preferences.
- Login means identifying a returning Telegram user and issuing the existing backend JWT. This flow is not explicitly specified in the README; the owner selected passwordless Telegram registration/login for this milestone.

## Confirmed authentication decision

- New Telegram accounts use Telegram user identity, without email/password collection.
- Existing email/password registration and login remain available.
- Linking Telegram to an existing email/password account is **out of scope**. Never merge by Telegram username, display name, or guessed email.
- Telegram user ID, not username or chat ID, identifies the account. Usernames can change.
- Registration is limited to teachers/students; the bot cannot create administrators or superusers.

## Screen-library decision and research

**Use aiogram 3 + aiogram-dialog 2.** No custom screen framework is needed.

Verified on 2026-10-14:

| Item | Evidence / consequence |
|---|---|
| Published packages | PyPI metadata reports aiogram `3.31.0` and aiogram-dialog `2.6.0`. Resolve compatible versions with `uv` during Task 08 and commit the resulting lockfile; these are research results, not a mandate to pin exact patches. |
| Compatibility | aiogram metadata declares Python `>=3.10,<3.15`; aiogram-dialog declares `>=3.10` and requires aiogram `>=3.14.0`. The aiogram-dialog development manifest also lists Python 3.14. Verify actual dependency resolution and a Python 3.14 smoke test before adopting. |
| Screens | `Dialog` / `Window` / states, keyboard widgets, and input widgets supply registration navigation and forms. |
| Editing | `ShowMode.AUTO` uses **EDIT for callbacks**, but **SEND for incoming user messages**. Use explicit `ShowMode.EDIT` for text-input/error transitions when a dialog message exists. The setting applies only to the next update, so it must be reapplied on relevant transitions. There is no documented `ShowMode.EDIT_AUTO`; do not use it. |
| Limitations | The initial screen needs a new message. A deleted/uneditable message may require a replacement. User input messages remain in chat; editing the bot screen does not remove them. Do not promise an entirely single-message chat. |
| License | aiogram-dialog's published metadata and repository manifest identify Apache-2.0. Preserve required notices if redistributing it. |

Sources:

- [aiogram-dialog repository](https://github.com/Tishka17/aiogram_dialog)
- [ShowMode documentation](https://aiogram-dialog.readthedocs.io/en/stable/how_are_messages_updated/index.html)
- [aiogram-dialog published metadata](https://pypi.org/pypi/aiogram-dialog/json)
- [aiogram published metadata](https://pypi.org/pypi/aiogram/json)
- [aiogram-dialog development manifest](https://github.com/Tishka17/aiogram_dialog/blob/develop/pyproject.toml)
- [aiogram deep-link helpers](https://docs.aiogram.dev/en/latest/utils/deep_linking.html)
- [Telegram deep-link requirements](https://core.telegram.org/bots/features#deep-linking)

**Contingency:** Only if Task 08 demonstrates an actual compatibility blocker, evaluate a small aiogram FSM renderer: store the active message ID, render text/keyboard, acknowledge callbacks, edit via `edit_message_text`, and replace only on specifically classified edit failures. A basic renderer is roughly 100–200 lines; validation, stale callbacks, recovery, concurrency, and tests can bring the complete layer to roughly 250–450 lines. These are estimates, not measured code. This is feasible, but unnecessary while aiogram-dialog works. Do not build a general UI framework. If the fallback exceeds this bounded scope, obtain owner approval for ordinary message-by-message navigation.

## Baseline repository evidence and reusable pieces (before implementation)

- [Auth router](../../backend/auth/router.py) already provides `/api/v1/auth/register`, `/login`, `/me`, `/me/teacher`, and teacher/student profile endpoints. Do not reimplement password auth.
- [Auth models](../../backend/auth/models.py): `User.email` and `hashed_password` are non-nullable; Telegram identity is absent. `User.timezone` and `notification_lessons_today` exist. `TeacherProfile` already stores work times, duration, between-lesson break, max/day, and invitation code. `StudentProfile` stores lesson/homework notification values.
- Missing onboarding fields: working weekdays and max consecutive lessons. Current break default is **0**, whereas the README says **5 minutes**. Current work-time defaults are null, whereas the README says **09:00–18:00**.
- [Auth schemas](../../backend/auth/schemas.py): public user output assumes email exists and omits role/teacher linkage; self-update cannot change timezone or daily-digest preference. Teacher patch-like updates currently carry defaults that can overwrite unrelated values; student updates do not distinguish omission from explicit disabling.
- [Auth service](../../backend/auth/service.py): `create_user`, profile creation, and `link_student_to_teacher` each commit separately. Do not call these unchanged inside a supposedly atomic registration transaction. Teacher updates regenerate a code when omitted; codes have no uniqueness constraint.
- [Profile routes](../../backend/auth/router.py) authenticate a caller but do not currently authorize access to the requested profile owner. This blocks safe reuse for onboarding; fix these routes, not unrelated scheduling code.
- [JWT helpers](../../backend/auth/utils.py) and [current-user dependencies](../../backend/auth/dependencies.py) should remain the user-authentication mechanism. Passwordless users require a deliberate password-login failure path.
- [Backend Telegram package](../../backend/telegram/) and [bot package](../../telegram/) are empty. Put the backend adapter in the former and the separate executable bot client in the latter.
- [Dependency manifest](../../pyproject.toml) packages only `backend`; aiogram/aiogram-dialog are absent, and httpx is currently dev-only. The bot requires httpx at runtime.
- [Existing test fixtures](../../tests/conftest.py) already use `httpx.AsyncClient`, `ASGITransport`, a real isolated SQLite database, and dependency overrides. Reuse these. [Smoke tests](../../tests/test_smoke.py) already cover a lesson lifecycle, so the “no tests yet” statement in [AGENTS.md](../../AGENTS.md) is stale.
- [Dockerfile](../../Dockerfile) currently copies only backend/migrations and starts FastAPI. [Local Compose](../../docker-compose.yml) and [production Compose](../../docker-compose.prod.yml) have no bot service.

Known scheduling/vacation issues in [AGENTS.md](../../AGENTS.md) are **not blockers for account onboarding**. Do not repair scheduling validators, event timezones, vacations, or dead event dependency paths in this milestone.

## Proposed implementation contract

Task 00 approves the unresolved details below before dependent implementation.

### Backend-owned identities and trusted-bot boundary

- Keep the common `User` account and role-specific profiles; allow absent email/password for Telegram-only accounts. Do not generate fake email addresses or passwords.
- Store nullable unique 64-bit `telegram_user_id` and nullable `telegram_private_chat_id` directly on the common `User`. There is no separate Telegram account or identity table. Use the Telegram sender's ID; the private chat ID is routing information only. Display name can populate `User.full_name`; username storage is not needed for this milestone.
- The bot forwards sender identity over HTTPS to a **service-authenticated backend adapter**, not a public “log in by Telegram ID” route. A dedicated shared service credential is sufficient for this single trusted bot deployment; it is not the Bot API token or JWT signing secret.
- The backend can verify the bot service credential, not independently prove that a forwarded sender ID came from a Bot API update. Document that trust boundary explicitly. Bot handlers must derive identity from the real update sender and never from callback payloads or user input.
- Put the service credential in domain-owned settings: backend `backend/telegram/config.py`, bot `telegram/config.py`. Require it for adapter access, compare securely, never log it, and use TLS across servers. Do not share the database, JWT signing key, or Telegram Bot API token with both services.
- Returning Telegram users obtain short-lived access tokens through the adapter. No refresh-token system is required: the trusted bot can request a fresh user token after expiry. Validate active status every time. Never authenticate offline.

### Minimal API contract

Names below are proposed contracts to approve in Task 00, not existing endpoints.

| Endpoint | Authentication | Purpose |
|---|---|---|
| `POST /api/v1/telegram/session` | Bot service credential | Resolve Telegram sender; return JWT for an existing active account. Unknown identity returns a machine-readable 404, not an account or token. |
| `POST /api/v1/telegram/register` | Bot service credential | Atomically create identity, user, role profile, settings, and student-teacher link; return JWT. |
| `POST /api/v1/telegram/teacher-code/resolve` | Bot service credential | Validate an invitation before the wizard; return only teacher display information needed for confirmation. Validate again when committing registration. |
| `GET /api/v1/auth/me` | User JWT | Return safe current-account data, including role and nullable teacher linkage; support users with no email. |
| `PATCH /api/v1/auth/me` | User JWT | Update own timezone/daily-digest preference and existing self-service fields. |
| Existing role-profile routes | User JWT + owner/admin authorization | Read/update onboarding preferences, preserving existing route shapes where practical. |

Registration input must not accept a backend user ID, teacher ID, active/superuser flags, or arbitrary model fields. Derive teacher ID from a valid code. The service must verify the teacher is active and has the teacher role.

### Onboarding settings contract

| Setting | README default | Proposed representation |
|---|---|---|
| Timezone | Not specified | Required valid IANA timezone; do not silently guess from Telegram language or server timezone. UI may offer UTC, but the user confirms it. |
| Teacher work start/end | 09:00 / 18:00 | Existing `Time` columns; canonical API strings `HH:MM`. Reject end <= start until overnight scheduling is specified. |
| Working days | Monday–Friday | New validated non-empty list of unique ISO weekdays 1–7 on `TeacherProfile`, stored portably as JSON. Do not create recurrent break events as a side effect. |
| Lesson length | 60 minutes | Existing positive integer minutes. |
| Between-lesson break | 5 minutes; 0 disables | Existing non-negative integer minutes. |
| Max consecutive lessons | 4 | New positive integer on `TeacherProfile`. |
| Max lessons/day | 6 | Existing positive integer; reject max-consecutive > max/day. |
| Daily digest | 09:00 local | Existing `notification_lessons_today`: propose minute-of-local-day 0–1439, null = off. Explicit toggle plus time input. |
| Student pre-lesson reminder | 15 minutes; maximum 10 hours | Existing `notification_lesson`: integer minutes 1–600, null = off. Bot input explicitly selects hours or minutes and converts hours to minutes; never guess a unit. |
| Student homework reminder | 2 days before lesson | Existing `notification_homework`: propose positive integer days, null = off. No arbitrary maximum without owner approval. |

Persist preferences only. Display clearly that actual scheduled notifications and scheduling-rule enforcement are not yet implemented.

### Bot behavior

- `/start` in a private chat first resolves the sender with the backend. Existing user: open role-specific home screen. Unknown user: show teacher/student selection, unless an invitation directly selects student onboarding.
- Generate `https://t.me/<bot_username>?start=t_ABC123` with aiogram's helper and parse the actual `/start t_ABC123` argument. Generated codes match `^t_[A-Z0-9]{6}$`, including the `t_` prefix in the payload.
- An already registered account receiving an invitation must not change role or teacher. Show that it is already registered; teacher transfer/linking existing accounts is out of scope.
- Teacher wizard: timezone → work times → working days → lesson length → break → max consecutive → max/day → digest toggle/time → review → submit → code/invitation home screen.
- Student wizard: invitation/code → teacher confirmation → timezone → digest toggle/time → pre-lesson toggle/lead time → homework toggle/days → review → submit → home screen.
- Navigation: Back, Cancel, retry, and review edits operate on the active screen. Defaults are visible and editable. Invalid values preserve prior inputs.
- Keep wizard drafts and cached user JWTs in memory for the first single-process polling bot. Completed registration is durable in the backend. Restart recovery means `/start` logs in completed users and restarts incomplete wizards; do not claim drafts survive restarts.
- Use a shared async HTTP client, finite timeouts, and a small bounded circuit breaker around backend calls. No offline authentication or cached-success registration. Do not automatically retry registration with unknown outcome: resolve the sender first.
- No read-only schedule cache is built here because schedule viewing is not implemented. This is a milestone subset of the architecture requirement, not a claim of complete offline support.

## Tasks and dependencies

Task sections below follow the individual-task template. Each task must include its own verification before starting the next; per owner approval, manual HTTP/database checks are sufficient and new test files are not required. Task 13 supplements, not replaces, these checks.

### Execution status (2026-10-14)

Tasks 00–07 are implemented in order. Each task was checked before proceeding.
Verification used manual HTTP requests against the ASGI app with real disposable
databases, plus the existing password-auth/scheduling smoke test. SQLite and
PostgreSQL migration upgrades/downgrades, code collision preflight, transaction
rollback and concurrent signup were verified. No developer database was used.
The adapter's Swagger UI and OpenAPI security/discriminated request contract
were inspected, and teacher/student registration plus session recovery were
also checked over real local network HTTP. No new test files were added.

Ruff checks pass on the backend, and changed backend files are formatted. Full
mypy retains nine pre-existing errors in auth configuration, database typing,
and event validators/dependencies; no new type errors were introduced. The
unrelated notification-model formatting baseline remains unchanged.

Tasks 08–13 are now implemented and verified sequentially. The separate
aiogram/aiogram-dialog bot provides editable teacher/student onboarding,
invitations, role homes, and backend outage/restart recovery. Task 08–13
verification added automated tests where message counts and failure injection
needed measurement; the final suite has 28 passing tests (172 dependency
deprecation warnings). Bot strict mypy and Ruff checks pass. Full backend
mypy still has the same nine baseline errors noted above.

The owner confirmed real teacher signup, returning `/start`, invitation-based
student signup with an hours-based reminder, and student returning `/start`.
Invalid timezone feedback was confirmed as an edit of the same message using
a temporary metadata-only Bot API trace. An initial report of an unchanged
prompt did not recur; its original cause was not established.

Final disposable SQLite/PostgreSQL checks passed: upgrade, preserved existing
work settings/codes, invalid-code preflight refusal, Telegram-binding downgrade
refusal without data loss, downgrade and re-upgrade. Earlier backend checks
also covered duplicate-code preflight, atomic rollback and concurrent signup.
The final image builds and imports production bot dependencies, contains HTML
assets, and excludes `.env`. Both Compose configurations validate; environment
separation was checked without printing secrets. Local backend/live bot startup
and graceful poller replacement were verified. Backend outage/retry is tested
with injected transport failures, not by interrupting the live user's backend.

The default local Compose stack (PostgreSQL and backend; no optional bot
profile) was exercised on 2026-10-31 with isolated project resources. A real
separate-server HTTPS deployment remains an external acceptance check, not a
claim of verified production operation.
Scheduling enforcement, notification delivery, offline schedule cache,
account linking and post-onboarding settings UI remain outside this milestone.

| ID | Task | Prerequisites |
|---|---|---|
| 00 | Approve settings/API decisions | None |
| 01 | Add Telegram identity and credentialless-account schema | 00 |
| 02 | Add missing onboarding preference fields | 00, 01 |
| 03 | Make preference schemas/services safe for onboarding | 02 |
| 04 | Authorize profile access and expose current-account preferences | 01, 03 |
| 05 | Make account/profile/link creation composable and atomic | 01, 03 |
| 06 | Implement trusted Telegram session adapter | 01, 04 |
| 07 | Implement atomic Telegram registration and invitation resolution | 05, 06 |
| 08 | Bootstrap separate bot and prove editable-screen behavior | 00 |
| 09 | Add backend client, login routing, and outage handling | 06, 07, 08 |
| 10 | Build teacher registration dialog | 03, 07, 09 |
| 11 | Build student registration and invitation dialog | 03, 07, 09 |
| 12 | Wire local/production bot deployment | 08, 09 |
| 13 | Verify complete flows and update feature documentation | 04, 07, 10, 11, 12 |

After approval, Task 08 can run alongside the backend chain. Tasks 10 and 11 can run in parallel, but coordinate changes to shared start routing and widgets. Task 12 can run alongside the dialogs. Do not let separate agents create competing Alembic heads or independently edit shared schemas without coordinating.

### Common implementation acceptance criteria

Apply these to every implementation task (01–13), in addition to its local criteria:

- Use existing domain patterns, explicit cross-domain imports, Pydantic v2, async SQLAlchemy/HTTP, and `Annotated` dependencies.
- Reuse [test fixtures](../../tests/conftest.py); use a real database for backend integration tests. Mock Telegram API transport only at the external boundary. Do not call the real Telegram API in automated tests.
- Run targeted tests for touched behavior plus the existing auth/scheduling smoke test when auth compatibility changes. Do not use the developer's `dev.db` as a test database.
- Run `uv run ruff check --fix backend`, `uv run ruff format backend`, and `uv run mypy backend`. For bot changes, also run Ruff and strict mypy on `telegram`; include touched tests in Ruff checks.
- Record unrelated baseline failures rather than silently “fixing” unrelated modules or claiming all checks passed.
- For schema changes, add a reviewed static migration named `YYYY-MM-DD_slug.py`; do not blindly accept autogenerated migrations. Test SQLite and PostgreSQL upgrades/downgrades on disposable databases.
- Surface specific failures with repository-standard HTTP errors / bot error screens and safe logging. Never broadly catch exceptions and return a success-shaped default.
- No credentials, JWTs, full registration payloads, or bot tokens in logs or committed files.

## Task 00: Approve the unresolved onboarding contract

### Owner-approved decisions (2026-10-14)

- Execute Tasks 00–07 sequentially: analyze, implement, verify, then proceed. Manual HTTP/database checks are sufficient; new test files are not required.
- Use one common `User` model, with Telegram identity fields directly on it. Telegram-only accounts have no email/password and can authenticate only through the trusted Telegram adapter. Adding passwords and merging accounts are out of scope.
- Store working days as a non-empty JSON list of unique ISO weekdays 1–7. Work hours are same-day, end > start; max-consecutive <= max/day.
- Daily digest uses local minutes since midnight (0–1439), lesson reminders use minutes (1–600), homework uses positive days. Null disables a notification.
- Lesson-reminder UI requires an explicit hours/minutes choice and converts to backend minutes.
- New Telegram accounts start with enabled notifications: digest 540, lesson 15, homework 2.
- Owner confirms no existing notification data needs preservation and approves resetting existing notification preferences to those defaults. Other existing settings remain unchanged. Duplicate/invalid invitation codes require remediation approval, not silent replacement.
- Approve the proposed service-authenticated API routes and HTTPS trust boundary; backend trusts authenticated bot sender assertions. No empty/default service credential grants access.
- Approve memory-only wizard drafts/token cache, restart of incomplete wizards, and backend-only authentication.
- API errors use `{"detail": ...}`: validation 422; bad/missing bot credential 401; unconfigured adapter 503; unknown Telegram sender 404 with `telegram_identity_not_found`; inactive account 400; duplicate registration 409 with `telegram_already_registered`; invalid/inactive invitation 404 with `teacher_code_not_found`.
- Session/register return the existing `Token` shape. Registration uses a discriminated teacher/student role and forbids extra fields; teacher codes are server-generated. Resolution returns only teacher ID and display name.

**Domain:** product/API contract (documentation)
**Type:** planning
**Dependencies:** none

### Context

[Start](../../README.md#старт) supplies defaults but not all storage/validation semantics. [Auth models](../../backend/auth/models.py) currently say weekends are stored as weekly events, and notification integers have no explicit units.

### What to do

- Obtain owner approval for the proposed settings representations, including a dedicated working-days field instead of onboarding-created recurrent events.
- Confirm whether notification toggles start enabled. Proposed: enabled at the README's stated default times/intervals for new accounts; existing accounts' null/off preferences stay unchanged.
- Confirm local daily-digest storage as minutes since midnight, homework units as days, same-day working hours, and max-consecutive <= max/day.
- Determine how to treat existing non-null notification values and duplicate/invalid teacher codes. Do not silently reinterpret existing data or regenerate invitation codes.
- Approve the proposed API route names, trusted-bot service-credential boundary, and memory-only draft/restart behavior.
- Record approved choices in this file; adjust dependent tasks before implementation.

### Reuse

README defaults and existing preference columns; do not invent additional product settings.

### Acceptance Criteria

- [x] Every unresolved item has an owner-approved answer, including migration treatment for existing values.
- [x] API request/response shapes and status/error codes are concrete enough for backend and bot agents to work separately.
- [x] Passwordless authentication remains the already confirmed choice; no account linking is added.

### Out of scope

Code, scheduling/timezone-storage decisions for events, notification delivery.

### Suitable for

Human owner with AI assistance; requires product approval. Dependent implementation tasks must stop if this contract remains unresolved.

## Task 01: Add Telegram identity and support credentialless accounts

**Domain:** `backend/auth/` (existing), `backend/telegram/` (stub)
**Type:** schema/migration
**Dependencies:** 00

### Context

[Auth models](../../backend/auth/models.py) require email/password. [Start](../../README.md#старт) does not request either.

### What to do

- Add nullable unique `BigInteger` sender ID and nullable private chat ID directly to `User`, using its existing timestamps and model discovery. No additional identity model/table is needed.
- Make `User.email` / `hashed_password` nullable with correct ORM annotations; preserve email uniqueness for populated values. Keep email/password mandatory in the existing `UserCreate` password-registration schema.
- Adjust public account serialization to support null email without exposing hashes or service credentials.
- Adapt `authenticate` so credentialless, unknown, and invalid-password logins fail with normal invalid credentials, not hash-parser errors. The existing literal dummy Argon2 string is not a usable dummy hash; use a valid precomputed dummy hash for the failure path and retain password timing protection.
- Preserve existing password account data and functionality. No synthetic credentials.
- Define a non-destructive downgrade policy: restore non-null credentials only when data permits; otherwise fail clearly before destructive work and document remediation. Never delete Telegram accounts or invent credentials to make downgrade pass.

### Reuse

`User`, `Base`, `TimestampMixin`, `authenticate`, `hash_password`, `verify_password`, existing email/password schemas.

### Acceptance Criteria

- [x] Telegram IDs above signed 32-bit range round-trip; duplicate sender/user bindings are rejected by the DB.
- [x] Credentialless user serialization succeeds; password login for it and unknown emails returns the intended 401.
- [x] Existing email/password registration/login still works.
- [x] Upgrade preserves existing rows; downgrade succeeds on compatible data and fails safely on credentialless rows.
- [x] Common implementation checks pass.

### Out of scope

Adapter endpoints, linking existing accounts, bot screens.

### Suitable for

AI agent after Task 00.

## Task 02: Add missing onboarding settings and unique teacher codes

**Domain:** `backend/auth/` (existing)
**Type:** schema/migration
**Dependencies:** 00, 01

### Context

[Start](../../README.md#старт) requires working days and maximum consecutive lessons, which [TeacherProfile](../../backend/auth/models.py) lacks. Teacher codes are not unique today.

### What to do

- Add approved working-days and max-consecutive fields with explicit defaults and portable SQLite/PostgreSQL storage.
- Align new-account teacher defaults with the README: work times 09:00–18:00, weekdays Monday–Friday, lesson length 60, break 5, consecutive 4, daily 6.
- Backfill new missing fields according to Task 00; reset notification preferences to approved defaults. Do not overwrite existing explicit break/work-hour choices.
- Add unique teacher-code protection. Preflight collisions and report actionable remediation rather than deleting profiles, silently changing codes, or failing after partial mutation.
- Remove/update the now-misleading weekends-as-events model comment if Task 00 approved a preference field.

### Reuse

Existing `TeacherProfile` fields and Alembic batch-alter patterns.

### Acceptance Criteria

- [x] Fresh profiles have the specified defaults; existing explicit values survive upgrade.
- [x] Working days and consecutive limits persist correctly in both supported databases.
- [x] Duplicate codes fail deterministically, including existing-data preflight.
- [x] Migration is reversible without rewriting existing settings.
- [x] Common implementation checks pass.

### Out of scope

Enforcing work hours/days/limits in event validators; automatic break events.

### Suitable for

AI agent after approved data-migration decisions.

## Task 03: Validate and safely update onboarding preferences

**Domain:** `backend/auth/` (existing)
**Type:** schemas/service
**Dependencies:** 02

### Context

[Auth schemas](../../backend/auth/schemas.py) and [profile services](../../backend/auth/service.py) do not cover all onboarding settings and can reset omitted values.

### What to do

- Extend teacher/student create/public/update schemas with approved preference fields, units, bounds, defaults, and shared typed validation.
- Validate IANA timezone using specific expected exceptions; validate work start/end together, weekday lists, positive durations/limits, non-negative break, and approved notification units.
- For partial profile updates, validate the merged current+requested state; omission preserves values, explicit null disables only nullable notifications, and null for required settings is rejected.
- Preserve the existing teacher invitation code unless a caller explicitly requests a supported code change. Do not generate a new code because the field was omitted.
- Keep canonical API time format `HH:MM`; flexible human input belongs in the bot parser.
- Keep existing route methods compatible; adding PATCH is not required if current update routes adopt documented partial-update semantics.

### Reuse

`TeacherProfileCreate/Public/Update`, `StudentProfileCreate/Public/Update`, `string_to_time`, profile service functions.

### Acceptance Criteria

- [x] Valid README defaults round-trip; invalid timezone, duplicate/out-of-range weekdays, work end <= start, and inconsistent limits fail explicitly.
- [x] Reminder lead time 600 is accepted and 601 rejected; zero-minute break is accepted; invalid negative values fail.
- [x] Digest minute 0 and 1439 are accepted; 1440 rejected.
- [x] Omitted values and explicit null have tested distinct behavior; unrelated edits preserve invitation codes and preferences.
- [x] Common implementation checks pass.

### Out of scope

Sending notifications, schedule validation, new unrelated profile settings.

### Suitable for

AI agent.

## Task 04: Authorize profile access and expose current-account preferences

**Domain:** `backend/auth/` (existing)
**Type:** router/dependencies
**Dependencies:** 01, 03

### Context

The bot needs safe account/profile reads. Existing role-profile routes in [auth router](../../backend/auth/router.py) accept any authenticated caller for any user ID.

### What to do

- Add reusable owner-or-existing-superuser authorization for all teacher/student profile create/read/update routes; teacher status alone must not grant access to unrelated teacher/student profiles.
- Expose role and nullable teacher linkage through current-account output; include approved timezone/digest fields without exposing sensitive data.
- Extend `UserUpdateMe` and self-update service handling for timezone and digest. Explicit null must disable a digest, which current `update_user`'s blanket null-skipping would prevent.
- Keep self-service privilege fields forbidden. Restrict public password registration to student/teacher roles; preserve superuser-protected administrative account creation.
- Document relevant response models/errors in OpenAPI and preserve existing paths.

### Reuse

`CurrentUser`, `SuperUser`, `get_current_active_user`, `UserPublic`, `UserUpdateMe`, `InsufficientPermissions`, existing routes.

### Acceptance Criteria

- [x] Owner and superuser behavior is tested for all six role-profile routes; unrelated callers receive 403.
- [x] Credentialless `/me` returns role, timezone, nullable email and linkage; self-update cannot alter role/privileges.
- [x] Explicit null disables daily digest; omitted digest is unchanged.
- [x] Public registration rejects ADMIN; existing teacher/student password flows remain valid.
- [x] Common implementation checks pass.

### Out of scope

Teacher-managed student histories, all-domain access audit, unrelated event permissions.

### Suitable for

AI agent.

## Task 05: Make account, profile, and teacher linking transaction-composable

**Domain:** `backend/auth/` (existing)
**Type:** service
**Dependencies:** 01, 03

### Context

Existing service functions each commit independently. Telegram onboarding needs one durable success or no created account at all.

### What to do

- Extract narrowly scoped internal row-building / flush helpers for common account/profile creation and teacher-code resolution, or equivalently separate transaction ownership cleanly.
- Preserve existing public service/route commit behavior through wrappers; avoid changing every domain's transaction handling.
- Reuse a single teacher lookup/validation helper for `link_student_to_teacher` and Telegram onboarding. Require active teacher role and a valid profile.
- Generate a server-side, deep-link-safe invitation code for new teachers; rely on DB uniqueness and handle collisions with bounded retries around the specific constraint.
- Ensure the onboarding caller owns one transaction covering user (including Telegram identity), profile, and student-teacher association. Read queries may autobegin a SQLAlchemy transaction; structure transaction boundaries deliberately rather than nesting `session.begin()` blindly.

### Reuse

`create_user`, `create_teacher_profile`, `create_student_profile`, `link_student_to_teacher`, `get_user_by_id`, teacher-code exceptions.

### Acceptance Criteria

- [x] Injected profile/link failure rolls back all onboarding rows.
- [x] Existing password registration and `/me/teacher` retain documented behavior.
- [x] Inactive/non-teacher/unknown invitation targets are rejected.
- [x] Code collision behavior is bounded and testable; unrelated DB errors are not swallowed.
- [x] Common implementation checks pass.

### Out of scope

Telegram routes and UI, generic repository/unit-of-work framework.

### Suitable for

AI agent.

## Task 06: Implement the trusted Telegram login/session adapter

**Domain:** `backend/telegram/` (stub)
**Type:** config/dependency/service/router
**Dependencies:** 01, 04

### Context

[Architecture](../../README.md#архитектура) separates bot/backend servers. Raw Telegram user IDs are not public authentication credentials.

### What to do

- Add domain settings and a bot-service credential dependency with secure comparison, explicit missing/invalid configuration behavior, and redacted logging.
- Implement the approved session endpoint: resolve existing identity, load common account, reject inactive users, refresh private chat routing as appropriate, and issue the existing user JWT.
- Return approved machine-readable unknown-identity response for onboarding; never auto-create an account on login.
- Mount the router in [backend app](../../backend/main.py). Do not enable adapter access with an empty/default credential.
- Define typed request/response schemas and documented error responses. Identity fields come only from the authenticated bot service.

### Reuse

`get_user_by_id`, `create_access_token`, existing active-user/errors, `SessionDep`, common JWT dependencies.

### Acceptance Criteria

- [x] Missing/wrong service credentials fail before identity lookup or mutation.
- [x] Unknown sender receives the agreed 404; known active sender receives a JWT usable with `/auth/me`.
- [x] Inactive accounts receive no token; username changes do not change identity.
- [x] JWT and bot-service credentials are not interchangeable.
- [x] Common implementation checks pass.

### Out of scope

Telegram Login Widget/WebApp signature verification, refresh tokens, user account linking.

### Suitable for

AI agent.

## Task 07: Implement atomic Telegram registration and invitation resolution

**Domain:** `backend/telegram/` (new adapter), `backend/auth/` (reused)
**Type:** service/router
**Dependencies:** 05, 06

### Context

[Start](../../README.md#старт) distinguishes teacher/student registration and student teacher codes.

### What to do

- Implement the approved registration route with teacher/student-specific validated preference inputs and trusted sender identity.
- Create common user with Telegram identity fields, role profile, and required student teacher link in the transaction from Task 05; defaults and constraints come from Task 03.
- Implement invitation resolution with minimal teacher confirmation data; do not expose email, schedules, or unrelated profile settings.
- Revalidate invitations inside registration. Generate teacher codes on the server, not from bot-supplied profile code.
- Define repeat/concurrent registration behavior: same Telegram sender must never produce two users or change an existing role/teacher. Prefer a clear already-registered conflict plus session recovery; handle the specific identity uniqueness race with rollback.
- Return a JWT only after durable success. Do not accept arbitrary privilege flags or assign role from an existing invitation to an already registered account.

### Reuse

Task 05 transaction helpers, Task 03 schemas/validation, Task 06 service auth, `TeacherProfile`, `StudentProfile`, JWT helper.

### Acceptance Criteria

- [x] Teacher registration creates one shared user/profile and unique code; student registration also links the intended active teacher.
- [x] Invalid code/settings leave zero partial registration rows.
- [x] Repeated/concurrent requests create exactly one account; conflicting second payload cannot change it.
- [x] Unknown/inactive/invalid-role teacher codes fail explicitly; direct payload privilege escalation fails.
- [x] Response-loss recovery via session lookup works without duplicating accounts.
- [x] Common implementation checks pass.

### Out of scope

Teacher transfers, deleting accounts, email-account linking.

### Suitable for

AI agent.

## Task 08: Bootstrap the separate bot and prove editable-screen behavior

**Domain:** `telegram/` (stub)
**Type:** bot/runtime
**Dependencies:** 00

### Context

[Bot interface](../../README.md#интерфейс-бота) explicitly calls for aiogram and message editing.

### What to do

- Add compatible aiogram 3 / aiogram-dialog 2 runtime dependencies with `uv`; promote httpx from dev-only to runtime dependency and update packaging so the bot package is included.
- Add bot-domain settings for Bot API token, backend URL, and bot-service credential. No import of backend settings/models/database/services from the bot.
- Provide a separate `python -m telegram` entrypoint, aiogram polling lifecycle, graceful shutdown, and one-process ownership of polling.
- Build a small reusable screen/navigation layer using aiogram-dialog, not a replacement framework. Prove two inline-driven screens plus a text-input step before building full wizards.
- Use EDIT for callback transitions and explicitly for input validation/transitions. Initial render may send; replacement may send only after a classified uneditable/missing-message failure.
- Acknowledge callbacks, handle stale states, and serialize per-user updates using aiogram-supported event isolation. Escape user-derived text before formatted display.
- Restrict onboarding to private chats. Document memory-only drafts and restart behavior.

### Reuse

aiogram `Dispatcher`, `Router`, FSM/event-isolation facilities; aiogram-dialog `Dialog`, `Window`, widgets and `ShowMode`.

### Acceptance Criteria

- [x] Dependency resolution/imports work on Python 3.14 with committed lockfile.
- [x] Automated Bot API request recording proves initial send then inline edits with the same message ID, with zero extra sends during normal transitions.
- [x] Text-input success and validation error also edit the same bot message.
- [x] Missing/uneditable message triggers one controlled replacement; arbitrary Telegram failures do not trigger endless sends or broad catches.
- [x] Callback acknowledgement, stale-state recovery, and double-click isolation are tested.
- [x] Common implementation checks pass, including bot type checks.

### Out of scope

Backend integration, full onboarding, Redis, multi-instance deployment, webhook hosting.

### Suitable for

AI agent. Invoke the bounded fallback decision only after a demonstrated library blocker.

## Task 09: Add the bot backend client, returning-user routing, and outage handling

**Domain:** `telegram/` (new)
**Type:** bot/client
**Dependencies:** 06, 07, 08

### Context

[Architecture](../../README.md#архитектура) requires HTTP separation and circuit-breaker recovery. Login must not rely on stale cached account data.

### What to do

- Add one lifecycle-managed `httpx.AsyncClient` and typed methods for session, registration, invitation resolution, `/me`, and role-profile reads.
- Attach service credentials only to adapter calls and user JWTs only to user calls. Never expose either to Telegram message/callback content.
- Implement finite timeouts and a small circuit breaker with closed/open/half-open transitions, one recovery probe, configurable bounded recovery timing, and clear unavailable UI. Count transport errors/5xx, not user validation/401/404 responses, as availability failures.
- Route `/start`: sender identity comes from the update; returning user opens home based on backend role; unknown user opens onboarding. JWT cache keys use Telegram sender ID.
- Handle expired user JWT once by obtaining a new session token and retrying only a safe read. Preserve explicit inactive/permission responses.
- On registration timeout/ambiguous response, resolve the sender before offering another submit; do not blindly replay account creation.
- Implement an honest minimal home screen: role, confirmed account/settings summary, teacher invitation if applicable, no clickable unimplemented schedule/homework actions.

### Reuse

Existing backend JWT contract, Task 08 dialogs, httpx transport/testing tools.

### Acceptance Criteria

- [x] Returning users bypass registration; restart followed by `/start` logs them in again.
- [x] Token expiry, backend timeout, 5xx, unknown identity, inactive user, and validation failure follow distinct tested paths.
- [x] Open breaker avoids repeated backend traffic; half-open recovery permits a bounded probe and returns to closed on success.
- [x] Backend outage never creates an account locally or reports successful registration/login.
- [x] Responses after lost registration replies are recovered without duplicates.
- [x] Common implementation checks pass.

### Out of scope

Read-only schedule cache, offline login, background notifications, generic resilience framework.

### Suitable for

AI agent.

## Task 10: Build teacher registration screens

**Domain:** `telegram/` (new)
**Type:** bot/dialog
**Dependencies:** 03, 07, 09

### Context

[Start](../../README.md#старт) lists all teacher onboarding preferences and their defaults.

### What to do

- Implement the teacher wizard sequence defined above, with visible defaults, Back/Cancel, weekday multiselect, review/edit, and a single final backend submission.
- Add only the shared input helpers needed here: validated IANA timezone selection/input, integer settings, and flexible clock parser accepting at least `09 30`, `09:30`, `09/30`, normalizing to `HH:MM`.
- Client-side feedback complements, never replaces, backend validation. Do not parse dates yet; account onboarding has no date inputs.
- Preserve draft values when navigating or receiving backend validation errors; prevent duplicate submits.
- After success, show the backend-generated teacher code and valid invitation link from aiogram's deep-link helper.
- Label notification delivery and scheduling enforcement as not yet available while saving the requested preferences.

### Reuse

Task 08 dialog layer, Task 09 client, aiogram-dialog input/multiselect/navigation widgets, backend settings constraints.

### Acceptance Criteria

- [x] Full wizard creates a teacher with all chosen settings and correct README defaults.
- [x] Flexible valid clock formats work; invalid times, timezone, and inconsistent limits preserve the draft and show actionable errors.
- [x] Back/edit/cancel do not create accounts; submit failures do not lose input.
- [x] Inline navigation and text-input responses use the active message, as measured by mocked Bot API calls.
- [x] Invitation payload is valid and within Telegram limits.
- [x] Common implementation checks pass.

### Out of scope

Editing settings after onboarding, date parsing, schedule creation, actual notification delivery.

### Suitable for

AI agent.

## Task 11: Build student registration and teacher invitation screens

**Domain:** `telegram/` (new)
**Type:** bot/dialog
**Dependencies:** 03, 07, 09

### Context

[Start](../../README.md#старт) permits student registration with an invitation or manually entered teacher code.

### What to do

- Parse short `/start t_ABC123` payloads via aiogram `CommandStart` / command arguments. Generate invitations with the matching `create_start_link` contract; do not add a second prefix.
- Unknown invited user goes directly to teacher confirmation/student onboarding. Plain `/start` offers role selection; student without invitation enters a code.
- Implement timezone, digest, pre-lesson and homework preference screens, review/edit, Back/Cancel, and single submission using shared inputs from Task 10 when available. Coordinate extraction rather than duplicate parsers.
- Unknown/malformed/inactive-teacher invitation shows a recoverable code-entry error. Final registration remains backend-validated.
- Already registered users keep their identity, role, and teacher even if a different invitation is presented.

### Reuse

Task 08 dialogs, Task 09 backend client, shared input widgets/helpers, backend invitation resolution and atomic registration.

### Acceptance Criteria

- [x] Valid invitation and manually entered code both create a student linked to the correct teacher.
- [x] Malformed/unknown/inactive codes produce no partial account and permit correction.
- [x] Notification toggles/defaults and the 600-minute maximum are correctly submitted; disabled preferences send explicit null.
- [x] A returning teacher/student opening an invitation is never re-registered, converted, or transferred.
- [x] Back/cancel/review and edit-in-place behavior are tested.
- [x] Common implementation checks pass.

### Out of scope

Teacher transfer, existing-account linking, student history, homework workflows.

### Suitable for

AI agent; coordinate shared helper ownership with Task 10.

## Task 12: Wire separate local and production bot deployment

**Domain:** runtime/deployment
**Type:** bot/infrastructure
**Dependencies:** 08, 09

### Context

[Architecture](../../README.md#архитектура) requires both same-machine development and different-server deployment. The existing image does not include the bot package.

### What to do

- Extend packaging/image build deliberately to include the bot runtime, with separate backend and bot commands/services. Do not start polling inside FastAPI lifespan or run backend migrations in the bot entrypoint.
- Update both Compose files with a separate bot service and bot-specific environment variables; only backend receives database/JWT-signing credentials, only bot receives the Bot API token, both receive the dedicated service credential.
- Support a remote HTTPS backend URL as well as local Compose URL. Do not require shared volumes/database access.
- Supply placeholder-only environment examples and exact `uv` launch commands. Document single-poller operation and rotation of the bot-service credential.
- Ensure a bot started during backend outage remains operational enough to present an unavailable screen and recovers after backend return.

### Reuse

[Dockerfile](../../Dockerfile), [local Compose](../../docker-compose.yml), [production Compose](../../docker-compose.prod.yml), existing uv lockfile.

### Acceptance Criteria

- [x] Same-machine backend/bot launch is documented; local backend/live-bot startup is smoke-tested.
- [x] Separate-server configuration is documented and both Compose files validate.
- [x] Full default local Compose stack startup is verified; the optional bot profile was not started.
- [ ] Real separate-server HTTPS deployment is smoke-tested.
- [x] Image includes required bot modules and production dependencies.
- [x] Service environment separation is verified without printing secret values.
- [x] Graceful stop/start does not leave a duplicate poller; backend outage/retry behavior is tested with injected transport failures.
- [x] Common implementation checks pass. Use configured container tooling for any container commands.

**Reconciliation verification (2026-10-31):** Local Compose migrations reached head, the backend served `/docs` (HTTP 200), the named homework volume was mounted and initialized with mode 0700, and the backend stopped cleanly (exit 0). After restart, readiness returned HTTP 200 and storage permissions persisted. Production Compose configuration also validated with `.env.example`. The isolated stack and volumes were removed; the pre-existing development stack remained responsive. No bot poller was started. Real separate-server HTTPS remains unchecked pending an owner-approved target and test access.

### Out of scope

Kubernetes, webhook ingress, multiple bot replicas, infrastructure unrelated to this milestone.

### Suitable for

AI agent; real Telegram manual smoke test needs owner-provided credentials outside committed files.

## Task 13: Verify end-to-end onboarding and update feature status

**Domain:** `tests/`, `telegram/`, project documentation
**Type:** integration/documentation
**Dependencies:** 04, 07, 10, 11, 12

### Context

Completion must demonstrate the actual README flow and message-edit behavior, not just endpoint existence.

### What to do

- Add integration coverage combining simulated aiogram updates, mocked Bot API transport, the real ASGI backend, and the isolated test DB. Do not mock backend account services or the database.
- Cover teacher signup → generated invitation → student signup → returning `/start` → backend `/me`/profile reads.
- Cover plain student code entry, defaults/customization, disabled notifications, invalid input, inactive accounts, double-submit, stale callbacks, expired JWT, restart recovery, outage/circuit recovery, and a lost registration response.
- Measure Bot API calls/message IDs for normal inline and text-input transitions; verify no new bot message per step.
- Run migration upgrade/downgrade checks on disposable SQLite/PostgreSQL databases and the relevant existing smoke suite.
- Update [README](../../README.md) with real deep-link/login/run instructions without erasing the Russian product spec; update [AGENTS.md](../../AGENTS.md) Feature Status and stale test information.
- Document exactly what remains unimplemented: scheduling enforcement, notifications, offline schedule cache, account linking, and post-onboarding settings UI.

### Reuse

[Existing fixtures](../../tests/conftest.py), [existing smoke test](../../tests/test_smoke.py), backend dependency overrides, Bot API transport mocks.

### Acceptance Criteria

- [x] End-to-end teacher/student flows persist correct backend rows and return working JWTs.
- [x] Inline navigation and text-input updates satisfy measured send/edit counts and message-ID continuity.
- [x] Invalid, repeated, concurrent, unauthorized, and outage paths cannot create partial/duplicate accounts or report false success.
- [x] Password registration/login and existing scheduling smoke behavior remain intact.
- [x] Deployment instructions and feature-status descriptions match shipped behavior.
- [x] Common implementation checks pass; test evidence and any baseline failures are recorded in the completion report.

### Out of scope

Implementing the rest of the product spec to make documentation look complete.

### Suitable for

AI agent for automated checks/documentation; human for final real-bot UX smoke test.

## Out of scope for this milestone

- Schedule day/week endpoints, timelines, offline schedule cache.
- Scheduling enforcement of saved teacher preferences, automatic between-lesson breaks, vacation priority rules.
- Notification scheduler/sending, homework upload/marking, broadcasts.
- Flexible date parsing and next-year rollover (no onboarding date input).
- Telegram-to-existing-email-account linking, teacher transfers, logout/account deletion.
- HTMX registration/login UI, Telegram Login Widget/WebApp, other messenger clients.
- Persistent wizard drafts, Redis, webhooks, horizontally scaled bot workers.
- Unrelated pre-existing event/router/type-check issues.

## Open questions / execution gates

1. Task 00 choices are approved above; dependent tasks must follow them.
2. Service-authenticated API contract and deployment trust boundary are approved above.
3. Library fallback is gated on a measured compatibility failure in Task 08; message-by-message UX requires owner approval if the small fallback is not viable.
4. If existing production teacher codes collide or notification values have different units, stop and obtain a remediation decision. Do not silently alter live invitations or reinterpret reminders.
5. This milestone saves notification/scheduling settings without executing their effects. UX and completion documentation must state that limitation.
