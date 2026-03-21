# Workflows to Add / Enhance

Tracking new workflows and enhancements needed based on competition task logs.

## Enhancements to Existing Workflows

### create_voucher — add dimension support
- **Status:** TODO
- **Why:** 3/6 on "Kostsenter" task — dimension created OK via raw API but voucher posting not linked to dimension
- **Fix:** Add `freeAccountingDimension1` field to posting schema. The Posting object supports `freeAccountingDimension1/2/3` as `{id: dimensionValueId}`.
- **Example prompt:** "Bokfør et bilag på konto 7300 for 25700 kr, knyttet til dimensjonsverdien 'IT'"

### create_voucher — support balancing account
- **Status:** TODO
- **Why:** Voucher needs debit + credit to balance. Agent sometimes forgets the credit side.
- **Fix:** Auto-add balancing posting to account 1920 if only one posting is provided.

## New Workflows Needed

### W1: Payroll (salary/lønn)
- **Status:** RESEARCH NEEDED
- **Priority:** Medium — seen in ES prompt (Fernando López payroll)
- **API:** Unknown — need to research salary endpoints
- **Example prompt:** "Ejecute la nómina de Fernando López para este mes. Salario base 37850 NOK + bonificación 9200 NOK."

### W4: Project Invoice
- **Status:** RESEARCH NEEDED
- **Priority:** Medium — always paired with time registration
- **API:** Likely PUT /order/{id}/:invoice with project hours, or dedicated project invoice endpoint
- **Example prompt:** "Gere uma fatura de projeto ao cliente com base nas horas registadas"

### W5: Custom Dimensions (create_dimension)
- **Status:** OPTIONAL — agent handles via raw API
- **Priority:** Low — agent scored 3/6 without a dedicated workflow
- **API:** POST /ledger/accountingDimensionName + POST /ledger/accountingDimensionValue
- **Note:** Could wrap in a workflow for reliability, but raw API approach is working

### W7: Bank Reconciliation
- **Status:** NOT SEEN YET
- **Priority:** Low — may appear in T3
- **API:** Unknown

### W8: Supplier Payment
- **Status:** NOT SEEN YET
- **Priority:** Low — may appear in T3
- **API:** POST /supplierInvoice/{invoiceId}/:addPayment

## Completed Workflows (14 total)

| Workflow | Phase | Task Types |
|----------|-------|------------|
| create_employee | T1 | Employee creation |
| create_customer | T1 | Customer + supplier creation |
| create_department | T1 | Department creation |
| create_product | T1 | Product creation |
| create_order | T1 | Order creation |
| create_invoice | T1 | Invoice (auto-creates customer, products, VAT) |
| register_payment | T2 | Payment registration (self-contained, searches invoice) |
| create_credit_note | T2 | Credit notes (self-contained, searches invoice) |
| create_travel_expense | T2 | Travel expenses + per diem |
| delete_travel_expense | T2 | Delete travel expense by ID/employee/title |
| create_project | T2 | Project creation (resolves customer + PM) |
| create_supplier_invoice | T3 | Supplier invoices via voucher (debit/credit postings) |
| create_voucher | T3 | Manual journal entries |
| register_time | T3 | Timesheet hours registration |
