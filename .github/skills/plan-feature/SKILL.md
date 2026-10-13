---
name: plan-feature
description: 'Use when asked to write release/milestone plans, decompose a README.md product-spec feature (scheduling rules, notifications, homework, Telegram bot, frontend) into small implementation tasks, or write a task description for an AI agent or human developer to pick up in this repo. Keywords: plan, roadmap, decompose, breakdown, backlog, release plan, task description, analytics.'
---

# Feature Planning & Task Decomposition

## When to Use
- Writing a plan for an upcoming release/milestone.
- Breaking a README.md feature down into small, independently-completable tasks.
- Writing a task description for an AI agent or a human teammate to implement.

## Before Planning

1. Read [README.md](/README.md) for the full product spec (Russian) covering the
   feature area in question.
2. Read the **Feature Status** and **Known Issues / Tech Debt** sections of
   [AGENTS.md](/AGENTS.md) to see what's already built, partially built, or
   known-buggy — don't re-plan finished work, and surface known bugs that would
   block new work.
3. Check for existing reusable building blocks before planning new ones.
   `backend/events/` (validators, service, schemas) is the base that every
   lesson/break/vacation feature composes — new event-like features (e.g.
   homework due dates tied to a lesson, notification scheduling) should reuse
   it, not duplicate it. Grep the relevant domain package
   (`router.py`/`service.py`/`validators.py`/`schemas.py`) for existing
   functions before describing a task as "create a new X".

## Output Format

### Release / milestone plan
Use [plan-template.md](./assets/plan-template.md). One task per `##` section,
ordered by dependency (migration/schema tasks before service/router tasks
before bot/frontend tasks).

### Individual task description
Use [task-template.md](./assets/task-template.md) for each task, whether it's
destined for an AI agent or a human. Keep tasks small: one task = one
router/service/validator change plus its migration — not a whole domain.

## Principles (apply to every task you write)

- **Compose, don't duplicate.** Name the existing `events` validator/schema/
  service functions a task should reuse instead of writing new ones.
- **No speculative scope.** Don't add settings, fields, or endpoints beyond
  what README.md actually asks for.
- **Flag migrations explicitly.** Any schema change needs an Alembic migration
  named `YYYY-MM-DD_slug.py` (see [AGENTS.md](/AGENTS.md)); say so in the task.
- **Call out blocked/ambiguous spec points as open questions**, rather than
  guessing — e.g. README.md describes a teacher setting
  ("максимальное количество уроков подряд") that has no corresponding column
  yet. Surface this instead of silently inventing a shape for it.
- **Note dead/unused code you find while decomposing.** This repo has a few
  per-domain `validators.py`/`dependencies.py` files that are no longer wired
  into their router (see Known Issues in AGENTS.md) — don't plan tasks that
  build on top of them without first flagging the inconsistency.
