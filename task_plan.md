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