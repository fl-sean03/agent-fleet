# {{PROJECT_NAME}}

{{ONE_LINE_DESCRIPTION}}

## Commands
{{COMMAND_1}}      # {{DESCRIPTION_1}}
{{COMMAND_2}}      # {{DESCRIPTION_2}}
{{COMMAND_3}}      # {{DESCRIPTION_3}}

## Stack
{{LANGUAGE}}, {{FRAMEWORK}}, {{DATABASE/TOOLS}}

## Boundaries

### Always Do
- Run tests before committing
- Update types when changing interfaces
- Document breaking changes

### Ask First
- Database schema changes
- Adding new dependencies
- Architectural changes

### Never Do
- Commit secrets or .env files
- Modify production configs directly
- Skip tests for "quick fixes"

## Quick Reference

| Resource | Location |
|----------|----------|
| Current status | `STATUS.md` |
| Session handoff | `.agents/HANDOFF.md` |
| Glossary | `.agents/context/glossary.md` |
| Parameters | `.agents/context/parameters.md` |

## Project Structure
```
{{PROJECT_TREE}}
```

## Notes
{{IMPORTANT_CONTEXT}}
