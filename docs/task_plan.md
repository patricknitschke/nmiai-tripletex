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

**26 workflows** covering T1/T2/T3 tasks. See `docs/add_workflows.md` for backlog.

**Key design principles:**
- Chief plans fast (no files), Senior executes with full context
- Search before create — some tasks have pre-existing data
- Self-contained workflows — handle prerequisites internally
- All name searches use count=10 + exact match (no partial match bugs)
- 100s deadline with 20s buffer before 120s cloudflare timeout
- BETA endpoints blocked, lookup_api flags them
- **Efficiency: batch APIs** — POST /project/list for multi-project, /resultbudget/company for aggregated data
- **Efficiency: cache IDs** — resolve entity once, reuse ID; trust 201 responses (no verify GETs)
- **Efficiency: embed sub-resources** — projectActivities in project creation payload

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
- Project invoices (W4) — **create_project_invoice workflow built (v34)**. **B45+B46 FIXED**: dateTo+1 day (exclusive param) + hourlyRate set via POST /project/hourlyRates (readOnly on entry). Both register_time and create_project_invoice auto-set rate.
- Ledger error correction (W11) — **analyze_ledger workflow built (P3)**, needs competition test. **B47 FIXED**: (1) dateTo exclusive +1 day, (2) compare_expenses invalid params removed (client-side filter), (3) pagination loops, (4) `amount` not `amountGross` for balance check, (5) duplicate detection drops abs() + adds description to key, (6) voucher_summaries only flagged vouchers. **compare_expenses workflow (v35)** uses /resultbudget/company for expense comparison (1 call vs N posting fetches)
- Monthly/yearly closing — **Chief bypass added (P2)**, Senior handles directly for closing tasks
- Custom dimensions — **create_dimension workflow built (v30)**, handles name + values in one call. Voucher dimension linking works via freeAccountingDimension1/2/3. **create_dimension_voucher combo (v34)** chains dimension creation + voucher posting in one atomic call

## Weakness Map by Competition Category

### Employees (T1 create, T3 employment contracts)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Create employee | T1 | `create_employee` | 7/7 | None |
| Set roles/admin | T1 | `create_employee` | — | Entitlement template = ALL_PRIVILEGES, works |
| Employment contracts (PDF) | T3 | `register_employment` | 9/10 | **B44 FIXED**: STYRK code lookup now uses count=25 + prefix matching + fallback search. Was count=1 → silent skip |
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
| Register payment | T2 | `register_payment` | 2/2 | **B38**: customer-filtered search can return 0 even when invoice exists (different customer record). Fixed: fallback broader search + amountExcludingVat matching |
| Credit notes | T2 | `create_credit_note` | 1/5 | VAT interpretation + search-before-create both improved but **never retested** |
| Supplier invoices | T3 | `create_supplier_invoice` | 5/6 | **B36 FIXED:** Was 1-posting (unbalanced). Now 2-posting: expense debit with vatType + AP 2400 credit with supplier ref. Same proven pattern as register_expense |
| Project invoices | T2-T3 | `create_project_invoice` | 0 | **B45+B46 FIXED**: (1) dateTo is exclusive in Tripletex API — was `today`, now `today+1` so entries created today are found. (2) hourlyRate is readOnly on TimesheetEntry — now sets rate via `POST /project/hourlyRates` (TYPE_FIXED_HOURLY_RATE) before fetching hours. Both `register_time` and `create_project_invoice` set rate when hourlyRate provided. |
| Reminder invoices | T2 | `find_overdue_invoices` + `send_reminder` + `register_payment` | 4/6 | **B52 FIXED**: (1) `send_reminder` workflow uses PUT /invoice/:createReminder with includeCharge=true — handles charge + accounting (debit 1500, credit 3400) + sending in 1 write call. (2) Replaces old 3-write path (manual voucher + order + order→invoice). (3) Fallback to POST /invoice with 0% VAT if createReminder fails. (4) Norwegian purregebyr is VAT-exempt — old path wrongly applied 25%. Optimal: 2 writes (createReminder + payment) vs old 4 writes |

### Travel Expenses (T2)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Create travel expense | T2 | `create_travel_expense` | 4/6 | **B21 FIXED:** Per diem was added as cost line, now uses `/travelExpense/perDiemCompensation` endpoint with rateType+rateCategory. **B43 FIXED:** (1) travelDetails with departure/return dates now set on POST /travelExpense (required before per diem). (2) Cost line success detection fixed — checks value.url OR value.id (API returns url not id on 201). (3) Removed read-only isPaidByEmployee from cost payload |
| Delete travel expense | T2 | `delete_travel_expense` | — | Built, never seen in competition |
| Receipt expenses | T3 | `register_expense` | 0/5 | **B37 FIXED:** (1) `search_pdf` tool for targeted PDF extraction via Flash (no raw PDF in Senior context). (2) Norwegian VAT rate table in prompt (12% transport, 15% food, 25% general). (3) Multi-item split: call register_expense per line item. Needs retest |

