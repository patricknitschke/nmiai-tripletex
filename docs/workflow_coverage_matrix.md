# Workflow Coverage Matrix

Last updated: 2026-03-22

Purpose: map every unique `task_type` seen in `docs/tasks.csv` to the canonical workflow path the repo should use now, plus the current regression coverage anchor.

Coverage anchor legend:
- `Direct` = dedicated unit tests in `tests/test_workflows.py`
- `Routing` = covered mainly by `tests/test_orchestrator.py` or `tests/test_chief_routing.py`
- `Indirect` = exercised via composed workflow tests, but no dedicated class
- `Gap` = no dedicated automated coverage yet

## Coverage Anchors

| Workflow | Implementation | Coverage |
| --- | --- | --- |
| `register_payroll` | `src/agent/workflows/payroll.py` | Direct: `tests/test_workflows.py::TestRegisterPayroll` |
| `create_customer` | `src/agent/workflows/customer.py` | Direct: `tests/test_workflows.py::TestCreateCustomer` |
| `register_payment` | `src/agent/workflows/payment.py` | Direct: `tests/test_workflows.py::TestRegisterPayment` |
| `register_fx_payment` | `src/agent/workflows/fx_payment.py` | Direct: `tests/test_workflows.py::TestRegisterFxPayment` |
| `create_invoice` / `create_order` | `src/agent/workflows/invoice.py` | Direct: `tests/test_workflows.py::TestCreateInvoice` |
| `create_product` | `src/agent/workflows/product.py` | Direct: `tests/test_workflows.py::TestCreateProduct` |
| `create_project_invoice` | `src/agent/workflows/project_invoice.py` | Direct: `tests/test_workflows.py::TestCreateProjectInvoice` |
| `create_supplier_invoice` | `src/agent/workflows/voucher.py` | Direct: `tests/test_workflows.py::TestCreateSupplierInvoice` |
| `register_expense` | `src/agent/workflows/expense.py` | Direct: `tests/test_workflows.py::TestRegisterExpense` |
| `register_time` | `src/agent/workflows/timesheet.py` | Direct: `tests/test_workflows.py::TestRegisterTime` |
| `create_voucher` | `src/agent/workflows/voucher.py` | Direct: `tests/test_workflows.py::TestCreateVoucher` |
| `verify_trial_balance` | `src/agent/workflows/ledger_analysis.py` | Direct: `tests/test_workflows.py::TestVerifyTrialBalance` |
| `compare_expenses` | `src/agent/workflows/ledger_analysis.py` | Direct: `tests/test_workflows.py::TestCompareExpenses` |
| Hybrid routing fast-paths | `src/agent/orchestrator.py` | Direct: `tests/test_orchestrator.py` |

## Task-Type Matrix

