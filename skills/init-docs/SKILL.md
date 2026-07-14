---
name: init-docs
description: Initialize or audit project documentation structure for agentic collaboration. Use when starting a new project, onboarding to an existing project, or auditing documentation completeness.
argument-hint: [action] (init|audit|add-subdir)
allowed-tools: [Write, Read, Glob, Grep, Bash(mkdir *), Bash(ls *)]
---

# Project Documentation Setup

A standardized documentation system optimized for human-AI collaboration, based on emerging industry standards (AGENTS.md Linux Foundation spec, Claude Code best practices).

## Quick Reference

| Action | Command | Description |
|--------|---------|-------------|
| Initialize | `/init-docs init` | Set up full documentation structure |
| Audit | `/init-docs audit` | Check existing docs, identify gaps |
| Add subdir | `/init-docs add-subdir path/to/dir` | Add README/STATUS to a subdirectory |

## Core Principles

1. **Fractal/Recursive**: Same pattern (README + STATUS) repeats at each directory level
2. **Progressive Disclosure**: Root docs are minimal, point to detailed subdocs
3. **Single Source of Truth**: Parameters, glossary, config in ONE place
4. **Session Continuity**: HANDOFF.md tracks state across agent sessions

## Standard Structure

```
project-root/
├── CLAUDE.md              # Entry point (60-150 lines MAX)
├── STATUS.md              # Current progress, active work
├── .agents/               # Agent collaboration infrastructure
│   ├── HANDOFF.md         # Session continuity (read-only dashboard)
│   ├── sessions/          # Git-tracked session logs (optional)
│   └── context/           # Detailed reference docs
│       ├── glossary.md    # Term definitions
│       ├── parameters.md  # Single source for all params
│       └── troubleshooting.md
│
├── docs/                  # Human-focused documentation
│   └── *.md               # Architecture, setup, API, etc.
│
└── subdirectory/          # RECURSIVE PATTERN
    ├── README.md          # "What is this? How does it work?"
    ├── STATUS.md          # "What's done? What's pending?" (if active work)
    └── deeper/
        ├── README.md      # Pattern continues...
        └── STATUS.md
```

## When to Include Each File

| File | Include When | Skip When |
|------|--------------|-----------|
| **README.md** | Directory has non-trivial content | Only data files, obvious structure |
| **STATUS.md** | Active work in progress | Completed/archived work |
| **HANDOFF.md** | Root only | Never in subdirectories |
| **CLAUDE.md** | Root only | Use README.md in subdirs |

## Actions

### `init` - Full Initialization

1. Detect project type (check for package.json, Cargo.toml, pyproject.toml, etc.)
2. Create directory structure: `.agents/`, `.agents/context/`
3. Generate CLAUDE.md from template (see [templates/CLAUDE.md](templates/CLAUDE.md))
4. Generate STATUS.md from template
5. Generate .agents/HANDOFF.md from template
6. Create .agents/context/ reference docs
7. Scan existing subdirectories and suggest where README/STATUS needed

### `audit` - Documentation Audit

1. Check if CLAUDE.md exists and is <150 lines
2. Check if STATUS.md exists
3. Check if .agents/HANDOFF.md exists
4. Scan all directories for missing README.md files
5. Identify STATUS.md files for completed work (should be removed)
6. Check for redundant information across files
7. Report findings with specific recommendations

### `add-subdir` - Add Documentation to Subdirectory

For the specified path:
1. Create README.md with standard template
2. Ask if there's active work (if yes, create STATUS.md)
3. Recursively check child directories

## Template Guidelines

### CLAUDE.md (Root Entry Point)
- **Length**: 60-150 lines (hard limit)
- **Content**: Commands first, then stack, then boundaries
- **Style**: Terse, actionable, point to details elsewhere
- See: [templates/CLAUDE.md](templates/CLAUDE.md)

### STATUS.md (Progress Tracking)
- **Content**: Current phase, active tasks, key metrics
- **Tables**: Use tables for tracking matrices
- **Updates**: Should change weekly or more often
- See: [templates/STATUS.md](templates/STATUS.md)

### README.md (Subdirectory)
- **Length**: <200 lines ideal
- **Content**: What, why, how for this directory
- **Self-contained**: Reader shouldn't need parent context
- See: [templates/README-subdir.md](templates/README-subdir.md)

### HANDOFF.md (Session Continuity)
- **Purpose**: Read-only dashboard for session handoffs
- **Content**: Last session summary, current blockers, next steps
- **Rule**: Never modify during active session (update at end)
- See: [templates/HANDOFF.md](templates/HANDOFF.md)

## Information Flow

```
Down (increasing specificity):
  Root STATUS → Subdir STATUS → Task STATUS
  "11/12 complete" → "3 running on Vast.ai" → "F100: 59%"

Up (summarized findings):
  Task results → Subdir STATUS → Root STATUS → CLAUDE.md highlights
  "τ=150 MPa" → "tri-F100: 150 MPa" → "NPT affects 2×" → "Key: NPT mode matters"
```

## Anti-Patterns to Avoid

| Don't | Do Instead |
|-------|------------|
| Document file paths | Describe capabilities |
| Embed large code blocks | Use `file:line` references |
| Duplicate params in multiple files | Single source in `.agents/context/parameters.md` |
| 400+ line READMEs | Split into focused docs |
| STATUS.md for completed work | Delete or archive |
| Vague descriptions | Specific, actionable items |

## Validation Checklist

After setup, verify:
- [ ] CLAUDE.md exists and is <150 lines
- [ ] STATUS.md exists with current work
- [ ] .agents/HANDOFF.md exists
- [ ] Every non-trivial directory has README.md
- [ ] No STATUS.md in completed/archived directories
- [ ] No redundant parameter definitions
- [ ] All cross-references are valid

## Examples

### Good CLAUDE.md Opening
```markdown
# MyProject

ML pipeline for image classification.

## Commands
make train        # Train model
make test         # Run tests
make deploy       # Deploy to staging

## Stack
Python 3.11, PyTorch 2.0, FastAPI
```

### Good STATUS.md Structure
```markdown
# Project Status

**Updated**: 2026-02-25

## Current Focus
Training v2 model with augmented dataset.

## Active Work
| Task | Status | Notes |
|------|--------|-------|
| Data augmentation | 80% | Running overnight |
| Model training | Pending | Blocked on data |

## Recent Completions
- [x] Dataset cleaning (2026-02-24)
```

### Good Subdirectory README
```markdown
# data-pipeline/

ETL pipeline for training data preparation.

## Contents
- `extract/` - Data source connectors
- `transform/` - Cleaning and augmentation
- `load/` - Database loaders

## Usage
```bash
python -m data_pipeline.run --config prod.yaml
```

## Configuration
See `.agents/context/parameters.md` for all config options.
```
