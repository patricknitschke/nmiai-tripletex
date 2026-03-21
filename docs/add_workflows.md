# Workflows to Add / Enhance

Tracking new workflows and enhancements needed based on competition task logs.

## Priority Fixes (Day 3) — Ranked by Expected Points

See `docs/task_plan.md` → "Weakness Map by Competition Category" for full context.

### P1: Bank Recon Supplier Payments — 3-6 pts (3 tasks)
- **Status:** ✅ IMPLEMENTED
- **What was done:** When no existing supplier invoice matches, workflow now auto-creates one via `create_supplier_invoice()` using supplier name extracted from CSV description (6-language regex), `amountOut` as total, default account 7300 + 25% VAT. Then immediately registers payment.
- **Files changed:** `src/agent/workflows/bank_reconciliation.py` — added `_extract_supplier_name()` helper + auto-create logic in supplier payment block

### P2: Monthly/Yearly Closing Timeout — 6-10 pts (2 tasks)
- **Status:** ✅ IMPLEMENTED
- **What was done:** Added keyword detection in `_run_hybrid_mode()` — 13 closing-related keywords (NO/NN/EN/ES/FR/PT/DE) skip Chief entirely, routing straight to Senior. Saves 30s+ of Chief timeout overhead.
- **Files changed:** `src/agent/orchestrator.py` — added `_CLOSING_KEYWORDS` check before `chief_plan()` call