### Projects (T2-T3)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Create project | T2 | `create_project` | 4/4 | None — now embeds activities + skips verify GET |
| Batch projects | T2-T3 | `create_projects_batch` | — | **NEW v35**: POST /project/list, resolves PM once, embeds activities. 21 calls → 1 call |
| Full project lifecycle | T3 | Multi-workflow | 6/7 (v21), 2/7 (v28 pre-fix) | **v28 FIXED:** B19 timesheet date floor + PM email. v21 scored 6/7 (only supplier invoice failed). v28 pre-fix regressed due to new date bug — now fixed with clamp + prompt |

### Corrections (T2-T3)
| Task | Tier | Workflow | Best Score | Weakness |
|---|---|---|---|---|
| Credit notes (reversal) | T2 | `create_credit_note` | 1/5 | See Invoicing |
| Delete entries | T2 | `delete_travel_expense` | — | Only travel deletion. No general delete workflow |
| Ledger error correction | T3 | `analyze_ledger` + `create_voucher` | 3/4 | **B34+B35 FIXED:** analyze_ledger now returns account numbers (was null). create_voucher no longer drops 2710 for pure corrections. Senior prompt has VAT correction guidance (reverse + register_expense) |
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
| Bank fees/interest | T3 | `reconcile_bank_statement` | **needs test** | **B45-B51 FIXED:** Interest income (8040) vs expense (8150) now differentiated. Fee/interest auto-vouchers working. |

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
| P4 | Credit note retest | 3 | 6-12 pts (T2×2) | ⏳ Resubmit | Hardened: removed fuzzy match + create-then-credit fallback, added creditNoteEmail/sendType, fixed id-in-params bug |

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
| 8 | create_credit_note | T2 | Requires explicit invoiceId/invoiceNumber, forwards creditNoteEmail+sendType |
| 9 | create_travel_expense | T2 | Travel + per diem + costs |
| 10 | delete_travel_expense | T2 | Delete by ID/employee/title |
| 11 | create_project | T2 | Project + customer/PM resolution |
| 12 | create_supplier_invoice | T3 | Voucher with debit/credit postings + input VAT |
| 13 | create_voucher | T3 | Manual journal entries + dimension support |
| 14 | register_time | T3 | Timesheet hours on project activity |
| 15 | register_employment | T3 | Full contract: employee + dept + employment + salary + hours |
| 16 | reconcile_bank_statement | T3 | CSV parser, invoice matching, bulk payments + **auto-creates supplier invoices** |
| 17 | register_expense | T3 | Receipt → voucher with department + input VAT. Senior uses `search_pdf` tool for targeted extraction |
| 18 | analyze_ledger | T3 | Fetch postings, detect errors (imbalance/duplicate/orphaned VAT) |
| 19 | create_dimension | T2 | Custom accounting dimension + values in one call |
| 20 | register_payroll | T3 | Employee salary + bonus via /salary/transaction |
| 21 | register_fx_payment | T3 | Foreign currency payment + exchange difference voucher |
| 22 | create_project_invoice | T2-T3 | Project invoicing: fixed-price % or time-based |
| 23 | create_dimension_voucher | T2 | Dimension creation + voucher posting in one call |
| 24 | find_overdue_invoices | T2 | Finds overdue invoices + returns customer info for downstream steps |

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
| B34 | analyze_ledger returns account: null for all postings | Ledger correction 3/4 — LLM can't match errors to specific accounts, flies blind. API returns {id,url} not {number,name} for account references. | ✅ FIXED — Added `fields=*,account(*)` to GET /ledger/posting to expand account objects with number+name |
| B35 | create_voucher drops 2710 postings for pure correction vouchers | Ledger correction — posting 2710↔1920 loses 2710 line → unbalanced → 422. _resolve_postings unconditionally drops all system accounts. | ✅ FIXED — Only drop system accounts (2710/2400) when expense account (4xxx-7xxx) is present. Pure balance-sheet corrections keep all postings. Senior prompt now guides VAT corrections through register_expense |
| B25 | Manual 3-posting split WITH correct vatType STILL fails systemgenererte | create_supplier_invoice 0/0, create_voucher 0/0 — posting to 2710 (VAT account) IS the system-generated posting Tripletex auto-creates. Row 0 rejected. 2400 (supplier ledger) also system-managed. | ✅ FIXED B25v2 — Supplier invoice: 1 posting with `amountGross` + real `vatType` (25% input) + `supplier`. Tripletex auto-generates 2710+2400. Expense: 2 postings (expense amountGross+vatType, bank negative). `_resolve_postings` drops LLM-generated 2710/2400 postings. |
| B26 | B25v2 single posting still fails: guiRow 0 reserved for system-generated | Correct payload (1 posting + amountGross + vatType + supplier) rejected with same "rad 0 systemgenererte" error | ✅ FIXED — Add `"row": 1` to all user-created postings. Row 0 is reserved for Tripletex's auto-generated counterpart lines (2400 credit, MVA). |
| B36 | create_supplier_invoice unbalanced (1 posting) + fx_payment missing customer on AR + wrong amount field | Supplier invoice 422 "sum not zero" — only sends expense debit, no AP credit. FX payment uses `amount` instead of `amountGross` + missing `customer.id` on 1500 posting. | ✅ FIXED — (1) create_supplier_invoice now 2-posting: expense debit with vatType + AP 2400 credit with supplier ref (same pattern as register_expense). (2) fx_payment uses `amountGross` + attaches customer from invoice to 1500 posting. (3) `_resolve_postings` propagates customerId/supplierId from posting data to resolved postings. |
| B37 | Receipt expense: wrong VAT rate (25% for flights) + multi-item receipt lumped into single posting | 0/5 — Flight tickets are 12% MVA (persontransport lav sats), not 25%. Receipt also had office supplies needing separate account/rate. LLM had no VAT category guidance and no way to ask targeted questions about PDFs | ✅ FIXED — (1) `search_pdf` tool added: Senior uses Flash to ask targeted questions about PDF content instead of getting raw PDF attached. Forces structured extraction. (2) Norwegian VAT rate table added to Senior prompt (25%/15%/12%/0% with categories). (3) Multi-item receipt instructions: call register_expense once per line item. |
| B39 | FX invoice created in NOK not foreign currency | 2/4 — `register_fx_payment` pre-converted 6893 EUR × 10.37 = 71480.41 NOK and created order without currency. Tripletex thinks it's domestic invoice (currency id=1 = NOK). | ✅ FIXED — Now creates order with `currency: {id: EUR_id}` and uses foreign amount (6893) as line price. Added `_lookup_currency_id()` helper. |
| B40 | FX invoice not settled (amountOutstanding ≠ 0) | 2/4 — Payment registered 68033.91 NOK but invoice was 71480.41 NOK → amountOutstanding = 3446.50. Disagio voucher hit GL but didn't close the invoice. | ✅ FIXED — Payment now passes both `paidAmount` (68033.91 NOK received) AND `paidAmountCurrency` (6893 EUR = full foreign amount). Invoice fully settled at 0 outstanding. |
| B41 | Chief fabricates customers + create_voucher 422 "Kunde mangler" on AR postings | Overdue invoice task: (1) Chief invented "Musterkunde GmbH" instead of finding real customer. (2) Voucher 422 because AR account 1500 requires customer reference. (3) Double-booking: invoice + manual voucher for same 70 NOK. (4) Wrong execution order: payment before finding invoice. | ✅ FIXED — (1) `find_overdue_invoices` workflow searches real invoices. (2) `create_voucher` auto-attaches customer to AR postings (1500-1599) via `customerName`/`customerId` fields. (3) Chief prompt: explicit 4-step order for overdue tasks + NEVER fabricate names. (4) Senior prompt: overdue invoice guidance. (5) Schema: `customerName`/`customerId` added to `create_voucher`. |
| B42 | create_supplier_invoice expense posting missing project reference | Project lifecycle: 71800 kr supplier cost on account 6300 not linked to project "Dataplattform Brattli" — cost invisible in project reports/economy. | ✅ FIXED — Added `projectId` param to schema + workflow. Expense posting (row 1) now includes `project: {id}` when projectId is provided. |
| B44 | STYRK occupation code lookup too narrow (count=1) | Employment 9/10 — STYRK 3323 returned 0 results with count=1 (API uses substring match, ordering may not surface exact match). Field omitted silently instead of retried. | ✅ FIXED — `_resolve_occupation_code()` helper: count=25, prefers exact/prefix match, falls back to shorter prefix search (first 2 digits, count=50). Same fix in payroll.py. |
| B45 | Interest expense posted with wrong accounts (income accounts used) | Bank recon interest expenses Debit 1920/Credit 8040 (income). Should be Debit 8150 (expense)/Credit 1920 (bank). `_classify` returned generic "interest" without direction. | ✅ FIXED — `_classify` now returns `interest_income` or `interest_expense` based on amount direction. `_post_fee_or_interest_voucher` routes to correct accounts (8150 for expense, 8040 for income). |
| B46 | Redundant path param in query params | `_pay_customer_invoice` sent `id` as query param (already in URL path). `_pay_supplier_invoice` sent `invoiceId`. Not harmful but misleading. | ✅ FIXED — Removed redundant query params. |
| B47 | Hardcoded 25% VAT in supplier fallback voucher | `_post_supplier_bank_payment` always assumed 25% VAT. Many transactions have 0/12/15% VAT → wrong accounting entries. | ✅ FIXED — `_detect_vat_rate()` infers VAT from description keywords (transport=12%, food=15%, default=25%). |
| B48 | Hardcoded expense account 7300 for all supplier payments | Every fallback voucher debited 7300 (external services). Supplier expenses span 4000-7000 series. | ✅ FIXED — `_detect_expense_account()` infers account from description (rent→6300, IT→6540, travel→7140, goods→4300, default→7300). |
| B49 | Customer invoice matching Pass 3 (exact amount, any customer) too loose | Common amounts match wrong customer's invoice before name-only check runs. | ✅ FIXED — Reordered: name+partial (Pass 3) now runs before amount-only (Pass 4). Removed Pass 5 (any invoice with enough outstanding). |
| B50 | FX payment: 8060 used for both disagio and agio + no API rate lookup + wasteful currency fallback | (1) Agio (gain) posted to 8060 Valutatap instead of 8160 Valutagevinst — non-standard reporting. (2) Manual rates required even though Tripletex has exchange rate API. (3) Currency lookup did a full scan fallback (100 currencies) when code filter returned nothing — wastes API call with no benefit. | ✅ FIXED — (1) Agio now posts to 8160 Valutagevinst, disagio stays on 8060 Valutatap. (2) `_get_exchange_rate_nok()` helper uses `GET /currency/{id}/exchangeRate` for official Norges Bank rates when rates aren't provided. (3) Removed currency fallback scan. Schema: invoiceRate/paymentRate now optional, added invoiceDate field. |
| B50 | No pagination — max 1000 invoices | Companies with >1000 invoices silently get truncated data. | ✅ FIXED — `_fetch_all_pages()` helper paginates through all results. |
| B51 | Interest amount picks wrong field (amount_out for income) | `amount = amount_out if amount_out > 0 else amount_in` picks wrong value if both set for interest income. | ✅ FIXED — Explicit branching: `interest_income` uses `amount_in`, `interest_expense` uses `amount_out`. |
| B53 | create_dimension_voucher passes date=None to create_voucher | Voucher 422 "Kan ikke være null" — extra write + error when user doesn't specify date. 5 writes instead of optimal 4. | ✅ FIXED — `create_dimension_voucher` defaults voucherDate to `date.today().isoformat()` when not provided. Also propagates date into voucher_data dict so `create_voucher` sees it. |

