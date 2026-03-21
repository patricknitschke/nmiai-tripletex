# Task Plan: NMiAI Tripletex AI Accounting Agent

## Goal
Build a Python AI agent (FastAPI + Gemini) that receives accounting task prompts via `/solve`, interprets them with an LLM, then executes pre-built workflows against the Tripletex API. Deploy to GCP Cloud Run for the NM i AI competition (March 19-22, 2026).

## Architecture (v6 — Hybrid: Chief plans, Senior executes)

```
POST /solve (100s deadline)
  → Chief plans (1 LLM call, ~6-10s, text only — no PDFs)
    - Reads prompt, reasons about dependencies, max 5 steps
    - 30s timeout: if slow, skip to Senior directly
    - Safety net: detects "give up" plans → falls back to Senior
  → Senior executes with plan as context preamble
    - Receives full prompt + files (PDFs, CSVs) + Chief's plan
    - Calls workflows + raw API tools in a tool loop (max 15 iterations)
    - Passes IDs between workflow calls
```

**23 workflows** covering T1/T2/T3 tasks. See `docs/add_workflows.md` for backlog.

**Key design principles:**
- Chief plans fast (no files), Senior executes with full context
- Search before create — some tasks have pre-existing data
- Self-contained workflows — handle prerequisites internally
- All name searches use count=10 + exact match (no partial match bugs)
- 100s deadline with 20s buffer before 120s cloudflare timeout
- BETA endpoints blocked, lookup_api flags them

## Current State — v30 (Competition Day 3)

**Fully supported (proven scores):**
- Customer creation (8/8, 7/7, 7/7) — bulletproof, all languages
- Invoice creation (4/4, 5/5) — VAT, products, sendToCustomer
- Supplier creation (4/4) — isSupplier=true, email in both fields
- Payment registration (2/2, 2/2) — finds pre-existing invoice, uses actual amount
- Project creation (4/4) — resolves customer + PM (v28: projectManagerEmail/Name support)
- Department creation (3/3) — parallel creation with unique numbers
- Product creation (5/5) — VAT auto-resolved
- Employee creation (7/7) — with employment record
- Employment contracts (9/10, 9/10) — W9 workflow, missing STYRK extraction

**Supported but struggling:**
- Credit notes: 1/5 — VAT fix deployed (9v), never retested
- Supplier invoices: 5/6 — **B23 fix**: vatType stripped on retry in `_post_voucher`. Previous no-VAT type was OUTPUT on INPUT accounts
- Bank reconciliation: 1/2 (×3) — customer payments 5/5 perfect, supplier payments **fixed in v27 (P1: auto-creates supplier invoices + B18 voucherType fix + sendToLedger=false retry)** — needs retest
- Travel expenses: 3/6 — untested since B7 proxy fix
- Receipt expenses: untested — built but never scored

**No workflow / gaps:**
- Payroll (W1) — **register_payroll workflow built (v33)**, handles base salary + bonus via /salary/transaction with specifications. Auto-creates employment if missing. **B27 fix**: full GET before PUT for version. **B28 fix**: employment now linked to company division via GET /division (required for salary transactions).
- Project invoices (W4) — no workflow, agent spirals on raw API
- Ledger error correction (W11) — **analyze_ledger workflow built (P3)**, needs competition test
- Monthly/yearly closing — **Chief bypass added (P2)**, Senior handles directly for closing tasks
- Custom dimensions — **create_dimension workflow built (v30)**, handles name + values in one call. Voucher dimension linking works via freeAccountingDimension1/2/3. **create_dimension_voucher combo (v34)** chains dimension creation + voucher posting in one atomic call

## Weakness Map by Competition Category

### Employees (T1 create, T3 employment contracts)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Create employee | T1 | `create_employee` | 7/7 | None |
| Set roles/admin | T1 | `create_employee` | — | Entitlement template = ALL_PRIVILEGES, works |
| Employment contracts (PDF) | T3 | `register_employment` | 9/10 | Missing STYRK code extraction from PDFs — costs 1 check each time |
| Employee with start date (no PDF) | T1→T3 | **Misrouted** | 5/7 | Chief picks `create_employee` instead of `register_employment`. Routing hint exists in schema but LLM ignores it |

### Customers & Products (T1)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Create customer | T1 | `create_customer` | 7/7 | None — bulletproof |
| Register supplier | T1 | `create_customer` | 4/4 | None — B5 email fix working |
| Create product | T1 | `create_product` | 5/5 | None |

