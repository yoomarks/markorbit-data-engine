# MarkOrbit Engineering Discipline v1.0

This file is the repository-wide engineering instruction source for Codex and other AI coding agents. Apply it to all development, bug fixes, refactors, PRs, CI fixes, and agent-driven changes in this repository.

Core default: **Root cause + Minimum change + Reuse + Verification + Scope discipline.**

## 1. Understand the real execution path before editing

Before changing code, locate the actual failure, call chain, SQL, configuration, workflow, or runtime path. Confirm the root cause first. Do not make broad speculative changes before the root cause is known.

## 2. Fix the root cause, not the symptom

Priority: root-cause fix > narrow compatibility handling > temporary workaround.

Do not make tests pass by swallowing exceptions, skipping critical logic, deleting tests, weakening error conditions, bypassing data-integrity checks, or hard-coding around the real defect.

## 3. Make the smallest necessary change

If three lines solve the verified problem, do not change thirty. Modify only what is required for the current objective.

Unless the current issue is directly caused by an architectural defect, do not opportunistically perform large refactors, move directories, rewrite modules, create frameworks, add abstraction layers, rename unrelated code, format the whole repository, or clean unrelated technical debt. Record non-blocking debt instead of expanding scope.

## 4. Reuse existing capabilities first

Use this order:

1. Existing repository implementation.
2. Existing functions, modules, tools, and infrastructure.
3. Python/OS/database native or standard-library capability.
4. Already-installed dependencies.
5. Only then add a dependency or build a new subsystem.

Do not duplicate existing functionality.

## 5. Control added complexity and code volume

Every added line, file, helper, wrapper, abstraction, or dependency must have a current verified purpose. Avoid one-off abstractions, unnecessary wrappers, speculative extensibility, premature generalization, premature modularization, or plugin systems without a present requirement.

**Solve today's verified problem, not tomorrow's imagined problem.**

## 6. Minimum change must not reduce engineering quality

Never weaken data integrity, security checks, permissions, necessary error handling, transaction consistency, idempotency, backward compatibility, required logging, required tests, migration safety, API contracts, or user-data protection merely to keep a change small.

## 7. Verification is part of completion

A change is not complete because it looks correct. Run the checks that apply to the change, including as appropriate: unit tests, integration tests, lint, type checks, build, Docker validation, database validation, migration validation, and GitHub Actions/CI.

For bug fixes, reproduce the failure when practical and demonstrate: **failure before -> fix -> passing after**.

## 8. Handle CI failures by fixing the real failing item

When CI fails:

1. Read the current failing log.
2. Identify the first real root cause.
3. Modify the minimum necessary code.
4. Verify in the corresponding local/runtime environment when possible.
5. Re-run CI.
6. Continue until green.

Do not stop at explaining the failure. Do not use one failure as an excuse to refactor the surrounding subsystem.

## 9. Maintain scope discipline

Start each task with one clear objective. If another issue is discovered:

- fix it only if it blocks the current task;
- otherwise record it and leave it outside the current change.

Prefer one clear PR objective with one necessary set of changes.

## 10. Git and PR discipline

Before submitting, inspect the diff for unnecessary edits, unrelated files, generated junk, temporary debug content, unneeded dependencies, unnecessary lockfile changes, and opportunities to simplify.

PR descriptions should focus on:

- the problem;
- the root cause;
- the change;
- the verification.

Avoid process-heavy or decorative descriptions.

## 11. Agent execution behavior

When the task is clear and the agent has access, act directly. Information available from the repository, logs, tests, CI, and docs should be read and resolved without repeatedly asking the user.

Default workflow:

**read -> locate -> modify -> test -> fix -> retest -> complete**

Avoid replacing implementation with long explanations or unnecessary questions.

## 12. Reporting discipline

Reduce fragmented progress reports. Unless a real user decision is required, finish a coherent stage before reporting.

Final reports should be concise and state:

- what was completed;
- the key changes;
- test/CI status;
- any remaining blocker;
- the next step.

Do not narrate every search, command, or reasoning step.

## 13. Complexity review before submission

Before every submission, ask:

> Is this implementation more complex than the current verified problem requires?

If yes, simplify it. Specifically check for unnecessary abstractions, files, dependencies, duplicated capabilities, unrelated changes, speculative future-facing code, and a more direct implementation.

## 14. Project-specific execution rule

These engineering rules apply immediately and remain the default for all future work in this repository. Existing project data-integrity, replay, idempotency, migration, source-lineage, and API-contract safeguards remain authoritative and must not be weakened by the minimum-change principle.
