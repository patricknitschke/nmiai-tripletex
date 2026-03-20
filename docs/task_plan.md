# Task Plan: NMiAI Tripletex AI Accounting Agent

## Goal
Build a Python AI agent (FastAPI + Claude/Gemini) that receives accounting task prompts via `/solve`, interprets them with an LLM to extract task type + structured data, then executes pre-built workflows with minimal API calls. Deploy to GCP Cloud Run for the NM i AI competition (March 19-22, 2026).

## Architecture (v3 — Chief Accountant Multi-Agent)
```
POST /solve
  → Chief Accountant THINKS (reasoning about dependencies, empty environment, approach)
  → Chief produces plan: {thinking, steps[{task, suggested_workflow}]}
  → Chief's thinking + plan stored as persistent "chief_memory"
  → For each step:
      → Sub-agent gets: task description + workflow field spec + prior context
      → Sub-agent has tools:
          - ask_chief(question) → Chief re-reads original prompt + remembers plan + prior Q&A
          - execute_workflow(name, data) → calls pre-built workflow functions
          - tripletex_get/post/put/delete → raw API fallback
      → Sub-agent pulls data from Chief as needed, then executes
      → Conversation log persisted per step (Chief remembers all Q&A)
  → Between steps: Chief reviews progress, can adjust remaining steps
```

**Key design principles:**
- Chief is the "solution architect" — thinks through dependencies and approach BEFORE delegating
- Chief has persistent memory: its thinking + plan + per-step conversation logs
- Sub-agents PULL data from Chief (ask_chief) rather than Chief PUSHING all data upfront
- Sub-agents see exact workflow field specs — no guessing field names
- Fresh empty environment is a first-class concept — Chief knows everything must be created
- Each sub-agent session is independent with its own reasoning loop

## Current Phase
Phase 9 — Test + Deploy Multi-Agent Orchestrator

## Phases

### Phase 1–4: COMPLETE
Scaffolding, infrastructure, LLM interpreter, API research, workflow builds.

### Phase 5: Schema-Driven Interpreter + Workflow Audit — COMPLETE
- [x] 5a: Schema registry (schemas.py) with all 11 task types from OpenAPI spec
- [x] 5b: 2-call interpreter (classify → schema-guided extract)
- [x] 5c: All workflows audited against OpenAPI spec
- [x] 5d: Sandbox testing — 5/7 pass clean, 2 fail on sandbox config

### Phase 6: Deploy to Cloud Run — COMPLETE
- [x] Dockerfile + .dockerignore
- [x] Deployed to europe-north1, Vertex AI calls to us-central1
- [x] Service URL: https://pining-for-the-woods-tripletex-v01-370009516620.europe-north1.run.app
- [x] Submitted to app.ainm.no — first tasks received!
- [x] Supplier task handled by fallback (classified "unknown")
- [x] Fixed: classifier now maps supplier → create_customer

### Phase 7: Fix Workflows from Competition Feedback — COMPLETE
Real competition tasks revealed critical gaps. All fixed in code:

- [x] **7a: Bank account** — `_ensure_bank_account()` in invoice.py
- [x] **7b: Product numbers + VAT** — `_build_order_lines()` creates products, resolves VAT by rate
- [x] **7c: Named employees** — `_resolve_employee_id()` creates employee from name/email
- [x] **7d: Per diem** — extracted separately in schema, added as cost line (not dedicated endpoint yet)
- [x] **7e: Schemas updated** — all 11 schemas now expose every field workflows actually read

### Phase 8: Multi-Agent Chief Accountant — COMPLETE
Redesigned orchestrator from single-agent loop to true multi-agent with memory.

- [x] **8a: Chief as solution architect** — thinks through dependencies, produces {thinking, steps} plan
- [x] **8b: Sub-agent with ask_chief tool** — pulls data from Chief on demand, has workflow field specs
- [x] **8c: Persistent Chief memory** — thinking + plan stored as `chief_memory`, included in every Chief answer call
- [x] **8d: Per-step conversation logs** — each Chief ↔ sub-agent pair maintains Q&A history
- [x] **8e: Fresh environment awareness** — all prompts know the account starts empty
- [x] **8f: Workflow field specs in sub-agent prompt** — sub-agent sees exact field names, no guessing
- [x] **8g: Schemas audit** — all 11 schemas updated to expose every field workflows actually read