| Raw task_type from `tasks.csv` | Canonical workflow path now | Coverage status |
| --- | --- | --- |
| `create_customer+create_employee+create_project+create_invoice` | `create_customer` -> `create_employee` -> `create_project` -> `create_invoice` | Indirect via covered component workflows; project/customer/employee composition still has no dedicated end-to-end unit test |
| `register_payment` | `register_payment` | Direct |
| `create_invoice` | `create_invoice` | Direct |
| `fallback(dimensions+voucher)` | `create_dimension_voucher` | Gap for dedicated unit coverage of combined dimension+voucher path |
| `create_employee+fallback(payroll)` | `register_payroll` | Direct for payroll; employee creation part still indirect |
| `create_customer+create_employee+create_project` | `create_customer` -> `create_employee` -> `create_project` | Indirect |
| `create_customer(supplier)` | `create_customer` with `isSupplier=true` | Direct for supplier flag behavior |
| `create_employee+create_customer+create_project+fallback(timesheet+project_invoice)` | `create_employee` -> `create_customer` -> `create_project` -> `register_time` -> `create_project_invoice` | Indirect with direct coverage on `register_time` and `create_project_invoice` |
| `create_invoice+register_payment` | `create_invoice` -> `register_payment` | Indirect with direct coverage on both component workflows |
| `create_invoice+create_credit_note` | `create_invoice` -> `create_credit_note` | Gap for dedicated `create_credit_note` unit coverage in current suite |
| `create_customer(supplier)+fallback(supplier_invoice)` | `create_customer` with `isSupplier=true` -> `create_supplier_invoice` | Indirect with direct coverage on both component workflows |
| `create_customer` | `create_customer` | Direct |
| `fallback(dimensions)+create_voucher` | `create_dimension_voucher` when one prompt asks for both dimension and posting; otherwise `create_dimension` -> `create_voucher` | Direct on `create_voucher`; Gap on dedicated combined dimension flow |
| `create_travel_expense` | `create_travel_expense` | Gap |
| `create_employee+create_customer+create_project` | `create_employee` -> `create_customer` -> `create_project` | Indirect |
| `create_department x3` | `create_department` repeated or batched by agent | Gap |
| `create_product` | `create_product` | Direct |
| `register_payment(bulk)+fallback` | `reconcile_bank_statement` for statement-driven bulk matching; otherwise repeated `register_payment` only when explicit invoice set is already known | Routing/Indirect |
| `create_employee+fallback(employment+salary)` | `register_employment` | Gap for dedicated `register_employment` unit coverage |
| `fallback(bank_reconciliation)` | `reconcile_bank_statement` | Routing; no dedicated current unit class |
| `fallback(ledger_analysis)+create_project x3+fallback(activity)` | `compare_expenses` -> `create_projects_batch` | Direct on `compare_expenses`; Gap on `create_projects_batch` |
| `create_department+create_employee+fallback(employment)` | `create_department` -> `register_employment` | Indirect |
| `create_department+create_voucher` | `create_department` -> `create_voucher` | Direct on `create_voucher`; Gap on `create_department` |
| `register_employment` | `register_employment` | Gap |
| `create_supplier_invoice` | `create_supplier_invoice` | Direct |
| `fallback(ledger_correction)` | `analyze_ledger` -> `create_voucher` | Direct on `create_voucher`; Gap on dedicated `analyze_ledger` unit coverage |
| `create_voucher x3` | repeated `create_voucher` | Direct |
| `create_voucher x5` | repeated `create_voucher` | Direct |
| `reconcile_bank_statement` | `reconcile_bank_statement` | Routing/Indirect in current suite |
| `create_project+register_time+create_supplier_invoice+create_invoice` | `create_project` -> `register_time` -> `create_supplier_invoice` -> `create_invoice` | Indirect |
| `create_employee` | `create_employee` | Gap |
| `fallback+create_voucher+create_invoice+register_payment` | For overdue/reminder style prompts: `find_overdue_invoices` -> `send_reminder` -> `register_payment`; for generic mixed tasks use explicit composed workflows only | Routing + direct coverage on `create_voucher` / `create_invoice` / `register_payment` |
| `register_fx_payment` | `register_fx_payment` | Direct |
| `create_employee+create_project+register_time+create_supplier_invoice+create_invoice` | `create_employee` -> `create_project` -> `register_time` -> `create_supplier_invoice` -> `create_invoice` | Indirect |
| `register_payment+create_voucher` | `register_fx_payment` for exchange-difference tasks; otherwise `register_payment` -> `create_voucher` only when payment and manual GL delta are truly separate | Direct on both component workflows |
| `analyze_ledger+create_voucher` | `analyze_ledger` -> `create_voucher` | Direct on `create_voucher`; Gap on dedicated `analyze_ledger` unit coverage |
| `dimension+voucher` | `create_dimension_voucher` | Gap |
| `register_expense` | `register_expense` | Direct |
| `register_payroll(fallback)` | `register_payroll` | Direct |
| `register_payroll` | `register_payroll` | Direct |
| `create_project+create_project_invoice` | `create_project` -> `create_project_invoice` | Direct on `create_project_invoice`; Gap on `create_project` |
| `create_supplier_invoice+fx_payment` | `create_supplier_invoice` and `register_fx_payment` as separate workflows | Direct on both component workflows |
| `find_overdue+create_voucher+create_invoice+register_payment` | Canonical now is `find_overdue_invoices` -> `send_reminder` -> `register_payment`; manual voucher+invoice path is legacy and should be avoided | Routing + direct coverage on `register_payment`; Gap on `find_overdue_invoices` / `send_reminder` unit coverage |
| `create_dimension_voucher` | `create_dimension_voucher` | Gap |
| `register_time+create_project_invoice` | `register_time` -> `create_project_invoice` | Direct |
| `compare_expenses+create_project x3` | `compare_expenses` -> `create_projects_batch` | Direct on `compare_expenses`; Gap on project batch creation |
| `reverse_payment(credit_note)` | `create_credit_note` | Gap |
| `compare_expenses+create_projects_batch` | `compare_expenses` -> `create_projects_batch` | Direct on `compare_expenses`; Gap on `create_projects_batch` |
| `create_voucher x3 + balanceSheet` | repeated `create_voucher` -> `verify_trial_balance` | Direct |
| `create_voucher optimization` | `create_voucher` | Direct |
| `create_voucher x3 + trial_balance` | repeated `create_voucher` -> `verify_trial_balance` | Direct |
| `create_invoice optimization` | `create_invoice` | Direct |
| `create_project_invoice` | `create_project_invoice` | Direct |

## Final Notes

- The highest-volume prompt families in the corpus are now covered by dedicated canonical workflows: `create_supplier_invoice`, `create_invoice`, `reconcile_bank_statement`, `register_payment`, `register_employment`, and `register_expense`.
- Current green baseline on the live repo after the final swipe:
  - `pytest tests/test_workflows.py` -> `69 passed`
  - `pytest tests/test_orchestrator.py` -> `3 passed`
- The biggest remaining coverage gaps are around workflows that exist but still lack dedicated unit classes: `register_employment`, `create_project`, `create_department`, `create_travel_expense`, `create_dimension_voucher`, `create_credit_note`, `find_overdue_invoices`, `send_reminder`, and `create_projects_batch`.