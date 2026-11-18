# Telegram UI Cleanup — Plan

**Status:** Implemented and verified by automated tests (2026-11-17); no live
Telegram or PostgreSQL checks were performed.

**Superseded in part (2026-11-18):** its student-list label, `Refresh` and
per-kind list behavior are replaced by
[telegram-lists-and-breaks-cleanup](./telegram-lists-and-breaks-cleanup.md).

## Goal
Declutter the Telegram bot: move Settings/Vacations into the Telegram command
menu, tighten the week schedule, settings, student list, broadcast and homework
screens, and fix the homework-published notice formatting on the home screen.

## Scope (README.md sections covered)
- Telegram bot / schedule viewing / settings / student profiles / broadcast /
  homework presentation (AGENTS.md Feature Status: all **Partial** or **Done**).
  No backend, API, migration or scheduling-rule changes: every task is in the
  top-level `telegram/` client plus its tests. No schema change, so no Alembic
  migration is needed.
- Source: `todo.txt` (repository root). It is not part of any shipped contract
  and may be deleted once this plan is implemented.
- This plan **supersedes** the following existing presentation contracts; update
  the linked docs in Task 8: the week header and `Updated`/timezone ordering in
  [telegram-interface-streamlining](./telegram-interface-streamlining.md), the
  `[PAST]` marker described in AGENTS.md ("unchanged past/ongoing markers"),
  and the "former students" lists in
  [teacher-student-profiles-and-removal](./teacher-student-profiles-and-removal.md).

## Findings from the code (read before implementing)
- Home buttons are in `telegram/dialogs/home.py` (`Screens.home` window).
  `open_settings` ([settings.py](../../telegram/dialogs/settings.py)) and
  `open_vacation_management` ([vacations.py](../../telegram/dialogs/vacations.py))
  are `CallbackQuery` handlers; there are no bot commands today (only
  `CommandStart` in [runtime.py](../../telegram/runtime.py), no
  `set_my_commands` in [__main__.py](../../telegram/__main__.py)).
- Week/day formatting is `format_schedule_pages` in
  [schedule/formatting.py](../../telegram/dialogs/schedule/formatting.py). It has
  no "now" input; `generated_at` is the backend snapshot time. Status text is
  `f"[{item.status.upper()}]"` (only `upcoming` is already an emoji).
