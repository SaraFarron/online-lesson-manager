---
name: implement-plan
description: "Use when asked to execute or implement tasks from an attached implementation plan or backlog, including docs/plans/TELEGRAM-AUTH-PLAN.md and docs/plans/SCHEDULING-FOUNDATION-PLAN.md. Analyse, implement, and verify each task sequentially; stop for decisions or plan changes; optionally commit verified changes."
argument-hint: "Attach or name a plan, specify task IDs or a defined batch, and optionally request commits."
---

# Implement a plan

Execute an existing implementation plan, one task at a time. This is an
execution workflow, not a workflow for writing or decomposing a new plan.

## Resolve the assignment

1. Read the attached or named plan and applicable repository instructions.
   If the plan cannot be accessed, ask the user for it; do not substitute a
   guessed backlog.
   New project plans are stored in `docs/plans/`. Older plans may still be in
   the repository root; use the actual tracked or attached path rather than
   assuming every historical plan has already been moved.
2. Identify the exact requested tasks, their prerequisites, approval gates,
   acceptance criteria, and recorded execution status. Inspect the actual
   code before treating plan descriptions as current repository facts.
3. Use an explicitly defined batch when requested. If "first set", "next
   tasks", or similar wording has no unambiguous definition, ask for task IDs
   before changing code. Default to one numbered task, not the whole milestone.
4. Do not expand scope to satisfy unfinished prerequisites silently. If a
   required prerequisite is incomplete or an approval is missing, stop and
   ask the user how to proceed.
5. Inspect Git status and preserve pre-existing changes. Determine whether
   the invocation requests commits. Do not commit by default or infer commit
   permission from previous invocations or examples.

Keep a short execution checklist using available task tracking. Do not create
planning, tracking, or verification Markdown files unless requested.

## Repeat for each task

Finish the complete cycle below before starting the next task, including
optional committing. Do not implement several tasks and verify them afterward.

### 1. Analyse and plan

- Read the task, completed prerequisite contracts, and relevant implementation.
- Read the product specification when the task depends on product behavior.
- Identify reusable helpers, affected clients/routes/schemas, migrations,
  documentation, and compatibility requirements.
- State a brief task-local implementation and verification plan. Map each
  acceptance criterion to a concrete check, including failure paths and
  meaningful edge cases.
- If a product decision is unresolved, the approved plan must change, or
  requirements conflict, stop before implementing that decision.

### 2. Implement

- Make complete, surgical changes for this task following existing patterns.
- Wire all required surfaces; do not leave a feature implemented but unused.
- Preserve behavior outside the task's explicitly approved changes.
- Update directly related documentation. Update the plan's execution status
  only if it has an established status convention, and only after verification.
- Do not add unrelated refactors, tooling, tests, or features.

### 3. Verify

- Prefer realistic developer checks through Swagger, the Telegram interface,
  browser, or direct HTTP requests, choosing the interface that actually
  exercises the task. Check persisted results where relevant.
- Inspect the response/output and verify acceptance criteria, not merely a
  successful command exit or HTTP status. Exercise relevant negative cases,
  authorization boundaries, and existing behavior affected by the change.
- Use isolated test data/databases where practical. Do not send unsolicited
  messages, modify real user records, or run destructive migration checks
  without explicit approval.
- Run repository-required lint, format, and type checks for code changes.
  Run relevant existing regression tests when available.
- Writing new automated tests is not mandatory. Add focused tests when
  realistic checks cannot adequately verify required behavior, such as
  internal-only logic, deterministic time boundaries, or concurrency. Follow
  existing test patterns; do not introduce a new framework unnecessarily.
  If the plan explicitly requires tests, honor that requirement.
- Do not claim verification through an interface that was not actually used.
  If access, credentials, an external service, or tooling blocks a required
  check, report it and stop; do not mark the task complete.
- Fix implementation defects within the approved scope and repeat affected
  checks. If fixing a failure requires a plan change or uncertain decision,
  stop and ask instead.

### 4. Commit when requested

- Commit only after the task's required verification passes.
- Review the diff and staged content. Stage only task-related changes; never
  indiscriminately stage the worktree or include another person's changes.
  If unrelated edits cannot safely be separated, stop and ask.
- Use a very short, descriptive commit subject and any repository-required
  trailers. Do not amend or push unless explicitly requested.
- If committing fails, report the failure and resolve it before proceeding;
  do not bypass hooks or claim the changes were committed.

### 5. Close the task

- Record completion through the existing tracking convention, with a brief
  account of the checks actually performed and the commit hash, if any.
- Continue only if all required checks pass, no decision remains unresolved,
  and the next task belongs to the user's requested scope.
- Stop after the selected task or batch. Do not start the next milestone.

## Stop-and-ask protocol

When blocked or uncertain, explain briefly:

- What happened and which task is affected.
- Which requirement, decision, or verification step is blocked.
- The viable choices and the recommended next step.

Ask one focused question using the available user-question tool. Do not
silently change the plan, skip verification, or continue to another task
while waiting for approval.

## Final report

Keep the final response concise: completed task IDs, actual verification
results, commits if requested, and any remaining blockers. Distinguish
implemented-but-unverified work from completed work. Never report skipped or
blocked checks as passing.
