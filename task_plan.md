# Task Plan

## Goal
Harden the payroll workflow against the identified Tripletex runtime and correctness issues without changing unrelated behavior.

## Phases
- [completed] Inspect current payroll workflow and tests
- [completed] Implement payroll workflow fixes
- [completed] Add regression tests for period sync, tax deduction, employee lookup, and salary type safety
- [completed] Run targeted tests and fix any regressions
- [completed] Record verified repo notes

## Errors Encountered
- Initial payroll test rerun failed because several fixtures no longer matched the stricter exact employee-lookup logic; fixed by aligning fixture emails and adding an explicit DOB where employment creation is expected.

## Additional Goal (2026-03-22)
Harden month-end closing behavior for multi-voucher journal tasks to avoid 422 write errors and accounting mistakes.

## Additional Phases
- [completed] Enforce account pre-validation + missing-account creation before voucher posting
- [completed] Enforce no-fabrication guard for missing posting amounts
- [completed] Update Chief/Senior month-end instructions (contra-account + trial-balance verification)
- [completed] Add and run voucher regression tests for new behavior

## Additional Goal (2026-03-22 Prompt Corpus Audit)
Audit the full tasks.csv prompt corpus by workflow family, harden canonical routing for repeated task patterns, and remove remaining avoidable drift in payment/FX handling.

## Additional Phases
- [completed] Delegate read-only audits across prompt/workflow families using subagents
- [completed] Confirm which audited gaps were already fixed in live code vs stale in historical logs
- [completed] Reintroduce deterministic orchestrator fast-paths + fallback preambles for slow/high-confidence workflow families
- [completed] Tighten payment/FX workflow behavior and add regression coverage
- [completed] Run focused validation for orchestrator, payment, and FX workflows

## Additional Goal (2026-03-22 Workflow Audit)
Harden the highest-value canonical workflows against OpenAPI mismatches, ambiguous money movement, and avoidable bad writes.

## Additional Audit Phases
- [completed] Audit sales, accounting, HR, and project workflows against docs/tripletex_openapi.json
- [completed] Implement strict payment, invoice, expense, timesheet, and project-invoice guardrails
- [completed] Add targeted regression tests for the hardened paths
- [completed] Run focused workflow regressions and record residual unrelated failures in the broader suite
- [completed] Extend coverage for credit-note matching and supplier-payment reconciliation
- [completed] Remove remaining payroll/customer regression drift and restore green workflow suite
- [completed] Do final prompt-corpus swipe and record dominant family-to-workflow mapping