### Invoicing (T1-T3)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Create invoice | T1 | `create_invoice` | 5/5 | None |
| Invoice + payment | T2 | `create_invoice` + `register_payment` | 2/2 | ID passing between steps sometimes breaks |
| Register payment | T2 | `register_payment` | 2/2 | B6 fixed — searches for pre-existing invoice |
| Credit notes | T2 | `create_credit_note` | 1/5 | VAT interpretation + search-before-create both improved but **never retested** |
| Supplier invoices | T3 | `create_supplier_invoice` | 1/6 | **B22 FIXED:** Manual 3-posting split with `amount` + no-VAT type. Previous approach used amountGross+vatType which conflicted with account default VAT config |
| Project invoices | T2-T3 | `create_project_invoice` | 0 | **NEW v34**: Fixed-price % invoicing + time-based invoicing from timesheet hours. Needs competition test |
| Reminder invoices | T2 | Fallback | 4/6 | Account 1500 is system-managed, voucher posting fails |

### Travel Expenses (T2)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Create travel expense | T2 | `create_travel_expense` | 4/6 | **B21 FIXED:** Per diem was added as cost line, now uses `/travelExpense/perDiemCompensation` endpoint with rateType+rateCategory |
| Delete travel expense | T2 | `delete_travel_expense` | — | Built, never seen in competition |
| Receipt expenses | T3 | `register_expense` | 0/0 | **B24 FIXED:** Restructured from 2-posting amountGross (broken) to 3-posting with explicit VAT split on 2710 + no-VAT type (same as create_supplier_invoice). Needs retest |

### Projects (T2-T3)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Create project | T2 | `create_project` | 4/4 | None |
| Full project lifecycle | T3 | Multi-workflow | 6/7 (v21), 2/7 (v28 pre-fix) | **v28 FIXED:** B19 timesheet date floor + PM email. v21 scored 6/7 (only supplier invoice failed). v28 pre-fix regressed due to new date bug — now fixed with clamp + prompt |

### Corrections (T2-T3)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Credit notes (reversal) | T2 | `create_credit_note` | 1/5 | See Invoicing |
| Delete entries | T2 | `delete_travel_expense` | — | Only travel deletion. No general delete workflow |
| Ledger error correction | T3 | `analyze_ledger` + `create_voucher` | **needs test** | **P3 BUILT:** analyze_ledger fetches postings, detects imbalances/duplicates/orphaned VAT. Senior uses create_voucher to correct |
| Monthly/yearly closing | T3 | `create_voucher` | 1/6 | **P2 FIXED:** Chief bypassed for closing tasks (keyword detection). Senior handles directly — saves 30s+ |

### Departments (T1)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Create department | T1 | `create_department` | 3/3 | None — bulletproof |
| Enable accounting modules | T1? | **MISSING** | — | No workflow. API exists (BETA). Unknown if competition tests this |

### Bank Reconciliation (T3)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Customer payments | T3 | `reconcile_bank_statement` | 5/5 | None — perfect on every run |
| Supplier payments | T3 | `reconcile_bank_statement` | **needs test** | **P1 FIXED:** Now auto-creates supplier invoices from CSV before matching. Extracts supplier name from description (6 languages) |
| Bank fees/interest | T3 | Skipped | 0 | Code just logs "use create_voucher" and skips. No auto-voucher |

## Tier Coverage Summary

| Tier | Max Score | Coverage | Key Gaps |
|---|---|---|---|
| **T1** (×1, max 2pts) | ~95% | Bulletproof | Only gap: employee+startDate routing (minor) |
| **T2** (×2, max 4pts) | ~70% | Some fragile | Credit notes weak (1/5), travel untested, project invoices missing (W4), multi-step ID passing unreliable |
| **T3** (×3, max 6pts) | ~50% | Biggest point bleed | Bank recon supplier payments broken, ledger corrections 0/4, closing needs separate vouchers, project lifecycle improved (6/7→retest), receipt expense dead (Chief timeout) |

## Priority Fixes by Expected Points

| # | Fix | Tasks | Expected Points | Status | Notes |
|---|-----|-------|----------------|--------|-------|
| P1 | Bank recon: create supplier invoices from CSV before matching | 3 | 3-6 pts | ✅ **IMPLEMENTED** | Auto-creates supplier invoices from CSV description + amount, then registers payment |
| P2 | Monthly/yearly closing: skip Chief | 2 | 6-10 pts | ✅ **IMPLEMENTED** | Keyword detection bypasses Chief for closing/depreciation/accrual tasks |
| P3 | Ledger error correction: analyze_ledger workflow | 1 | up to 6 pts (T3) | ✅ **IMPLEMENTED** | Fetches postings, detects imbalances/duplicates/orphaned VAT → Senior fixes via create_voucher |
| P4 | Credit note retest | 3 | 6-12 pts (T2×2) | ⏳ Resubmit | VAT fix + search-before-create already deployed. Just needs resubmission |

