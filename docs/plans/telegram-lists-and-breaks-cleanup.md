# Telegram Lists, Breaks and Refresh Cleanup — Plan

**Status:** Implemented and verified by automated tests (2026-11-18), Tasks 1–8
(commits listed in the repository history). No live Telegram or PostgreSQL
verification was performed. The full suite has one failure that predates this
work (`test_notification_delivery.py::test_notification_service_fails_closed_when_not_configured`
returns 401 instead of 503 in this environment) and a few intermittent
default-user bot tests ("Stack is broken" resets) that also fail occasionally on
the previous baseline. Source: `todo.txt` in the repository root; it
is not a shipped contract and may be deleted once this plan is implemented.

**Superseded in part (2026-11-18):** Decision 4's `Delete break` confirmation
step is removed; see [telegram-polish-and-time-slots](./telegram-polish-and-time-slots.md).

## Goal
Make the teacher student list, student profile, breaks screens and lesson list
readable on a phone (one button per row, shorter text, no empty sections), unify
the lesson and break lists, and remove every manual "Refresh" button.

## Scope (README.md sections covered)
- Telegram bot / student profile view for teacher / lessons and breaks
  management (AGENTS.md Feature Status: **Partial** / **Done**).
- Tasks 1–5 and 7 are top-level `telegram/` client changes plus tests, with no
  schema change and no Alembic migration. Task 6 (unified lists) needs a small
  **backend read-only addition** (see Decision 1); it also needs no
  migration, because the next occurrence is computed, not stored.
- This plan **supersedes** these presentation contracts; update the linked docs
  in Task 8: the two-list (one-time / weekly) lesson and break browsing and the
  `Refresh` controls described in
  [telegram-scheduling-and-settings-ui](./telegram-scheduling-and-settings-ui.md),
  [telegram-event-editor-consistency](./telegram-event-editor-consistency.md) and
  [telegram-ui-cleanup](./telegram-ui-cleanup.md). It does not change scheduling
  rules, API timestamp formats, authorization, or break-displacement semantics
  (AGENTS.md: keep confirmation and permanent-deletion semantics stable).

## Findings from the code (read before implementing)
- **Why rows are unreadable:** aiogram-dialog `Select` renders all items in a
  single keyboard row. Lesson list
  ([lessons/management.py](../../telegram/dialogs/lessons/management.py), `managed_lesson`),
  break list ([breaks.py](../../telegram/dialogs/breaks.py), `break`) and student list
  ([student_profiles.py](../../telegram/dialogs/student_profiles.py)) all use a bare
  `Select`. Existing `Column(Select(...))` usage to copy:
  `lessons/creation.py`, `homework/student.py`, `homework/teacher_creation.py`,
  `onboarding.py`.
- **The break review buttons** (`Change date`, `Change start`, `Change end`,
  `Change series end`) sit in one `Row` in the `BreakScreens.review` window;
  `Group(..., width=2)` honours the existing `when=` conditions.
- **Student labels:** `TeacherStudentProfileSummary.display_name` and
  `username_display` are *never empty*. The backend substitutes
  `"Name unavailable"` / `"No username"` (`STUDENT_NAME_UNAVAILABLE_LABEL`,
  `STUDENT_USERNAME_UNAVAILABLE_LABEL` in `backend/auth/constants.py`,
  used in `backend/auth/service.py`). "No full name" therefore has to be
  detected by those placeholders; the bot must not import backend constants.
- **The "message" part of the student list** (`profile_page`) is built from the
  same `label` strings as the buttons, so one change fixes both.
- **Month names:** `format_human_date`/`format_local_interval` in
  [shared/inputs.py](../../telegram/shared/inputs.py) always use full names
  (`_MONTH_NAMES`); `_SHORT_MONTH_NAMES` already exists and is only used by
  `format_compact_schedule_date`. The same formatters feed lessons, vacations
  and break detail screens, so the short form must be opt-in.
- **Break creation is bound to the list kind:** `begin_create` assumes
  `dialog_data["kind"]` (`once`/`weekly`) was set by `choose_one_time` /
  `choose_weekly`; `_kind`, `_resource` and the create/update payload selection
  all derive from it. An "Add break → One-time / Recurring" flow must set `kind`
  before `BreakScreens.start_date`, and a unified list must derive `kind` from
  `EventManagementItem.is_recurrent` on selection.
- **Unified list needs data the API does not return:** the backend lists
  (`GET /api/v1/lessons/`, `/recurrent-lessons/`, `/breaks/`,
  `/recurrent-breaks/`, via `management.reads.list_managed_events`) are
  per-resource, paginated and ordered by series/event `start`; they have no
  "next occurrence" and cannot be merged across pages on the client without
  fetching everything. The bot must not expand recurrences itself.
  Reusable: `backend/events/occurrences.py::expand_recurrence`,
  `RecurrentCancels` handling already used by `backend/events/range_service.py`.
