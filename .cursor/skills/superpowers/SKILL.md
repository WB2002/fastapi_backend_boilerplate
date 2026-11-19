---
name: superpowers
description: Core software development discipline and workflow framework (obra/superpowers). Use when starting any task, executing multi-step plans, managing subagents (SDD), enforcing test-driven development, systematic debugging, or requiring strict evidence verification.
---

## Setup constraints

These rules override the rest of this skill when they conflict.

- Do not set Status to `accepted`. Only the human owner does.
- Do not set lifecycle to `APPROVED`, `MERGED`, or `CANARY`.
- Do not review your own change. Readonly reviewers report failures first.
- Do not merge or deploy.
- Do not commit, push, or open a pull request unless the user explicitly asks in this turn.
- Do not invent product scope or visible interaction behavior. Stop at the missing human gate.
- `/spec` writes `intent/<slug>/spec.md` from an accepted intent. Do not replace that path or treat a draft as accepted.
- ADRs go through `/record-decision` and `docs/adr/0000-template.md`. Leave Status `proposed`.
- Never weaken or delete a test to get green. If `.cursor/.fix-mode` exists, do not edit test files.
- Out-of-scope findings become an intent or a debt record, not part of the current diff.
- Do not edit `scripts/verify.sh`, `scripts/verify-full.sh`, or `.github/` unless the user has created `.cursor/.guardrails-edit`.

# Superpowers Engineering Discipline

The Superpowers skill framework (adapted from Jesse Vincent's `obra/superpowers`) establishes strict operational discipline, preventing AI coding assistants from rushing into unverified code generation, skipping tests, or hallucinating implementation details.

## 1. The 1% Rule (Mandatory Skill Invocation)

> **If you think there is even a 1% chance a skill applies to what you are doing, you ABSOLUTELY MUST invoke the skill.**
> **IF A SKILL APPLIES TO YOUR TASK, YOU DO NOT HAVE A CHOICE. YOU MUST USE IT.**

- **Pre-action invocation**: Evaluate and activate applicable skills *before* making file edits, running destructive commands, or answering design questions.
- **No rationalizations**: Never bypass a process skill under the excuse of "this is a trivial edit", "I will write tests later", or "I already know what needs to be done".
- **Subagent exception**: If you were dispatched as an isolated subagent with a pre-scoped task brief and frozen requirements from a coordinator, execute the brief directly without re-invoking meta-discovery.

## 2. Process-First Hierarchy

When multiple skills overlap, process skills (methodology and gates) always dictate and sequence execution before implementation skills:

```
[Discovery & Scope]      /new-intent, /design-experience, /project-brief
        │
        ▼
[Specification & Plan]   /spec, /plan (writing-plans), /architecture-gate
        │
        ▼
[Task Execution]         superpowers:subagent-driven-development (SDD), executing-plans
        │
        ▼
[Implementation]         tdd (RED → GREEN → REFACTOR), source-driven-development
        │
        ▼
[Defect Investigation]   /fix-bug, systematic-debugging
        │
        ▼
[Verification & Review]  verification-before-completion, ship-check, readonly reviews
```

## 3. Subagent-Driven Development (SDD)

When implementing a multi-step plan (`plan.md`):

1. **Granular task sequencing**: Execute tasks one by one in numerical order (`TASK-NNN`). Never attempt to implement an entire multi-task plan in a single prompt context.
2. **Fresh context isolation**: For each task, spawn a fresh implementer subagent with only the task brief, relevant requirements (`REQ-NNN`), and touched files. This prevents context bloat and hallucination drift.
3. **Two-stage review gate**:
   - **Stage 1 (Spec Compliance)**: Verify the implementation fulfills the exact acceptance criteria without unrequested scope additions.
   - **Stage 2 (Code Quality & Safety)**: Verify adherence to `tdd` (test exists and passed), clean separation of concerns, zero dead code, and zero commented-out code.
4. **Whole-branch final audit**: Run full-suite verification and static checks across all changed files after the sequence completes.

## 4. Test-Driven Development (TDD) Invariants

No production logic is written without a pre-existing failing test.

- **RED**: Write a minimal, focused test at a public interface seam. Run it and confirm it fails for the expected reason (missing implementation, not syntax or bad imports).
- **GREEN**: Write the minimal code required to satisfy the test.
- **REFACTOR**: Improve design, readability, and performance while ensuring tests remain green.
- **Invariant**: Never weaken, delete, or skip a test to force green. If a test fails, fix the implementation.

## 5. Systematic Debugging Protocol

When diagnosing bugs, regressions, or unexpected test failures:

1. **Reproduce First**: Construct a deterministic, automated reproduction test before touching implementation code.
2. **Root Cause Isolation**: Trace data flow and invariants backwards from the defect. Do not guess or apply speculative patches.
3. **Hypothesis Testing**: Form a single hypothesis, test it with targeted instrumentation, and confirm or refute it.
4. **Targeted Fix**: Apply the minimal surgical change resolving the isolated root cause.
5. **Regression Verification**: Run the reproduction test and the entire test suite to guarantee zero side-effect breakage.

## 6. Verification Before Completion

Evidence must always precede claims of completion:

- Run `scripts/verify.sh` and targeted test suites in the current turn.
- Inspect raw stdout, stderr, and exit codes.
- Paste the raw command output into your response.
- Never state "all tests pass" or "build succeeded" without fresh command output from the current turn.

## 7. Anti-Rationalization Red Flags

| Rationalization | Reality & Required Action |
| :--- | :--- |
| "This is just a one-line fix, no need for a test." | A single line can break production. Write a reproduction test first. |
| "I'll implement the entire plan at once to save time." | Violates SDD. Break into tasks and execute sequentially. |
| "The test failed because the assertion was too strict." | Never weaken the test. Fix the implementation to meet the contract. |
| "I'm confident the build will pass in CI." | Confidence is not evidence. Execute local verification commands now. |
| "I can set Status to accepted since all criteria pass." | Setting `accepted` is strictly reserved for the human owner. |
