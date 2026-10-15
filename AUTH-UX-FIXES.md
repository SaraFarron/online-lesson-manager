# Auth onboarding UX fixes

Follow analyze -> implement -> verify for each task. Finish verification before
starting the next task. Stop for an unanticipated product decision or required
scope change. Automated tests are needed only where manual Telegram/API checks
cannot reliably verify the requirement.

Owner-approved scope:
- Make review edit buttons readable on Telegram mobile.
- Use short teacher codes, including replacing existing codes. Previously
  shared links may stop working; retaining old-code aliases is not required.
- Target format: `t_` followed by exactly six uppercase ASCII letters/digits,
  for example `t_ABC123`. New deep links use `?start=t_ABC123` directly.

## Task 01: Make registration review edit buttons readable

**Domain:** `telegram/` (existing)
**Type:** bot
**Dependencies:** None

### Context

[README onboarding](./README.md#implemented-telegram-flows-and-limits) supports
review/edit before registration. The review `Select` in
[onboarding.py](./telegram/onboarding.py) currently renders all edit choices
without an explicit row layout. On Telegram mobile, labels truncate to
indistinguishable "Edi..." buttons.

### What to do

- Give review edit choices an explicit one-button-per-row layout using
  aiogram-dialog's existing layout widgets.
- Use concise, distinct setting names rather than repeating an "Edit" prefix;
  put the instruction to select a setting in the review text.
- Keep Register and Back/Cancel separate from setting choices.
- Apply the shared layout to teacher and student reviews.
- Preserve callback identifiers, field-to-step mapping, draft values,
  validation, return-to-review behavior, and edit-in-place transitions.
- Update directly related documentation only where behavior changes.

### Reuse

Reuse `build_wizard`, `wizard_data`, `edit_setting`, and `show_step` in
[onboarding.py](./telegram/onboarding.py), plus aiogram-dialog layout widgets.
Do not create a separate review-navigation framework.

### Acceptance Criteria

- [ ] On a narrow Telegram mobile chat, every teacher setting is distinguishable
      without opening it; no grid of indistinguishable "Edi..." labels remains.
- [ ] Student review also has readable setting choices.
- [ ] Every choice opens the correct setting and returns to review after an
      accepted edit; unchanged values remain intact.
- [ ] Register, Back and Cancel retain their intended behavior.
- [ ] Normal review navigation edits the same message rather than sending
      additional messages.
- [ ] Verify the actual mobile rendering manually. Keyboard structure assertions
      alone do not prove labels are readable.
- [ ] Run relevant existing onboarding checks, Ruff/format checks, and
      `uv run mypy telegram`.

### Out of scope

Invitation changes, post-registration settings UI, redesigning the whole wizard,
or unrelated screen changes. No database migration is needed.

### Suitable for

AI agent for implementation; human for final Telegram mobile rendering check.

## Task 02: Shorten teacher codes and invitations, including existing teachers

**Domain:** `backend/auth/`, `telegram/` (existing)
**Type:** service / bot / data maintenance
**Dependencies:** None technically; execute after Task 01 verification.

### Context

[README invitation contract](./README.md#implemented-telegram-flows-and-limits)
currently uses `teacher_<code>`. `generate_teacher_code` in
[auth/service.py](./backend/auth/service.py) generates 32-character UUID hex
codes, making links unnecessarily long. The owner approved replacing existing
codes even though previously shared links may stop working.

### What to do

- Generate new codes as `t_` plus exactly six uppercase letters/digits using a
  cryptographically secure random generator. Codes are invitations, not login
  credentials; no authentication decision may rely on code secrecy.
- Reuse the database unique constraint, nested-transaction collision handling,
  and bounded retries in `add_teacher_profile`. Never treat a preliminary
  existence lookup as a replacement for the unique constraint.
- Generate `https://t.me/<bot_username>?start=t_ABC123` instead of adding another
  `teacher_` prefix. Wire both signup completion and returning-teacher home.
- Share bot invitation formatting/parsing logic between
  [runtime.py](./telegram/runtime.py) and
  [onboarding.py](./telegram/onboarding.py), rather than maintaining two rules.
- Parse short payloads strictly. Manual student code entry and backend
  resolution must accept the full displayed code, including `t_`.
- Audit `TeacherCode`, teacher-profile creation/update, trusted registration,
  API examples, and bot tests. Generated codes must use the new format.
  Preserve unrelated explicit-code API behavior unless changing it is required;
  stop for approval before imposing a new restriction on that existing API.
- Provide a one-off, explicit data-maintenance command to replace existing
  nonconforming teacher codes. Include dry-run, an explicit apply option,
  collision-safe allocation, and an all-or-nothing transaction. Leave already
  conforming codes unchanged so rerunning does not rotate links again.
- Preview the affected count and obtain confirmation immediately before applying
  to the configured database. Do not alter the developer database during tests.
  State that existing shared links will become invalid; do not add aliases.
- Ensure returning teachers fetch and display the replacement code/link rather
  than a stale cached profile. Existing student-teacher links must not change.
- Update [README.md](./README.md), examples and affected feature documentation.

### Reuse

Reuse `generate_teacher_code`, `add_teacher_profile`, `resolve_teacher_code`,
the `teacher_profile_code_key` constraint, the existing `TeacherCode` schema,
aiogram `create_start_link`, and the existing backend client profile reads.
The database already supports short unique strings: no schema migration is
required. Treat rotation as explicit data maintenance, not an automatic
application-startup side effect.

### Acceptance Criteria

- [ ] Every newly generated teacher code matches `^t_[A-Z0-9]{6}$` and has
      exactly eight characters; the invitation payload equals that code.
- [ ] The generated link contains `?start=t_XXXXXX`, with no duplicated prefix,
      and Telegram opens the correct teacher-confirmation flow.
- [ ] Manual code entry and invitation signup link students to the correct
      active teacher; invalid/inactive codes cannot create partial accounts.
- [ ] Forced collisions exercise retry and retry-exhaustion behavior without
      duplicate codes or partial registration. Use focused automated checks
      for these cases rather than waiting for a random collision.
- [ ] Dry-run performs no writes. Apply replaces every nonconforming code,
      preserves already conforming codes, and rerunning is a no-op.
- [ ] A failed rotation leaves no partially rotated set of codes. Teacher IDs,
      settings and existing student links remain unchanged.
- [ ] Returning `/start` displays the updated invitation for existing teachers.
      Old rotated codes fail resolution clearly; old-code aliases are absent.
- [ ] Verify rotation on a disposable database, then confirm the live teacher
      home and new invitation from a second Telegram account after approved apply.
- [ ] Existing password auth and bot signup/recovery checks remain passing.
      Run relevant Ruff/format/type checks; report known backend baseline
      failures separately rather than fixing unrelated issues.

### Out of scope

URL shortener services, account linking/transfers, invitation expiry or referral
tracking, old-code aliases, notification delivery, and scheduling enforcement.
Do not reset accounts or change teacher-student relationships to rotate codes.

### Suitable for

AI agent for implementation and disposable-database checks; owner confirmation
before applying code rotation and for the final real Telegram invitation check.