- **Refresh inventory (button → handler):** lessons list (`refresh_events`),
  breaks list (`refresh_events`), student lists ×2 + profile
  (`refresh_profiles`, `refresh_profile`), vacations (`refresh_vacations`),
  student homework detail (`refresh_assignment`), teacher homework list and
  detail (`refresh_assignment_list`, `refresh_detail`), settings
  (`Refresh settings`), broadcast results (`refresh_broadcast_results`).
  Schedule screens already have none (`_refresh` in `schedule/dialog.py` is the
  internal reload used by period switching — keep it). Error text in
  [api/client.py](../../telegram/api/client.py) says "Refresh the list." (break,
  vacation, lesson not found) and must be reworded. Internal helpers such as
  `_refresh_detail` in `breaks.py`, `_refresh_after_removal`, the
  "Reload"/"Retry" recovery buttons and the unknown-outcome reconciliation paths
  are **not** Refresh buttons; keep them.

## Tasks

### 1. Student list: one name per button, no homework counts
**Domain:** `telegram/` (existing) · **Type:** bot

- In `student_profile_data` build the label from `display_name` if it is a real
  name, else `username_display` if it is a real username, else the placeholder
  name. Treat `"Name unavailable"` and `"No username"` as absent. Put these two
  literals in one place in `telegram/shared/` (or `telegram/api/`) and pin them
  to the backend constants with a test (tests may import `backend`).
- Drop `· N incomplete` from the label. Because `profile_page` reuses the label,
  the list message loses it too.
- Render the student buttons in a single column (`Column(Select(...))`) for
  both `active` and `former` windows.

**Reuse:** `_safe`, existing `Column` pattern.

**Acceptance Criteria**
- [ ] Button/message show only full name; username when there is no name; no
      homework text in either place.
- [ ] Existing assertions in `tests/test_bot_student_profiles.py` that expect
      `· N incomplete` or the double name are updated.
- [ ] `uv run ruff check telegram tests`, `ruff format --check telegram tests`,
      `mypy telegram`, `pytest -q tests/test_bot_student_profiles.py` pass.

### 2. Student profile: hide empty homework and history sections
**Domain:** `telegram/` (existing) · **Type:** bot

- In `_profile_detail_text` omit the `Homework: …` line when
  `homework.assigned == 0`.
- Omit the `Last 10 actions` heading **and** the `No recorded actions.` line
  when `profile.actions` is empty; avoid leaving stray blank lines (the removed
  former-student footer must still be separated correctly).
- Hide the `View homework` button (`can_view_homework`) as well when
  `homework.assigned == 0` (Decision 3).

**Acceptance Criteria**
- [ ] Profile with zero assigned homework and zero actions shows only name,
      username and added date (plus the read-only footer for former profiles).
- [ ] Profiles with homework and/or actions render as before.
- [ ] Tests cover all four combinations.

### 3. Remove every manual Refresh button
**Domain:** `telegram/` (existing) · **Type:** bot

- Delete the buttons and their now-unused handlers listed under "Refresh
  inventory" (and unused imports/state such as the lesson/break
  `refresh_events`). Keep the internal reload helpers listed above.