## Workflows (18 total)

| # | Workflow | Tier | What it does |
|---|----------|------|-------------|
| 1 | create_employee | T1 | Employee + department resolution + name search |
| 2 | create_customer | T1 | Customer/supplier + address + dedup by org/name |
| 3 | create_department | T1 | Department creation |
| 4 | create_product | T1 | Product with VAT type |
| 5 | create_order | T1 | Order creation |
| 6 | create_invoice | T1 | Invoice (auto-creates customer, products, VAT) |
| 7 | register_payment | T2 | Self-contained: finds invoice, uses actual amount |
| 8 | create_credit_note | T2 | Self-contained: finds invoice, creates if needed |
| 9 | create_travel_expense | T2 | Travel + per diem + costs |
| 10 | delete_travel_expense | T2 | Delete by ID/employee/title |
| 11 | create_project | T2 | Project + customer/PM resolution |
| 12 | create_supplier_invoice | T3 | Voucher with debit/credit postings + input VAT |
| 13 | create_voucher | T3 | Manual journal entries + dimension support |
| 14 | register_time | T3 | Timesheet hours on project activity |
| 15 | register_employment | T3 | Full contract: employee + dept + employment + salary + hours |
| 16 | reconcile_bank_statement | T3 | CSV parser, invoice matching, bulk payments + **auto-creates supplier invoices** |
| 17 | register_expense | T3 | Receipt → voucher with department + input VAT |
| 18 | analyze_ledger | T3 | Fetch postings, detect errors (imbalance/duplicate/orphaned VAT) |
| 19 | create_dimension | T2 | Custom accounting dimension + values in one call |

## Bug Fix History

| Bug | Description | Impact | Fix |
|-----|-------------|--------|-----|
| B1 | OpenAPI spec not in Docker image | Crashed lookup_api on GCP | Fixed .dockerignore |
| B3 | Chief "gives up" on fresh-env tasks | 0 API calls on payment/credit note | Safety net + prompt fix |
| B4 | Specialist duplication | Double invoices/payments | Replaced with Senior-with-preamble |
| B5 | Email in invoiceEmail only | 2/5 on supplier creation | Copy to email field too |
| B6 | "Fresh empty" assumption wrong | Credit notes/payments fail | Search before create |
| B7 | Proxy token expiry | 403 on concurrent tasks | concurrency=1 on Cloud Run |
| B8 | Chief plan truncation | CSV tasks fail | max_tokens=8192 + max 5 steps |
| B9 | Employee wrong department | Employment contracts get default dept | Resolve departmentName |
| B10 | Chief PDF timeout | 0/5 on all PDF tasks | 30s timeout cap |
| B11 | Wrong input VAT type | Supplier invoice 422 | Case-insensitive + exclude wrong direction |
| B12 | HTML 404 crash | Non-JSON response kills task | try/except in TripletexClient |
| B13 | Chief receives PDFs | Slow planning → no time for execution | Skip files in chief_plan() |
| B14 | 429 RESOURCE_EXHAUSTED | Unhandled crash on rate limit | Retry with backoff (3 attempts) |
| 9v | "sin IVA" = 0% VAT | Wrong invoice amounts | Prompt: excl-VAT ≠ exempt |
| B16 | Employee start-date routing | 5/7 → 7/7 on employee tasks | ✅ FIXED v27 — Chief PLAN_PROMPT has start-date keywords + Senior prompt too |
| B18 | Supplier invoice voucherType | Blocks P1 bank recon (1/2 ×4) | ✅ FIXED v27 — voucherType=None on first attempt + sendToLedger=false retry |
| B19 | Timesheet date before project start | Project lifecycle 5/7→2/7 regression | ✅ FIXED v28 — register_time clamps date to project startDate + Chief/Senior prompts warn against past dates |
| B20 | Wrong project manager (fallback grab) | PM set to random employee | ✅ FIXED v28 — create_project resolves by projectManagerEmail → projectManagerName → firstName+lastName → fallback |
| B21 | Per diem added as cost line | 2/6 checks fail on travel expense — perDiemCompensations empty | ✅ FIXED — uses `/travelExpense/perDiemCompensation` endpoint with rateType+rateCategory lookup |
| B22 | Supplier invoice "systemgenererte" 422 | 1/6 on supplier invoices — account default VAT config conflicts with amountGross+vatType | ✅ FIXED — manual 3-posting split with `amount` (net) + explicit no-VAT type. Also hardened `_post_voucher` retries with no-VAT type |
| B23 | Voucher systemgenererte on ALL accounts | Dimension voucher 3/6, supplier invoice 5/6 — no-VAT type id=5 is OUTPUT ("Ingen utgående avgift") but expense accounts need INPUT. All retries fail | ✅ FIXED — `_post_voucher` retry 1 now strips vatType entirely (lets Tripletex use account default). Also added `create_dimension` workflow to avoid API fumbling |
| B24 | register_expense 2-posting structure triggers systemgenererte on ALL accounts | Receipt expense 0/0 — amountGross on expense accounts triggers auto-generated VAT postings → 422. Even _post_voucher retries fail because they just rename field without splitting VAT. | ✅ FIXED — Restructured to 3-posting like create_supplier_invoice: expense (net) + VAT 2710 + bank (gross credit), all with `amount` + explicit no-VAT type |
| B25 | Manual 3-posting split WITH correct vatType STILL fails systemgenererte | create_supplier_invoice 0/0, create_voucher 0/0 — posting to 2710 (VAT account) IS the system-generated posting Tripletex auto-creates. Row 0 rejected. 2400 (supplier ledger) also system-managed. | ✅ FIXED B25v2 — Supplier invoice: 1 posting with `amountGross` + real `vatType` (25% input) + `supplier`. Tripletex auto-generates 2710+2400. Expense: 2 postings (expense amountGross+vatType, bank negative). `_resolve_postings` drops LLM-generated 2710/2400 postings. |
| B26 | B25v2 single posting still fails: guiRow 0 reserved for system-generated | Correct payload (1 posting + amountGross + vatType + supplier) rejected with same "rad 0 systemgenererte" error | ✅ FIXED — Add `"row": 1` to all user-created postings. Row 0 is reserved for Tripletex's auto-generated counterpart lines (2400 credit, MVA). |

