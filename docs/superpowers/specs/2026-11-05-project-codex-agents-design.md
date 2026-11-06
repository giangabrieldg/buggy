# Project Codex Agents Design

## Intent

Add reusable, project-scoped Codex agents to help build the local Windows AI assistant. The profiles should make implementation, test development, and quality review available by name in future Codex work on this repository.

## Goals

- Store custom agent profiles in `.codex/agents/`, with one standalone TOML file per agent.
- Define focused roles for architecture, implementation, testing, and QA.
- Keep responsibilities distinct so implementation, automated testing, and user-facing QA can be delegated independently.
- Let agents inherit the active session's model and permissions unless the parent task says otherwise.

## Non-goals

- Do not build the assistant or add application code in this change.
- Do not create global agents under the user's home directory.
- Do not alter global Codex settings, model providers, sandbox settings, or approval policy.
- Do not enable autonomous multi-agent delegation from project instructions; the user or parent agent will request delegation for each task.

## Approaches

- **Project-scoped standalone TOMLs (selected):** one `.codex/agents/<role>.toml` per role. This matches the request to create agents for this project and allows each role to have its own focused instructions.
- **User-scoped TOMLs:** place profiles under `~/.codex/agents/` for reuse across repositories. This is broader than the requested project scope.
- **Only `.codex/config.toml`:** configure shared agent defaults, but do not define the four named role profiles there.

## Roles

- **Architecture:** map requirements to components, explain tradeoffs, identify integration risks, and review design decisions before implementation.
- **Builder:** implement a bounded, approved coding task, follow repository conventions, and report changed files and verification evidence.
- **Test engineer:** design and maintain automated tests, use isolated fixtures, run requested checks, and report failures precisely.
- **QA reviewer:** check the finished user flows against acceptance criteria, focusing on Windows behavior, usability, and edge cases; report findings without silently changing product behavior.

## Constraints and decisions

- Each profile must define `name`, `description`, and `developer_instructions` as required by the documented Codex custom-agent format.
- Files will be `.codex/agents/architecture.toml`, `builder.toml`, `test_engineer.toml`, and `qa_reviewer.toml`.
- Model fields will be omitted so profiles inherit the active Codex model. Permission and sandbox behavior remain controlled by the parent session.
- Agent instructions may scope their work, but they do not provide separate sandbox or approval boundaries.
- The repository currently has no existing `.codex` configuration to preserve or extend.

## Builder decisions

- Keep agent instructions concise and limited to each role's domain.
- Allow the builder to edit code only when it receives a concrete implementation task; keep architecture and QA profiles review-oriented by default.
- Do not pin a model because availability can vary by account and the user did not request model specialization.