- Reword the three "Refresh the list." messages in `api/client.py` (e.g. "Go
  back to the list.") and update the tests that assert them.
- Rows that held Refresh next to other buttons (`Active students`,
  `Weekly lessons`/`One-time lessons`, `Back to list`) must still lay out well.
- Broadcast results, settings and homework lose Refresh too; keep
  Reload/Retry (Decision 2).

**Acceptance Criteria**
- [ ] No `Refresh` / `Refresh settings` / `Refresh results` label and no
      `refresh_*` button handler remains under `telegram/` (`grep -rni refresh
      telegram` only finds internal helpers and comments).
- [ ] Tests that clicked Refresh (`test_bot_student_profiles.py`,
      `test_bot_lesson_management.py`, `test_bot_vacation_management.py`,
      `test_bot_homework_management.py`, `test_bot_settings_dialogs.py`,
      `test_telegram_offline_e2e.py`, `test_bot_schedule_dialogs.py`) are
      migrated to the remaining reload/back paths or removed.
- [ ] Stale-screen recovery (back to list, reopen, "Reload") is still covered.

### 4. Breaks: menu, list and labels
**Domain:** `telegram/` (existing) · **Type:** bot

Depends on Task 6 for the unified list; the menu/label work can land first on
the current per-kind data.

- Menu text: `Manage breaks. Tap a break to remove it.` (grammar-corrected
  from the todo; Decision 8). Replace the
  `One-time breaks` / `Recurring breaks` buttons with `Add break` and the list
  of all breaks (one button each).
- `Add break` opens a choice window `One-time break` / `Recurring break`; each
  sets `kind` and runs the existing `begin_create` flow unchanged.
- Break list: one button per break in a column, paginated with the existing
  `BREAK_PAGE_SIZE` and Previous/Next. Title and timezone footer stay.
- Label: `Weekly · <interval>` / `One-time · <interval>` using the short month
  (`Oct 5`). Add a short-month parameter to `format_human_date` →
  `format_local_interval` → `format_local_interval_in_timezone` in
  `shared/inputs.py` (reuse `_SHORT_MONTH_NAMES`) and use it for the lessons
  list, break list, break detail and displacement screen (Decision 6). Other
  callers (vacations, prompts) keep the full month.
- Selecting a break opens the existing detail with Edit/Delete.
  "Tap a break to remove it" is the menu wording only; keep the detail →
  `Confirm delete` flow (Decision 4).
- Remove the per-kind switcher buttons (`Recurring breaks` / `One-time breaks`
  inside the list window).

**Reuse:** `_load_page`, `select_break`, `begin_create`, `_break_label`,
`feedback_block`, `page_label`.

**Acceptance Criteria**
- [ ] Menu text and buttons exactly as above; Back to home retained.
- [ ] Creating one-time and recurring breaks still works end to end and returns
      to the list with `Break created.`
- [ ] Labels never contain the word "break" and use `Oct`-style months, also on
      the lessons list and break detail screens.
- [ ] Tests in `tests/test_bot_break_*` (and any lesson/break cross-flow tests
      that click `One-time breaks`/`Recurring breaks`) updated.

### 5. Break displacement and review screens
**Domain:** `telegram/` (existing) · **Type:** bot

- `_occurrence_label`: short month, and replace
  `Automatic break reservation (read-only):` with `Auto break:`.
- `displacement_data` / window: remove the `Affected lessons: N` counter
  (`affected_count`), and put an empty line before the
  `Confirming permanently deletes…` warning (also before the open-ended note
  where it is appended).
- Review window: put `Change date`, `Change start`, `Change end`,
  `Change series end` into `Group(..., width=2)` so they render 2 × 2
  (`when="can_edit"` / `"can_edit_cutoff"` preserved).

**Acceptance Criteria**
- [ ] Displacement screen: no counter, blank line before the warning, short
      months, `Auto break:` label; pagination of affected lessons unchanged.
- [ ] Weekly review shows four buttons as 2 × 2; one-time review (three buttons)
      stays readable.
- [ ] Confirmation token and payload behavior untouched; existing displacement
      tests updated for text only.

### 6. Unified lesson and break lists sorted by next occurrence
**Domain:** `backend/events/management/` + `backend/lessons/`, `backend/breaks/`
(existing) and `telegram/` · **Type:** service | router | bot

- **Backend:** add a read-only combined listing for lessons and for breaks
  (recommended: `GET /api/v1/lessons/all` style endpoint placed before
  `/{lesson_id}`, or an `include_recurrent` mode on the existing list; pick one
  in Decision 1; a new endpoint is chosen, e.g. `GET /api/v1/lessons/upcoming`) that returns `EventManagementItem` rows for one-off and
  recurring events together, each with a new
  `next_occurrence_start: datetime | None` (UTC, offset-bearing like existing
  fields), ordered by it ascending and paginated in SQL/Python over the merged
  set. Compute the next occurrence with `expand_recurrence` honouring
  `RecurrentCancels`, the series cutoff and (for open-ended series) the existing
  one-year validation bound; for one-offs use `start` if not past. Reuse
  `_management_item` / `_resolve_scope` / visibility handling so students and
  vacation visibility stay identical to the per-resource lists. Keep the
  existing per-resource endpoints unchanged.
- **Client:** add a `read_unified_management_page` call and model field in
  `telegram/api/` (`EventManagementItem.next_occurrence_start`, optional so old
  payloads still validate). In `lessons/management.py` replace the
  `One-time lessons` / `Weekly lessons` menu with a single list (drop `kind`
  switchers; derive `kind` from `is_recurrent` on selection so `_resource`,
  detail and edit flows keep working). Label keeps `Weekly` / `One time`
  prefix, shows the next occurrence time, and renders in a column
  (`Column(Select(...))`). The same list is reused by Task 4 for breaks.
- Include only items with a future occurrence (`end > now` for one-offs; at
  least one non-cancelled occurrence not yet ended for series); `total` counts
  only those (Decision 5). Past one-offs and ended series are omitted.

**Reuse:** `list_managed_events`, `expand_recurrence`, `RecurrentCancels`
handling in `range_service.py`, `EventManagementPage`, `_list_label`.

**Acceptance Criteria**
- [ ] Backend: ordering/pagination tests with mixed one-off/recurring,
      cancelled next occurrence, finite series that ended, open-ended series,
      student vs teacher scope, vacation-hidden items; no N+1 beyond one
      cancellation query.
- [ ] Bot: one list, closest upcoming first, one lesson per row, selecting any
      item opens the correct detail/edit flow for both kinds.
- [ ] `ruff check --fix backend && ruff format backend && mypy backend`,
      `uv run ruff check telegram tests`, `mypy telegram` pass.
- [ ] No migration needed (confirm with `alembic check` / no model changes).

### 7. Lesson list layout
**Domain:** `telegram/` (existing) · **Type:** bot

Covered by Task 6 for the merged list; if Task 6 slips, ship the interim fix
first: wrap the existing `managed_lesson` `Select` in `Column` (and the break
list likewise) so the per-kind lists are already one-per-row.

**Acceptance Criteria**
- [ ] Lessons render one per row; Previous/Next remain a separate row.

### 8. Verification and documentation
**Domain:** docs + tests · **Type:** docs

- Run `uv run pytest -q tests`, `uv run ruff check telegram tests`,
  `uv run ruff format --check telegram tests`, `uv run mypy telegram`, and the
  backend lint/mypy trio.
- Update AGENTS.md (Feature Status "Telegram bot" and "Schedule viewing"
  presentation notes: unified lists, no Refresh, short months in break
  screens) and add the superseding note to the three plans named in Scope.
- Record that no live Telegram or PostgreSQL verification was performed, if so.

## Out of scope for this release
- Changing scheduling rules, break-displacement confirmation semantics, or the
  delete-break confirmation.
- Re-exposing former students, HTMX frontend, notification behavior.
- Managing past one-off lessons or ended series from the bot (they are no longer
  listed; see Decisions).

## Decisions recorded (2026-11-18)
1. **Unified list:** add a new backend combined-listing endpoint with computed
   `next_occurrence_start`; the bot does not fetch-and-sort client-side.
2. **Refresh:** remove all manual Refresh buttons everywhere (broadcast results,
   settings, homework included). Keep the Reload/Retry recovery buttons and the
   unknown-outcome reconciliation paths.
3. **`View homework`:** hidden when the student never had homework assigned
   (`homework.assigned == 0`), together with the `Homework:` line.
4. **Break detail:** keep the detail screen (Edit break / Delete break →
   confirm). The menu text only hints at removal.
5. **Lists show only items with a future occurrence.** Past one-off lessons or
   breaks and ended series are not listed. `total`, pagination and empty states
   count only listed items. An occurrence that is in progress still counts
   (`end > now`), so an ongoing lesson stays manageable; confirm in review if a
   strict `start > now` is wanted.
6. **Short months** (`Oct 5`) apply to the lessons list, break list, break
   detail, and the displacement screen. Make the short form the default in
   `format_human_date`/`format_local_interval` only if every caller is meant to
   change; otherwise keep it opt-in and enable it for these screens. Vacation
   and prompt text are unchanged.
7. **Wording:** `Auto break: <duration> · <interval>`.
8. **Copy review:** user-typed text may be corrected for grammar/typos.
   Corrected strings: menu `Manage breaks. Tap a break to remove it.`
   (was "Tap on break to remove it."), `Add break` choice buttons
   `One-time break` / `Recurring break`, list labels `Weekly · …` /
   `One-time · …`. Any other string touched while implementing should be
   proofread, and test expectations updated in the same change.

## Open questions
- None blocking. Only the strict-vs-inclusive "ongoing" boundary in
  Decision 5 is a default awaiting confirmation.

## Implementation notes (2026-11-18)
- Tasks were executed in the order 1, 2, 3, 5, 6, 4, 7, 8 because Task 4 depends
  on the unified break list from Task 6; Task 7 is satisfied by Task 6 (and the
  break list) with `Column(Select(...))` layouts.
- `GET /api/v1/lessons/upcoming` and `/breaks/upcoming` read "now" from the
  existing `get_schedule_clock` dependency so tests can freeze it. Open-ended
  and not-yet-started series are searched from `max(now, series start)` in
  one-step windows; the one-year validation bound is not used for listing.
- The break list is the break management home (no separate menu); `Add break`
  opens a `One-time break` / `Recurring break` choice and then the unchanged
  creation flow. The lesson list is likewise opened directly from Manage events.