## Day 3 Evening — Priority Action Queue (March 21)

**v28 deployed with B19+B20 fixes (timesheet date clamp + PM resolution). All P1-P4 ready.**

| # | Action | Expected Points | Effort | Status |
|---|--------|----------------|--------|--------|
| 1 | **Resubmit project lifecycle** | up to 6 pts (T3×3) | Zero code changes | ⏳ RESUBMIT — B19+B20 fix timesheet dates + PM. v21 scored 6/7, v28 should match or beat |
| 2 | **Resubmit credit notes (P4)** | 6-12 pts (T2×2, 3 tasks) | Zero code changes | ⏳ RESUBMIT — VAT + search fixes in v18+ |
| 3 | **Resubmit bank reconciliation** | 3-6 pts (supplier payments) | Zero code changes | ⏳ RESUBMIT — P1 + B18 fix now in v27+ |
| 4 | **Resubmit supplier invoices** | up to 6 pts (T3) | Zero code changes | ⏳ RESUBMIT — B18 voucherType fix in v27+ |
| 5 | **Test monthly closing (P2)** | 6-10 pts (T3) | 1 submission | ⏳ RESUBMIT — Chief bypass + separate vouchers |
| 6 | **Resubmit ledger correction (P3)** | Up to 6 pts (T3) | 1 submission | ⏳ TESTED 3/4 — B34+B35 fixes deployed. Account extraction + 2710 handling + VAT correction prompt. Expect 4/4 |
| 7 | **Resubmit employee+startDate tasks** | 2-4 pts | Zero code changes | ⏳ RESUBMIT — B16 fix in v27+ |

**All code fixes are deployed. The points are on the table — just need resubmissions.**

**Tasks NOT worth fixing (low ROI):**
- Payroll (W1): **FIXED** — register_payroll workflow built after 4/4 fail on March 21. Now seen multiple times.
- Forex disagio: **B39+B40+B47 FIXED** — invoice in foreign currency + paidAmountCurrency settles fully + B47: (1) agio now uses 8160 Valutagevinst (was 8060 for both), (2) exchange rate API auto-lookup when rates omitted, (3) removed wasteful currency fallback scan

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