## Day 3 Evening — Priority Action Queue (March 21)

**v28 deployed with B19+B20 fixes (timesheet date clamp + PM resolution). All P1-P4 ready.**

| # | Action | Expected Points | Effort | Status |
|---|--------|----------------|--------|--------|
| 1 | **Resubmit project lifecycle** | up to 6 pts (T3×3) | Zero code changes | ⏳ RESUBMIT — B19+B20 fix timesheet dates + PM. v21 scored 6/7, v28 should match or beat |
| 2 | **Resubmit credit notes (P4)** | 6-12 pts (T2×2, 3 tasks) | Zero code changes | ⏳ RESUBMIT — VAT + search fixes in v18+ |
| 3 | **Resubmit bank reconciliation** | 3-6 pts (supplier payments) | Zero code changes | ⏳ RESUBMIT — P1 + B18 fix now in v27+ |
| 4 | **Resubmit supplier invoices** | up to 6 pts (T3) | Zero code changes | ⏳ RESUBMIT — B18 voucherType fix in v27+ |
| 5 | **Test monthly closing (P2)** | 6-10 pts (T3) | 1 submission | ⏳ RESUBMIT — Chief bypass + separate vouchers |
| 6 | **Test ledger correction (P3)** | Up to 6 pts (T3) | 1 submission | ⏳ NEVER TESTED — analyze_ledger workflow built |
| 7 | **Resubmit employee+startDate tasks** | 2-4 pts | Zero code changes | ⏳ RESUBMIT — B16 fix in v27+ |

**All code fixes are deployed. The points are on the table — just need resubmissions.**

**Tasks NOT worth fixing (low ROI):**
- Payroll (W1): **FIXED** — register_payroll workflow built after 4/4 fail on March 21. Now seen multiple times.
- Forex disagio: Complex edge case, seen twice — would need dedicated workflow

## Tracking Files
- `docs/tasks.csv` — 54+ competition prompts with scores, versions, analysis + summary section
- `docs/add_workflows.md` — workflow backlog with priority fixes and failure analysis

## Notes
- Competition: March 19-22, 2026 (Day 3 of 4 — TOMORROW IS THE LAST DAY)
- 56 variants per task (7 languages × 8 data sets)
- Rate limit: 10 submissions per task per day
- Cloud Run: project ainm26osl-722, concurrency=1
- Model: gemini-3.1-pro-preview (global location)
- Deploy: `bash scripts/deploy.bash <version>`
- Logs: `bash scripts/get_prod_logs.bash <version>`