### P3: Ledger Error Correction Workflow (W11) — up to 6 pts (1 task, T3)
- **Status:** ✅ IMPLEMENTED
- **What was done:** New `analyze_ledger` workflow (#18) in `src/agent/workflows/ledger_analysis.py`. Fetches all postings for a date range via `GET /ledger/posting`, groups by voucher, detects 3 error types (imbalanced vouchers, duplicate postings, orphaned VAT). Returns structured error list + voucher summaries. Senior uses `create_voucher` to post corrections.
- **Files created:** `src/agent/workflows/ledger_analysis.py`
- **Files changed:** `src/agent/workflows/__init__.py`, `src/agent/workflows/schemas.py`

### P4: Credit Note Retest — 6-12 pts (3 tasks, T2×2)
- **Status:** FIXES DEPLOYED, NEVER RETESTED
- **What changed:** 9v VAT fix ("sin IVA" = excl-VAT, not 0%), B6 search-before-create for pre-existing invoices. Both deployed since v18.
- **Action:** Just resubmit credit note tasks. No code changes needed.

## Open Enhancements

### B16 (NEW): Employee Start-Date Routing — 2-4 pts
- **Status:** BUG CONFIRMED — needs fix
- **What:** Chief picks `create_employee` instead of `register_employment` when prompt mentions a start date WITHOUT employment contract keywords (arbeidskontrakt/tilbudsbrev).
- **Evidence:** v19 Nathan Moreau task scored 5/7 — employee created but no employment record (startDate ignored). Schema notes have the hint but Chief's PLAN_PROMPT doesn't enforce it.
- **Fix:** Add explicit instruction to Chief PLAN_PROMPT:
  ```
  - If the prompt mentions a START DATE for an employee (date de début/tiltredelse/fecha de inicio/
    Startdatum/data de início/Anfangsdatum), ALWAYS use register_employment instead of create_employee.
  ```
- **File:** `src/agent/agents/chief.py` — add after line 98 (the "employment contracts" line)
- **Impact:** Employee tasks with start dates go from 5/7 → 7/7

### B18 (NEW): Supplier Invoice voucherType Conflict — blocks P1 bank recon
- **Status:** BUG CONFIRMED — code fix exists but may not be deployed
- **What:** voucherType "Leverandørfaktura" triggers Tripletex system-generated posting rules that conflict with our explicit postings. First attempt fails (vatType conflict), retry pops voucherType but may also fail.
- **Evidence:** v21 bank recon — "both vatType AND manual split rejected". 4 consecutive bank recon tasks stuck at 1/2.
- **Fix (in code at voucher.py line 250):** Already pops voucherType on retry. But need to verify:
  1. Try WITHOUT voucherType on FIRST attempt (not just retry)
  2. Ensure manual split amounts balance exactly
  3. Possibly use a generic voucherType (e.g. "Manuell") instead of "Leverandørfaktura"
- **File:** `src/agent/workflows/voucher.py` — `create_supplier_invoice()` function
- **Impact:** Bank recon supplier payments (3-6 pts across 4 tasks) + standalone supplier invoices (6 pts)

### W12: Foreign Currency Payment + Exchange Difference (disagio/agio)
- **Status:** SEEN — 1/4, no dedicated workflow
- **Priority:** Medium — T3 task, 4 checks × 56 variants = potentially high points
- **API:** PUT /invoice/{id}/:payment + POST /ledger/voucher
- **Example prompt (PT):** "Enviámos uma fatura de 19074 EUR ao Oceano Lda taxa 11.69 NOK/EUR. Cliente pagou a 11.28 NOK/EUR. Registe pagamento e lance diferença cambial (disagio)."
- **What went wrong (v21):**
  1. Chief timed out → no plan
  2. Senior spent 10 iterations investigating (customer, invoices, orders, vouchers, postings) but 0 POSTs
  3. Never registered payment or posted exchange difference
- **What the workflow needs:**
  1. Find existing invoice for the customer (by org number or name)
  2. Register payment at the new exchange rate amount (19074 × 11.28 = 215,154.72 NOK)
  3. Calculate exchange difference: (11.69 - 11.28) × 19074 = 7,820.34 NOK loss
  4. Post disagio voucher: debit 8060 (Valutadifferanse/exchange loss), credit 1500 (AR) or let it balance via invoice
  5. Or if agio (gain): debit 1500, credit 8060

### B19 (NEW): Bank recon — undetected customer payment descriptions
- **Status:** LOW RISK — currently working because bank CSV is always Norwegian
- **What:** Customer payment detection requires "innbetaling" or "betaling" in description. If bank CSVs ever use non-Norwegian descriptions, customer matching would break.
- **Evidence:** 5/5 customer payments on all 4 bank recon tasks — not currently failing.
- **Fix:** Fall back to `amount_in > 0` as primary detection (any positive incoming amount = customer payment)
- **Impact:** Defensive fix, no immediate point gain

### create_voucher — support balancing account
- **Status:** TODO
- **Priority:** Low — nice to have
- **Why:** Voucher needs debit + credit to balance. Agent sometimes forgets the credit side.
- **Fix:** Auto-add balancing posting to account 1920 if only one posting is provided.

## Open Workflows

### W1: Payroll (salary/lønn)
- **Status:** RESEARCH NEEDED
- **Priority:** Medium — seen once (ES prompt, Fernando López)
- **API:** Unknown — need to research salary endpoints
- **Example:** "Ejecute la nómina de Fernando López para este mes. Salario base 37850 NOK + bonificación 9200 NOK."

### W4: Project Invoice
- **Status:** RESEARCH NEEDED
- **Priority:** Medium — paired with time registration
- **API:** Likely PUT /order/{id}/:invoice with project hours
- **Example:** "Gere uma fatura de projeto ao cliente com base nas horas registadas"

### W11: Ledger Error Correction
- **Status:** ✅ IMPLEMENTED as `analyze_ledger` workflow (P3)
- **What it does:** Fetches postings → detects imbalance/duplicates/orphaned VAT → returns structured errors for Senior to correct via `create_voucher`
- **Needs competition test** to verify scoring improvement from 0/4

## Open Investigations

### Reminder fee posting to account 1500
- **Status:** INVESTIGATE — 4/6 without fix
- **Issue:** Account 1500 (Kundefordringer/AR) is system-managed, can't be posted to via voucher
- **Theory:** Reminder invoice auto-posts to 1500, making separate voucher redundant

### Voucher "systemgenererte" error
- **Status:** SAFETY NET ADDED (v20)
- **What:** When vatType on a posting triggers system-generated conflict, supplier invoice workflow retries with manual 3-posting split (net + VAT + credit). create_voucher logs clear warning.
- **Root cause:** Setting vatType on a posting makes Tripletex auto-generate VAT postings that conflict with explicit postings.

## Fixed (remove from active tracking)

| Item | Fix | Version |
|------|-----|---------|
| B7: Proxy token expiry | concurrency=1 on Cloud Run | v15 |
| B8: Chief plan truncation | max_tokens=8192 + max 5 steps | v17 |
| B9: Employee wrong department | Resolve departmentName in workflow | v17 |
| B10: Chief PDF timeout | 30s timeout + skip files for Chief | v17/v18 |
| B11: Wrong input VAT type | Case-insensitive + exclude wrong direction | v18 |
| B12: HTML 404 crash | try/except in TripletexClient | v18 |
| B13: Chief receives PDFs | Skip files in chief_plan() | v18 |
| B14: 429 rate limit crash | Retry with backoff (3 attempts) | v19 |
| W7: Bank reconciliation | Built workflow with CSV parsing + amount matching | v18 |
| W8: Supplier payment in bank recon | Code exists (detection + POST endpoint) but **broken in competition** — see P1 above. Fresh accounts have no supplier invoices to match | v20 (incomplete) |
| W9: Employment contract | register_employment workflow (4 steps) | v17 |
| W10: Receipt expense | register_expense workflow with dept + VAT | v17 |
| Voucher dimension support | dimensionId field in create_voucher | v17 |
| Slim Chief catalog | Names + notes only, no field specs | v18 |
| STYRK extraction hint | Senior prompt lists all employment PDF fields | v19 |
| Chief routes register_employment | When start date mentioned in prompt | v20 (PARTIAL — schema hint exists but Chief PLAN_PROMPT doesn't enforce. See B16) |

## Completed Workflows (17 total)

| Workflow | Tier | What it does |
|----------|------|-------------|
| create_employee | T1 | Employee + dept resolution + name/email search |
| create_customer | T1 | Customer/supplier + address + dedup |
| create_department | T1 | Department creation |
| create_product | T1 | Product with VAT type |
| create_order | T1 | Order creation |
| create_invoice | T1 | Invoice (auto-creates customer, products, VAT) |
| register_payment | T2 | Self-contained: finds invoice, uses actual amount |
| create_credit_note | T2 | Self-contained: finds invoice, creates if needed |
| create_travel_expense | T2 | Travel + per diem + costs |
| delete_travel_expense | T2 | Delete by ID/employee/title |
| create_project | T2 | Project + customer/PM resolution |
| create_supplier_invoice | T3 | Voucher with debit/credit + input VAT + retry |
| create_voucher | T3 | Manual journal entries + dimension support |
| register_time | T3 | Timesheet hours on project activity |
| register_employment | T3 | Full contract: employee + dept + employment + salary + hours |
| reconcile_bank_statement | T3 | CSV parser, invoice matching, customer + supplier payments |
| register_expense | T3 | Receipt → voucher with department + input VAT |
