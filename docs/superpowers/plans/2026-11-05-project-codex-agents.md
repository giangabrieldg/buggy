# Project Codex Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four project-scoped Codex agent profiles for architecture, implementation, testing, and QA.

**Architecture:** Use one standalone TOML file for each role under `.codex/agents/`. Each profile provides a unique name, a short use description, and focused developer instructions; model and permission settings remain inherited from the parent session.

**Tech Stack:** Codex custom-agent TOML; Python standard-library `tomllib` for syntax and required-field validation.

**Spec:** `docs/superpowers/specs/2026-11-05-project-codex-agents-design.md`

## Global Constraints

- Store profiles in `.codex/agents/`, one standalone TOML file per agent.
- Each profile defines `name`, `description`, and `developer_instructions`.
- Omit model fields so profiles inherit the active Codex model.
- Do not modify global Codex settings or add application code.
- Do not add project instructions that trigger autonomous delegation.
- Parent-session permissions and sandbox settings remain authoritative.

## Review Focus

- Invalid TOML prevents Codex from reading a profile: parse all four files with `tomllib`.
- Missing required fields cause incomplete profiles: assert all three required keys exist and are non-empty.
- A profile name that differs from its intended role is hard to invoke: assert the expected set of names.
- Accidental model or permission overrides violate the approved design: assert profile files omit `model`, `model_reasoning_effort`, `sandbox_mode`, and `approval_policy`.
- Role instructions can drift or overlap: manually review each `developer_instructions` block against the four responsibilities in the spec.

---

### Task 1: Add the four project agent profiles

**Files:**
- Create: `.codex/agents/architecture.toml`
- Create: `.codex/agents/builder.toml`
- Create: `.codex/agents/test_engineer.toml`
- Create: `.codex/agents/qa_reviewer.toml`

**Interfaces:**
- Produces four Codex-discoverable custom agents named `architecture`, `builder`, `test_engineer`, and `qa_reviewer`.
- Every file contains non-empty `name`, `description`, and `developer_instructions` fields.
- Architecture focuses on component boundaries, tradeoffs, integration risks, and design review.
- Builder implements bounded tasks explicitly assigned by the parent and reports changes and verification.
- Test engineer develops isolated automated tests and runs checks requested by the parent.
- QA reviewer checks acceptance criteria, Windows behavior, usability, and edge cases, then reports findings.
- No profile specifies model, model-reasoning, sandbox, or approval overrides.

- [x] **Step 1: Create the four standalone TOML profiles** with the approved names, role instructions, and inherited model/permissions.

- [ ] **Step 2: Parse and validate all profiles** (blocked: this machine has no Python interpreter available; `python` resolves to the Microsoft Store alias and `py` is absent.)

Run: `python -c "import pathlib,tomllib; files=list(pathlib.Path('.codex/agents').glob('*.toml')); assert len(files)==4; profiles=[tomllib.loads(p.read_text(encoding='utf-8')) for p in files]; assert {p['name'] for p in profiles}=={'architecture','builder','test_engineer','qa_reviewer'}; required={'name','description','developer_instructions'}; assert all(required<=p.keys() and all(isinstance(p[k],str) and p[k].strip() for k in required) for p in profiles); forbidden={'model','model_reasoning_effort','sandbox_mode','approval_policy'}; assert all(not forbidden.intersection(p) for p in profiles)"`

Expected: command exits successfully with no assertion errors.

- [x] **Step 3: Review the four profile files** to confirm each instruction block matches its approved role and the profiles are in the project-local directory.

Run: `Get-ChildItem .codex/agents -Filter *.toml | Select-Object -ExpandProperty Name`

Expected: `architecture.toml`, `builder.toml`, `test_engineer.toml`, and `qa_reviewer.toml`.
