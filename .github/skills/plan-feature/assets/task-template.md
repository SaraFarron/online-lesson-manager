# Task: <short, action-oriented title>

**Domain:** `backend/<package>/` (existing | new)
**Type:** schema/migration | service | router | validator | bot | frontend | notification

## Context
Link the relevant README.md section and current status (done / partial / missing —
see AGENTS.md Feature Status).

## What to do
Concrete, scoped steps. Reference exact files and symbols where possible.

## Reuse
Existing functions/classes this task must reuse instead of reimplementing, e.g.
`backend.events.service.create_event`, `EventValidator`, `TimeValidation`.

## Acceptance Criteria
- [ ] ...
- [ ] `ruff check --fix backend && ruff format backend && mypy backend` pass
- [ ] Migration added if models changed (`alembic revision --autogenerate`)

## Out of scope
What this task should explicitly NOT touch.

## Suitable for
AI agent | Human (reason, e.g. "needs a product decision on X")
