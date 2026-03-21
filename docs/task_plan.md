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

**17 workflows** covering T1/T2/T3 tasks. See `docs/add_workflows.md` for backlog.

**Key design principles:**
- Chief plans fast (no files), Senior executes with full context
- Search before create — some tasks have pre-existing data
- Self-contained workflows — handle prerequisites internally
- All name searches use count=10 + exact match (no partial match bugs)
- 100s deadline with 20s buffer before 120s cloudflare timeout
- BETA endpoints blocked, lookup_api flags them

## Current State — v20+ (Competition Day 3)

**Fully supported (proven scores):**
- Customer creation (8/8, 7/7, 7/7) — bulletproof, all languages
- Invoice creation (4/4, 5/5) — VAT, products, sendToCustomer
- Supplier creation (4/4) — isSupplier=true, email in both fields
- Payment registration (2/2, 2/2) — finds pre-existing invoice, uses actual amount
- Project creation (4/4) — resolves customer + PM
- Department creation (3/3) — parallel creation with unique numbers
- Product creation (5/5) — VAT auto-resolved
- Employee creation (7/7) — with employment record
- Employment contracts (9/10, 9/10) — W9 workflow, missing STYRK extraction

**Supported but struggling:**
- Credit notes: 1/5 — VAT fix deployed (9v), never retested
- Supplier invoices: 1/6 — B11 VAT fix deployed, blocked by 429 rate limits
- Bank reconciliation: 1/2 (×3) — customer payments 5/5 perfect, supplier payments **fixed (P1: auto-creates supplier invoices from CSV)**
- Travel expenses: 3/6 — untested since B7 proxy fix
- Receipt expenses: untested — built but never scored

**No workflow / gaps:**
- Payroll (W1) — no workflow, seen once
- Project invoices (W4) — no workflow, agent spirals on raw API
- Ledger error correction (W11) — **analyze_ledger workflow built (P3)**, needs competition test
- Monthly/yearly closing — **Chief bypass added (P2)**, Senior handles directly for closing tasks
- Custom dimensions — partial (voucher-only), agent misses dimension linking on other entities

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
| Supplier invoices | T3 | `create_supplier_invoice` | 1/6 | B11 VAT direction fixed in code, blocked by 429s + "systemgenererte" conflicts |
| Project invoices | T2-T3 | **MISSING (W4)** | 0 | No workflow. Agent falls back to raw API and spirals |
| Reminder invoices | T2 | Fallback | 4/6 | Account 1500 is system-managed, voucher posting fails |

### Travel Expenses (T2)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Create travel expense | T2 | `create_travel_expense` | 3/6 | Untested since B7 proxy fix. Payment type can be None → silent 422 |
| Delete travel expense | T2 | `delete_travel_expense` | — | Built, never seen in competition |
| Receipt expenses | T3 | `register_expense` | — | Built but never scored. None propagation bug in account resolution |

### Projects (T2-T3)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Create project | T2 | `create_project` | 4/4 | None |
| Full project lifecycle | T3 | Multi-workflow | 2/7 | 4-5 chained workflows. Timesheet had json= bug. Deadline pressure (195s) |

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
| **T3** (×3, max 6pts) | ~40% | Biggest point bleed | Bank recon supplier payments broken, ledger corrections 0/4, closing times out, payroll missing (W1), receipt expense untested |

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
| 9v | "sin IVA" = 0% VAT | Wrong invoice amounts | Prompt: excl-VAT ≠ exempt |

## Tracking Files
- `docs/tasks.csv` — 50+ competition prompts with scores, versions, analysis
- `docs/add_workflows.md` — workflow backlog with priority fixes and failure analysis

## Notes
- Competition: March 19-22, 2026 (Day 3 — ends tomorrow)
- 56 variants per task (7 languages × 8 data sets)
- Rate limit: 10 submissions per task per day
- Cloud Run: project ainm26osl-722, concurrency=1
- Model: gemini-3.1-pro-preview (global location)
- Deploy: `bash scripts/deploy.bash <version>`
- Logs: `bash scripts/get_prod_logs.bash <version>`
