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

## Current State — v17 deploying

**What's working (perfect scores):**
- Customer creation (8/8, 7/7, 7/7) — all languages, with address
- Invoice creation (4/4, 5/5) — VAT, products, sendToCustomer
- Supplier creation (4/4) — isSupplier=true, email in both fields
- Payment registration (2/2) — finds pre-existing invoice, uses actual amount
- Project creation (4/4) — resolves customer + PM
- Department creation (3/3) — parallel creation with unique numbers
- Product creation (5/5) — VAT auto-resolved
- Employee creation (7/7) — with employment record

**What's improving:**
- Employment contract PDFs: 4/10 → 9/10 (W9 workflow + B9 dept fix + B10 timeout)
- Supplier invoices: 0/4 → 1/6 (W6 workflow, B11 VAT fix pending in v17)
- Credit notes: 1/5 → needs retest (B6 search-before-create)

**What still needs work:**
- Bank reconciliation (0/2) — W7 built but untested
- Receipt expenses (0/5) — W10 built + B13 fix, needs test
- Payroll — no workflow yet (W1)
- Project invoices — no workflow yet (W4)

## Workflows (17 total)

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
| 16 | reconcile_bank_statement | T3 | CSV parser, invoice matching, bulk payments |
| 17 | register_expense | T3 | Receipt → voucher with department + input VAT |

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

## Remaining Work

**Untested (v17):** supplier invoice, receipt expense, bank reconciliation, credit note retest
**Missing workflows:** W1 (payroll), W4 (project invoice)
**Nice to have:** 14h (trim workflow results), W5 (custom dimensions workflow)

## Tracking Files
- `docs/tasks.csv` — 30+ competition prompts with scores, versions, analysis
- `docs/add_workflows.md` — workflow backlog with detailed failure analysis
- `docs/bugs_backlog.md` — bug details from competition logs

## Notes
- Competition: March 19-22, 2026 (ends tomorrow!)
- 56 variants per task (7 languages × 8 data sets)
- Rate limit: 10 submissions per task per day
- Cloud Run: project ainm26osl-722, concurrency=1
- Model: gemini-3.1-pro-preview (global location)
- Deploy: `bash scripts/deploy.bash v17`
- Logs: `bash scripts/get_prod_logs.bash v17`
