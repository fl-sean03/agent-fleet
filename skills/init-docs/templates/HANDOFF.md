# Session Handoff

> **Purpose**: Read-only dashboard for session continuity. Update ONLY at session end.
>
> **Rule**: Never modify during active work. Read at session start, update at session end.

## Last Session

**Date**: {{DATE}}
**Duration**: {{DURATION}}
**Objective**: {{OBJECTIVE}}

### What Was Done
- {{ACCOMPLISHMENT_1}}
- {{ACCOMPLISHMENT_2}}

### Decisions Made
| Decision | Rationale | Reversible? |
|----------|-----------|-------------|
| {{DECISION_1}} | {{RATIONALE}} | {{YES/NO}} |

### Files Modified
- `{{FILE_1}}` - {{CHANGE_DESCRIPTION}}
- `{{FILE_2}}` - {{CHANGE_DESCRIPTION}}

## Current State

### Working On
{{CURRENT_WORK_DESCRIPTION}}

### Blockers
- {{BLOCKER_1}}

### Environment State
- Branch: `{{BRANCH_NAME}}`
- Last commit: `{{COMMIT_HASH}}`
- Running processes: {{PROCESSES}}

## Next Session Should

### Immediate (Do First)
1. {{IMMEDIATE_1}}
2. {{IMMEDIATE_2}}

### When Time Permits
- {{OPTIONAL_1}}
- {{OPTIONAL_2}}

### Questions to Resolve
- {{QUESTION_1}}

## Context Links

| Topic | File |
|-------|------|
| Project overview | `CLAUDE.md` |
| Current progress | `STATUS.md` |
| Glossary | `.agents/context/glossary.md` |
| Parameters | `.agents/context/parameters.md` |

---

## Session Log (Append-Only)

### Session {{N}} - {{DATE}}
- Objective: {{OBJECTIVE}}
- Outcome: {{OUTCOME}}
- Commit: `{{HASH}}`