### Phase 9: Test + Deploy Multi-Agent — IN PROGRESS
- [x] **9a: Local testing** — supplier, employee, travel expense all working
- [x] **9b: Deploy v02 to Cloud Run** — deployed and receiving competition tasks
- [x] **9c: Fix Vertex crash** — NoneType on empty/blocked Gemini response → added None guard in client.py
- [x] **9d: Fix step result passing** — Steps couldn't see prior step results (IDs, invoice numbers)
  - Sub-agent now returns `workflow_result` with key fields (id, invoiceNumber, etc.)
  - Orchestrator stores `key_results` in `completed_steps` and includes in `prior_context`
  - Chief memory also updated after each step so it can answer questions about prior results
- [x] **9e: Fix customer name merge** — `_ensure_customer` now merges `customerName` into customer object
  - Previously: `{"customer": {"organizationNumber": "..."}}` → 422 (name required)
  - Now: falls back to `customerName` field if customer object has no name
- [x] **9f: Fix fallback behavior** — Sub-agent now respects `suggested_workflow: "fallback"`
  - When fallback: sub-agent sees "No workflow. Use raw API tools. Do NOT call execute_workflow."
  - Previously: sub-agent ignored fallback and tried execute_workflow anyway
- [ ] **9g: Fix 422 error detection** — BUG: workflow returns raw 422 JSON without `"error"` key,
  sub-agent logs it as "OK". `has_error` check needs to also detect `"status": 4xx` in response.
- [ ] **9h: Improve Chief planning for implicit prerequisites** — Chief doesn't infer that
  "register payment on invoice" in an empty account means "create invoice first".
  Needs stronger reasoning prompt about creating prerequisites for referenced resources.