- The home-screen bug has two causes in `home.py`. (a) `screen_data` appends
  the `homework_notice` line without a trailing `\n`, so `Teacher code: …` is
  glued onto it. (b) `wizard_result` merges the result into the home dialog's
  `dialog_data`, and `homework_notice` is never cleared, so
  `Homework assignment published.` keeps reappearing on every later return to
  the menu (e.g. after marking a different homework completed — the
  management dialog's `done()` returns no result). This matches the manual
  report: publish → student receives → teacher completes → menu shows the stale
  notice with broken formatting.
- "Page N of M" strings exist in: `student_profiles.py` (`profile_page`),
  `broadcast.py` (`result_page`), `homework/teacher_creation.py`
  (`picker_page`), `homework/teacher_management.py` (`student_page`,
  `assignment_page`), `homework/student.py` (`page`), and
  `lessons/creation.py` (`picker_page`, not in the todo).
- Teacher messages are removed after acceptance by
  `telegram/shared/date_cleanup.py::delete_accepted_input_message`, whose
  `input_type` is `Literal["date", "time"]` and whose failure notice reads
  "`<Type>` accepted, but …". Broadcast's `set_broadcast_text` does not call it.
- Dead-code note: removing the "Former students" buttons leaves
  `show_former_students`, `show_former_students_after_removal`, the "Active
  students" button and `kind == "former"` branches in `student_profiles.py`
  unreachable from the UI. Backend former-profile endpoints stay.

## Tasks

### 1. Move Settings and Vacations to the Telegram command menu, add Start
**Domain:** `telegram/` (existing) · **Type:** bot

- Remove the `Vacations` and `Settings` buttons (and the now-unused
  `can_manage_vacations`/`can_edit_settings` getter keys) from the
  `Screens.home` window in `home.py`.
- Register commands `/start`, `/settings`, `/vacations` via
  `bot.set_my_commands(...)` (default scope; ideally private-chats scope
  `BotCommandScopeAllPrivateChats`) once on startup in `__main__.py`. Keep the
  call out of `build_dispatcher` so tests with the recording transport are not
  affected unless explicitly asserting it.
- Add `Command("settings")` and `Command("vacations")` message handlers on the
  existing `commands` router in `runtime.py`, restricted to
  `F.chat.type == "private"`. Each starts the existing dialog with
  `StartMode.RESET_STACK` using the same backend `view` check, role gating
  (TEACHER/STUDENT only) and error → `unavailable` fallback as the current
  callback handlers.
- Refactor the callback handlers so the dialog-starting logic takes
  `(sender_id, chat_id, manager)` and is shared between callback and command
  entry points; do not duplicate it.
- Unregistered users or ADMIN get a short "Send /start to register" style reply
  (or the dialog's existing unavailable screen) rather than a traceback.
- Verify the commands still work after restart (dialog state is memory-only;
  commands must not depend on prior state).

**Reuse:** `open_settings`, `open_vacation_management`, `SettingsScreens`,
the vacation dialog's start states, `EditInputMiddleware`.

**Acceptance Criteria**
- [x] Home no longer shows Vacations/Settings for teacher and student.
- [x] `/settings` and `/vacations` open the same screens as before for both
      roles; `/start` unchanged.
- [x] Commands are published with `set_my_commands`; `/start` is listed.
- [x] Unregistered/ADMIN/private-chat-only behavior covered by tests.
- [x] Existing tests that `driver.click("Settings")`/`click("Vacations")`
      (e.g. `test_bot_settings_dialogs.py`, `test_bot_vacation_management.py`,
      `test_bot_cross_flow.py`, `test_bot_broadcast_dialogs.py`,
      `test_telegram_offline_entry.py`) are migrated to send the commands.
- [x] `uv run ruff check telegram tests`, `ruff format --check telegram tests`,
      `mypy telegram` pass.

### 2. Week schedule header/footer and status markers
**Domain:** `telegram/dialogs/schedule/` · **Type:** bot

In `format_schedule_pages` (live source only; the cached/offline snapshot header
is a separate contract and stays unchanged):
- Remove the title line for both views (`This week's schedule` and
  `Today's schedule`); the page starts directly with the body.
- Footer becomes: blank line, `Timezone: …`, then `Updated …` (only when
  applicable). Adjust `_render_page` and the `body_limit` budget (header may now
  be empty, footer is now up to two lines plus a blank separator) so the
  3500-UTF-16-unit limit still holds for every page.
- Show `Updated …` only when the snapshot is more than one hour older than
  "now". Add a keyword-only `now: datetime | None = None` (UTC-aware) parameter
  defaulting to the current time so tests can control it; pass nothing from the
  dialog unless the dialog needs to be deterministic. Compare against
  `response.generated_at`; do not use `datetime.now()` naive.
- Replace `[PAST]` with `✅` and `[ONGOING]` with `▶️`. Keep `⏳` for upcoming. Cover the `NOW` divider
  ordering tests. Preserve plain/markup length parity in
  `_TextLine` (emoji count as 2 UTF-16 units for some code points — rely on
  `_utf16_length`, do not use `len`).
- Apply the same footer ordering to the day view. The empty-day view has no timezone footer today;
  keep that, and when `Updated` applies, show it as the last line.

**Reuse:** `_TextLine`, `_render_page`, `_utf16_length`,
`format_compact_schedule_date`, `format_human_time`.

**Acceptance Criteria**
- [x] Week and day pages: no title line; blank line precedes `Timezone:`; `Updated`
      follows the timezone and appears only when the snapshot is > 1 hour old.
- [x] No `[PAST]`/`[ONGOING]` text remains; past rows use `✅`, ongoing rows
      use `▶️`; NOW divider ordering unchanged.
- [x] Pagination never exceeds the 3500-unit budget (add a boundary test).
- [x] Update assertions in `test_schedule_format.py`,
      `test_bot_schedule_dialogs.py`, `test_bot_schedule_integration.py`,
      `test_telegram_offline_entry.py`, `test_bot_lesson_creation.py` that match
      `"This week's schedule"`, `"Updated …"` or `"[PAST]"`.
- [x] ruff/mypy for `telegram tests` pass.

### 3. Remove explanatory text from settings screens
**Domain:** `telegram/dialogs/settings.py` · **Type:** bot

- Main settings window (`"{role} settings"`): delete the
  "Account and role preferences save separately. Messages require … duplicate."
  sentence for both roles (the window is shared).
- Teacher preferences window: delete both the "Break changes apply to new
  lessons only …" and "Student solution alerts apply to newly submitted
  versions …" sentences. Keep `{profile}` and `{notice}`.
- Keep the behavior itself unchanged (break snapshots, alert revalidation).

**Acceptance Criteria**
- [x] The three removed strings no longer appear in any screen.
- [x] No stray blank lines before `{notice}`.
- [x] Settings tests updated; ruff/mypy pass.

### 4. Student list: hide single-page label, drop "Former students" buttons
**Domain:** `telegram/dialogs/student_profiles.py` · **Type:** bot

- Add a shared helper (e.g. `telegram/shared/pagination.py`:
  `page_label(page_number, page_count, total, noun) -> str`) returning `""` when
  `page_count <= 1`; reuse it in Tasks 5–6. Don't leave a dangling blank line
  when the label is empty.
- `profile_page`: omit the `Page 1 of 1 · 1 student(s)` header when there is a
  single page (keep the "No students on this list." / profile lines).
- Remove both `Former students` buttons (list window and removal-result window).
  Keep the former-list flow code (`show_former_students`,
  `show_former_students_after_removal`, the `Active students` button,
  `can_return_to_active/former` keys, `kind == "former"` branches) in place so
  it can be re-exposed later, but leave it unreachable and add one short
  comment explaining it is intentionally not exposed. Do not leave
  commented-out code blocks. Backend former-profile endpoints stay.
- On the removal-result window, `Close` remains the only exit; keep the
  stale-preview refresh and same-operation retry behavior intact.

**Acceptance Criteria**
- [x] No `Former students` button anywhere; single-page list has no page line.
- [x] Multi-page lists still show the label and Previous/Next.
- [x] `test_bot_student_profiles.py` (lines using `click("Former students")`)
      rewritten; former-profile read-only behavior is still covered at the API
      level (`tests/test_student_action_auth.py`).

### 5. Broadcast: hide single-page label and delete the teacher's message
**Domain:** `telegram/dialogs/broadcast.py`, `telegram/shared/date_cleanup.py` · **Type:** bot

- `result_page`: use the Task 4 helper (hide when one page).
- After `set_broadcast_text` accepts a valid message, delete the teacher's
  message as lessons do, then `manager.show(show_mode=EDIT)`. Generalize
  `delete_accepted_input_message` (`input_type` gains `"text"`, or the notice
  wording becomes data-driven) so the failure warning reads sensibly
  ("Text accepted, but Telegram could not remove your message…"). Keep the
  expected-failure handling (`message to delete not found`, `can't be deleted
  for everyone`) and the warning-merge behavior.
- Do not delete the message on validation failure (`broadcast_text_error`), so
  the teacher can see what they sent. The draft text must stay in
  `dialog_data` and be shown on the compose screen (it already is via
  `text_heading`/`text_page`), since the original message disappears.

**Reuse:** `delete_accepted_input_message`, `feedback_block`.

**Acceptance Criteria**
- [x] Accepted broadcast text message is deleted; compose screen shows it.
- [x] Delete failure produces a warning, not an exception.
- [x] Single-page results show no page line.
- [x] `test_bot_broadcast_dialogs.py` updated (recording transport asserts the
      delete call); ruff/mypy pass.

### 6. Homework: hide single-page labels, publish directly from compose
**Domain:** `telegram/dialogs/homework/` · **Type:** bot

- Use the Task 4 helper for `picker_page` (`teacher_creation.py`),
  `student_page` and `assignment_page` (`teacher_management.py`), `page`
  (`student.py`), and `lessons/creation.py::picker_page` (wider scope approved).
- Wider scope approved: after the teacher's homework assignment text
  (`set_assignment_text`) and the student's submission text input are accepted,
  delete the user's message with the generalized helper from Task 5, and show
  the stored text on the active screen. Do not delete on validation errors or
  for document uploads (the file message is the user's source file; keep it
  unless a product decision says otherwise). Audit other `TextInput`
  handlers in `telegram/dialogs/` for the same pattern and list any left out.
- Compose window: replace the `Review` button (`review_assignment`,
  `can_review`) with the `Publish assignment` button bound to
  `publish_assignment` (`submit_label`, `can_publish`). The Review window,
  `HomeworkScreens.review`, `review_assignment` and `return_to_compose` become
  dead; remove them. Move the validations from `review_assignment` (blank
  text + no files, 50 MiB total) into `publish_assignment` so they are not lost.
- Keep the unknown-outcome path: when `outcome_unknown` is set, the compose
  window must still be able to show `Retry publish (same operation)` and must
  block editing (today `can_edit_review`/`can_review` gate this). Confirm
  publish still works with a pending upload blocked and the operation key reused.
- Previous/Next text-page buttons must remain available on the compose screen
  (already present).

**Acceptance Criteria**
- [x] No `Review` button; `Publish assignment` available on compose when there
      is text or a file and nothing pending.
- [x] Oversized and empty drafts get an error instead of an API call.
- [x] Unknown-outcome retry reuses the same operation key and payload.
- [x] Single-page labels hidden in all four homework lists.
- [x] `test_bot_homework_*` tests updated; ruff/mypy pass.

### 7. Fix home-screen text after a homework notice
**Domain:** `telegram/dialogs/home.py` · **Type:** bot

- Build the home text from a list of lines (or terminate every appended
  fragment with `\n`) so the notice and the `Teacher code: …` block render on
  separate lines, with a blank line between the header block and the notice.
  Fix the same bug class for the existing-invitation notice and the
  student/admin trailing paragraphs.
- Make `homework_notice` one-shot: pop it from `dialog_data` when the home
  screen has rendered it (or clear it when any other dialog starts/returns), so
  it does not reappear after later flows such as marking a homework completed.
- Add regression tests: (1) exact home text after publishing; (2) after
  publish → open Manage homework → mark complete → back to menu, the stale
  notice is gone and the text is correctly formatted.

**Acceptance Criteria**
- [x] Expected output after publishing:
      `TEACHER: …\nTimezone: …\nDaily digest (local): …\n\nHomework assignment published.\nTeacher code: …`
- [x] The notice appears once and not on later menu returns.
- [x] Existing home-text assertions updated; ruff/mypy pass.

### 8. Verification and documentation
**Domain:** docs, tests · **Type:** documentation

- Run `uv run pytest -q tests` plus `ruff check/format --check` and
  `mypy telegram`; do not claim PostgreSQL or live-Telegram verification
  (neither is performed).
- Update AGENTS.md (Telegram bot / schedule viewing rows, "Scheduling and
  Telegram handoff" section) and the superseded plans listed above so
  `[PAST]`, the week title, `Updated` placement, Home Settings/Vacations
  buttons and Former students are no longer described as current.
- Update README.md only if it describes those bot menus (check; it is the
  Russian product spec).
- Mention the new BotFather-menu behavior in README deployment notes if
  commands need no manual BotFather setup (they are set at startup).

## Out of scope for this release
- Backend/API changes, former-student endpoints, schema or migration changes.
- Offline cached-schedule header wording.
- HTMX frontend.
- Deleting user messages in flows other than broadcast and homework/lesson text
  input (Tasks 5–6).

## Decisions recorded
- `✅` replaces `[PAST]`; `▶️` replaces `[ONGOING]`.
- Both the week and day titles are removed.
- The day view follows the same footer ordering as the week view.
- Settings text is removed for both roles.
- "Former students" buttons are removed; the flow code stays unreachable.
- Message deletion and single-page label hiding extend to homework text input
  and the lesson-assignee picker.
- Home bug: formatting plus a stale one-shot notice (see Findings).

## Open questions
1. Should document uploads in homework (teacher attachments, student files)
   also have the user's message deleted? Proposed: no.

## Execution record (2026-11-17)

| Task | Commit | Verification |
|---|---|---|
| 1 Commands | `678714b`, `2f8f270` | New `tests/test_bot_commands.py` (command menu publication, no home buttons, both roles, no prior `/start`, unregistered/ADMIN/group/outage) plus migrated settings, vacation, broadcast, cross-flow and offline tests. |
| 2 Schedule | `e0bae9f` | `tests/test_schedule_format.py` (blank line, `Updated` boundary at exactly one hour, empty day, cached header unchanged, 3500-unit budget with the two-line footer) and migrated dialog/integration tests. |
| 3 Settings text | `ec29a19` | Exact-text assertions on the settings menu and teacher window. |
| 4 Student list | `f0978c6` | `tests/test_telegram_pagination.py`, rewritten student-profile tests (no former buttons, `Close` only after removal). |
| 5 Broadcast | `acb5227` | Recording-transport delete assertions, delete-failure warning, no deletion on invalid text, single-page results. |
| 6 Homework | `af27010` | Compose publishes directly; empty/oversized drafts rejected before an API call; unknown-outcome retry reuses the key and blocks edits; text deletion for assignment and solution input; single-page labels in all five lists. |
| 7 Home text | `dc3feae` | Exact home text after publishing; the notice is cleared on the next return. |

Notes:
- Settings and vacations start on a reset stack and rebuild home on return
  (`open_home`). aiogram-dialog derives context ids from the current second plus
  one of 100 random values, so stacking two contexts in one update could collide.
- The `Updated` check compares the backend snapshot with the bot's wall clock;
  tests that mock the backend clock therefore see `Updated` unless they pass `now`.
- Decision on the open question: document uploads (teacher attachments, student
  files) are not deleted, as proposed.
- Text inputs left unchanged (out of scope): lesson duration, onboarding timezone,
  teacher code and wizard values, and settings field edits.
- Existing flakiness: aiogram-dialog id collisions occasionally fail unrelated
  bot tests in full runs (also seen on the pre-change baseline); a rerun passes.
  `tests/test_notification_delivery.py::test_notification_service_fails_closed_when_not_configured`
  fails on the baseline too (it expects 503 but the local `.env` configures the
  service credential, giving 401).
