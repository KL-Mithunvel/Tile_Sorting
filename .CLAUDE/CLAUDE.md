# CLAUDE.md

> **IMPORTANT:** Read `CLAUDE-COMMON.md` first — it contains general must-follow instructions (companion files, deployment model, workflow, template structure). This file contains repo-specific instructions. Anything here overrides `CLAUDE-COMMON.md`.
>
> **Also read `PROJ_STARTER.md`** — it contains the owner's personal preferences (interaction rules, coding standards, tech stack choices, commit style). Copy its sections into any new project's `CLAUDE.md` User Rules alongside the rules from `CLAUDE-COMMON.md`.

---

## About This Repository

This repo is a curated library of `CLAUDE.md` files. Each file serves as a project brief for AI-assisted development sessions — capturing architecture, rules, conventions, and status so that Claude Code can contribute effectively without needing to re-discover project context from scratch.

The files here are used as **standard templates and reference material** for future projects.

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Project template — copy this into new projects |
| `CLAUDE-COMMON.md` | Universal workflow rules — must-follow for every project |
| `PROJ_STARTER.md` | Personal preferences and tech stack defaults — copy into new projects |
| `<project-id>.md` | `<Project name — one-line description>` |

New entries follow the same naming convention: `<short-project-id>.md`.

---

## How to Start a New Project

1. Copy `CLAUDE.md` into the new project root (keep the name `CLAUDE.md`).
2. Copy `CLAUDE-COMMON.md` and `PROJ_STARTER.md` alongside it, or reference the ones in this library.
3. Fill in every `[TO BE FILLED]` section below with the project's actual details.
4. In the **User Rules** section: copy `CLAUDE-COMMON.md` → Standard User Rules verbatim, then copy `PROJ_STARTER.md` contents below that. Add project-specific overrides last, clearly labelled.
5. Delete any `[if applicable]` sections that do not apply to the project.

---

## Project Overview  [TO BE FILLED]

> What the project is, who built it, the core problem it solves. Author, license, entry point, minimum runtime versions.

_Not yet filled — update before the first session._

---

## Running the System  [TO BE FILLED]

> Copy-paste-ready commands to start, test, and lint. Virtualenv activation first. Dev/simulation commands separated from hardware/production commands. Any seed-data or one-time setup steps.

_Not yet filled — update before the first session._

---

## Architecture  [TO BE FILLED]

> Module responsibilities table (file → role). Data flow diagram (ASCII). Threading or async model. Simulation vs real mode and how to switch.

_Not yet filled — update before the first session._

---

## Key Modules  [TO BE FILLED]

> One subsection per file or logical group. Public interface: function names, parameters, return types, exceptions raised. Side effects and I/O.

_Not yet filled — update before the first session._

---

## Schema Reference  [if applicable — TO BE FILLED]

> Path to DDL/schema file, read-only vs writable, tooling for inspection, annotated schema doc. Delete this section if the project has no data layer.

_Not yet filled — update before the first session._

---

## Key Conventions  [if applicable — TO BE FILLED]

> Universal row keys, encoded fields with full encode AND decode formulas, sentinel values, current-record pattern, per-tenant partitioning, env vars. Delete if no non-obvious encoding exists.

_Not yet filled — update before the first session._

---

## Data Files  [TO BE FILLED]

> What is stored, where, and whether it is git-tracked. Runtime-generated vs committed files. Files that must never be committed.

_Not yet filled — update before the first session._

---

## Platform Constraints  [TO BE FILLED]

> Target OS(es), platform-specific libraries and their guards, hardware dependencies and dev-mode equivalents.

_Not yet filled — update before the first session._

---

## Deployment Notes  [if applicable — TO BE FILLED]

> Dev vs deployment environment table, code transfer command, one-time target setup, pre-deploy checklist, hardware smoke test, config/env differences. Delete if no hardware or remote target.

_Not yet filled — update before the first session._

---

## Known Technical Debt  [TO BE FILLED]

> Existing rule violations (file + line), temporary workarounds. Do not omit — document so debt is not accidentally perpetuated.

_None recorded yet._

---

## Development Rules  [TO BE FILLED]

> Binding architectural rules, each numbered and sourced. Referenced by number in TODO items and commit messages.

_Not yet filled — update before the first session._

---

## Project TODO List  [TO BE FILLED]

Legend: 🔴 Bug / rule violation  |  🟡 Incomplete feature  |  🟢 Not started  |  ✅ Done

_Not yet filled._

---

## User Rules

> Copy **Standard User Rules** from `CLAUDE-COMMON.md` verbatim here, then copy all sections from `PROJ_STARTER.md` below those. Add project-specific overrides at the bottom, clearly labelled.

See `CLAUDE-COMMON.md` → Standard User Rules and `PROJ_STARTER.md` for the full rule set.

### Project-Specific Overrides

_None — add below as needed._