- [ ] **9i: Add supplier invoice workflow** — competition sent French supplier invoice, we have no workflow
  - Option A: `POST /ledger/voucher` with manual postings (debit 6500 + input VAT, credit 2400)
  - Option B: Rely on fallback sub-agent with raw API tools (now fixed, won't crash)
  - Need to research `POST /ledger/voucher` body schema + posting format
- [ ] **9j: Redeploy + retest** — deploy with all fixes, test with Spanish invoice+payment prompt
- [ ] **9i: Monitor competition scores**

### Phase 10: API Knowledge Tool (OpenAPI Lookup)
Build FIRST — gives the generic sub-agent an immediate boost, and every specialist
agent built in Phase 11 inherits it as a failsafe.

**Problem this solves (with real evidence):**
- Elvdal AS: sub-agent wasted 5 iterations guessing invoice field names
  (status, isPaid, openAmount, totalAmount — none valid). A single `lookup_api("GET /invoice fields")`
  would have returned the valid fields instantly.
- Chief answered with wrong field names (`customer_name` vs `name`). If it could
  query the spec, it would give correct names every time.
- Entitlement template bug: hardcoded `"administrator"` instead of `"ALL_PRIVILEGES"`.
- Supplier invoice: sub-agent didn't know which endpoint to use for purchase invoices.

**Approach: Plain tool first, MCP later if needed.**
Simple Python module + agent tool (works with any LLM, no protocol overhead).
Later, wrap as MCP server if we need interoperability.

**Agent tool:**
```
lookup_api(query)  → searches OpenAPI spec, returns endpoint details, enums, required fields
```

**Implementation:**
- [ ] **10a: Build lookup module** — `src/agent/api_spec.py`, wraps OpenAPI JSON
  - Functions: search_endpoints(keyword), get_endpoint_schema(path, method), get_enum_values(path, field)
- [ ] **10b: Expose as agent tool** — add `lookup_api` to sub-agent tool list
- [ ] **10c: Error recovery pattern** — on 4xx, agent queries the spec before retrying
- [ ] **10d (future): MCP server** — if we need interoperability, wrap with `mcp` Python SDK

**File structure:**
```
src/agent/
  api_spec.py              # OpenAPI lookup module (plain Python)
```

### Phase 11: Specialist Domain Agents
Build AFTER Phase 10 — each specialist inherits the `lookup_api` tool as failsafe.
Build incrementally: start with Invoice Specialist (highest value), add others one by one.

**Evidence from competition that specialists would help:**
- Elvdal AS: Chief didn't know "register payment" in empty account = create invoice first.
  An Invoice Specialist would know this pattern immediately.
- French supplier invoice: Sub-agent tried `create_invoice` (sales) for a purchase invoice.
  An AP Specialist would know to use `POST /ledger/voucher` instead.
- Chief gave wrong field names. Specialists know their own workflow field names perfectly.

**Architecture vision:**
```
Chief reads prompt → identifies domain(s) → consults specialists → designs plan
  ↓
Each specialist has: domain knowledge + ask_chief + execute_workflow + raw API + lookup_api
  ↓
Invoice Specialist: orders, products, VAT, bank accounts, payment registration
AP Specialist: supplier invoices, vouchers, purchase ledger
Employee Specialist: employee creation, roles, entitlements, departments
Travel Specialist: travel expenses, cost lines, per diem
General Specialist: customers, suppliers, departments, products (simple CRUD)
```

**Implementation (incremental):**
- [ ] **11a: Invoice Specialist** — highest value, handles invoice + payment + credit note
- [ ] **11b: AP Specialist** — supplier invoices via POST /ledger/voucher
- [ ] **11c: Employee Specialist** — employee + entitlements + departments
- [ ] **11d: Travel Specialist** — travel expenses + per diem
- [ ] **11e: Chief delegates to specialists** — route to right specialist based on plan
- [ ] **11f: Chief consults before planning** — ask specialists "can you handle this?"

**File structure:**
```
src/agent/agents/
  chief.py                    # Chief: plan, consult, review
  sub_agent.py                # Generic sub-agent (still used as default)
  specialists/
    __init__.py               # Registry: domain → specialist
    invoice.py                # Invoice + order + payment + credit note
    ap.py                     # Accounts Payable (supplier invoices, vouchers)
    employee.py               # Employee + department
    travel.py                 # Travel expense + per diem
    general.py                # Customer, supplier, product (simple CRUD)
```

### Phase 12: Tier 3 Workflows + Iteration
- [ ] Complex scenarios (opens Saturday — wait and assess complexity)
- [ ] Iterate based on leaderboard scores

## Key Decisions
| Decision | Rationale |
|----------|-----------|
| Python + FastAPI | User preference, async, fast to build |
| Schema-driven workflows | Field names/types from OpenAPI spec, not LLM guessing |
| Pre-built workflows per task type | Exact minimum API calls, max efficiency bonus |
| Vertex AI (Gemini 2.5 Flash) | Free via GCP, fast, good multilingual. Project: ainm26osl-722, location: us-central1 |
| Supplier = create_customer with isSupplier | No separate workflow needed, just a classifier mapping |
| **Multi-agent over single-agent** | Chief plans in own context, sub-agents execute in own context. Chief can "translate" prompts and adapt on failure. Sub-agents can reason about errors independently. |
| **Chief uses complete(), sub-agents use tool_use_loop()** | Chief only needs to produce a plan (text→JSON). Sub-agents need to call tools iteratively. Different LLM primitives for different roles. |
| **Sub-agents get clear English instructions, not raw prompts** | Chief translates multilingual prompts into precise task descriptions. Sub-agents don't need to parse Norwegian/German/etc. |
| **ask_chief tool for dynamic data pulling** | Sub-agents pull exactly what they need, when they need it. Chief doesn't need to extract all fields upfront. More robust than one-shot extraction. |
| **Chief has persistent memory (thinking + plan + Q&A)** | Chief's reasoning persists across all sub-agent interactions. No amnesia between questions. Consistent guidance throughout. |
| **Fresh empty environment as first-class concept** | All agent prompts explicitly know the account starts empty. "Not found" means "create it", not "error". |
| **Specialist domain agents (planned)** | Each accounting domain gets its own specialist agent with deep knowledge. Chief consults before planning, delegates to the right specialist. More robust than one generic sub-agent. |

## Errors from Competition (Phase 7)
| Task | Error | Root Cause | Fix |
|------|-------|------------|-----|
| Supplier (T1) | Classified as "unknown" → fallback | Classifier didn't know "leverandør" | **Fixed** — added mapping in classifier |
| Invoice (T1) | 422 "bankkontonummer mangler" | Fresh account has no bank account | **7a** — register bank account first |
| Invoice (T1) | Wrong product data | Product numbers + VAT rates not extracted | **7b** — extract and create products |
| Travel expense (T2) | Wrong employee | Named employee ignored, used first available | **7c** — create employee from prompt |
| Travel expense (T2) | Per diem as cost line | Tagegeld should use perDiemCompensations | **7d** — separate per diem handling |

## Notes
- Competition is LIVE (March 19-22, 2026)
- Tier 3 tasks open early Saturday
- 56 variants per task (7 languages × 8 data sets)
- Rate limit: 10 submissions per task per day
- Fresh Tripletex account per competition submission
- Deployed URL: https://pining-for-the-woods-tripletex-v01-370009516620.europe-north1.run.app
- Cloud Run project: ainm26osl-722
