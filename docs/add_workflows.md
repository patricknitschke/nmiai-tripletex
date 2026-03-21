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

### W7: Bank Reconciliation (bankavsteming)
- **Status:** SEEN — 0/2, needs dedicated workflow
- **Priority:** High — T3 task, complex, currently 0/2
- **API:** GET /invoice (match by customer+amount), PUT /invoice/{id}/:payment, POST /supplierInvoice/{id}/:addPayment
- **Example prompt:** "Avstem bankutskriften (vedlagt CSV) mot åpne fakturaer i Tripletex. Match innbetalinger til kundefakturaer og utbetalinger til leverandørfakturaer. Håndter delbetalinger korrekt."
- **What went wrong (v13):**
  1. All customer name searches resolved to same customer ID (API returns first partial match)
  2. All 5 payments stacked on same invoice (-65,225 overpaid)
  3. Invoice number from CSV (1001, 1002) doesn't match Tripletex internal numbers
  4. Never reached supplier payments or bank fees — deadline hit at 105s
- **What the workflow needs:**
  1. Parse CSV: separate incoming (customer payments) vs outgoing (supplier payments) vs fees
  2. Match invoices by: invoice number first, then by customer+amount, then by amount alone
  3. Register each payment with correct amount (partial payments = paidAmount from CSV, not full invoice)
  4. Handle supplier outgoing payments via POST /supplierInvoice/{id}/:addPayment
  5. Handle bank fees via create_voucher (debit 7770 Bankgebyr, credit 1920)
  6. Must be fast — process all rows in one workflow call, not one-by-one via LLM loop

### W9: Employment Contract Registration (arbeidskontrakt)
- **Status:** SEEN — 7/15, needs dedicated workflow
- **Priority:** High — T3 task, 15 checks = high point value
- **API:** POST /employee, POST /employee/employment, POST /employee/employment/details, salary endpoints
- **Example prompt:** "Has recibido un contrato de trabajo (PDF). Crea el empleado con todos los datos: numero de identidad, departamento, codigo de ocupacion, salario, porcentaje de empleo y fecha de inicio."
- **What went wrong (v15):**
  1. Employee created with wrong department (existing default, not the contract's "Regnskap")
  2. Department created AFTER employee — never re-linked
  3. Agent spiraled 8 iterations on lookup_api searching for employment/salary endpoints, never made the call
  4. No employment record (STYRK code, start date, percentage)
  5. No salary record
- **What the workflow needs:**
  1. Create department first (if specified in contract)
  2. Create employee linked to that department
  3. POST /employee/employment with: startDate, occupationCode (STYRK), percentage, employeeId
  4. POST salary endpoint with: annualSalary, paymentType
  5. All in one workflow call from the PDF-extracted data

### B9: Employee workflow ignores specified department
- **Status:** TODO — quick fix
- **Priority:** High — affects all employee tasks where department is specified
- **Symptom:** Employee always linked to first `count=1` department, not the one just created or specified in the prompt
- **Root cause:** `create_employee` workflow fetches `GET /department?count=1` and uses whatever comes back, ignoring any `departmentId` or `departmentName` passed in the data
- **Seen in:** Carmen Pérez (Regnskap → wrong dept), Rita Almeida (Drift → wrong dept)
- **Fix:** Check if data contains `departmentId` or `departmentName`, resolve to ID, and use that instead of the default first department

### B10: Chief LLM call timeout on PDF/image inputs
- **Status:** TODO — quick fix
- **Priority:** Critical — causes 0/X on any PDF task where Chief takes >60s
- **Symptom:** Chief planning call takes 146s on PDF receipt, Senior gets 0 iterations
- **Root cause:** No timeout on the Chief `complete()` call. gemini-3.1-pro-preview is slow on multimodal
- **Seen in:** Oppbevaringsboks receipt (151.8s total, 0 API calls)
- **Fix:** Add timeout to Chief planning (e.g., 30s max). If Chief times out, skip plan and let Senior work from scratch with the full 100s

### B7: Proxy Token Expiry on Long Tasks
- **Status:** INVESTIGATE
- **Priority:** High — kills any task that takes >60s if concurrent tasks share the token
- **Symptom:** All API calls return 403 "Invalid or expired proxy token"
- **Possible cause:** Cloud Run concurrency >1, or tasks running too long
- **Fix options:** Set Cloud Run max-instances/concurrency to 1, or detect 403 and bail early

### W8: Supplier Payment
- **Status:** NOT SEEN YET
- **Priority:** Low — may appear in T3
- **API:** POST /supplierInvoice/{invoiceId}/:addPayment

### W11: Ledger Error Correction (corrective entries)
- **Status:** SEEN — 0/4, needs dedicated workflow
- **Priority:** Medium — T3 task, 4 checks
- **API:** GET /ledger/voucher + GET /ledger/posting + POST /ledger/voucher (corrective)
- **Example prompt (FR):** "Nous avons découvert des erreurs dans le grand livre... une écriture sur le mauvais compte (6500 au lieu de 6540, 6800 NOK), une pièce en double (7000, 1300 NOK), une ligne de TVA manquante (4300, 17300 NOK HT), et un montant incorrect (6300, 10150 au lieu de 7450 NOK). Corrigez avec des écritures correctives."
- **What went wrong (v17):**
  1. Chief timed out on complex French prompt (no PDF, just long reasoning needed)
  2. Senior fetched each voucher twice (without fields, then with fields=*) — wasted 40s
  3. Never created any corrective entries — ran out of time after 3 iterations of reading
- **What the workflow needs:**
  1. Accept a list of errors: {type: "wrong_account"|"duplicate"|"missing_vat"|"wrong_amount", account, amount, correctAccount, correctAmount}
  2. Search voucher postings by account + amount to find the erroneous entry
  3. Create corrective voucher: reverse original posting + post correct one
  4. Handle 4 error types: wrong account → repost to correct account, duplicate → reverse it, missing VAT → add VAT posting, wrong amount → reverse + repost correct amount

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
