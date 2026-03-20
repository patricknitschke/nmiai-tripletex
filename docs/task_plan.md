# Task Plan: NMiAI Tripletex AI Accounting Agent

## Goal
Build a Python AI agent (FastAPI + Claude/Gemini) that receives accounting task prompts via `/solve`, interprets them with an LLM to extract task type + structured data, then executes pre-built workflows with minimal API calls. Deploy to GCP Cloud Run for the NM i AI competition (March 19-22, 2026).

## Architecture (v4 — Senior Accountant + modes)

**Default: Senior Accountant (AGENT_MODE=senior)**
```
POST /solve (100s deadline)
  → Senior Accountant (single agent loop)
    - Sees: full prompt + files + workflow catalog in system prompt
    - Tools: execute_workflow, lookup_api, tripletex_get/post/put/delete
    - Reads prompt, chains workflows directly, handles errors with lookup_api
    - Typically 3-6 iterations, 15-50s
```

**Alternative: Chief multi-agent (AGENT_MODE=chief)**
```
POST /solve
  → Chief plans → sub-agents execute per step → Chief reviews between steps
  → More overhead but better for complex multi-step coordination
```

**Planned: Hybrid mode (AGENT_MODE=hybrid)**
```
POST /solve
  → Chief THINKS + produces plan (1 LLM call)
  → Senior executes with plan as context preamble
  → Best of both: strategic planning + direct execution
```

**Key design principles:**
- Senior Accountant is the fast path — handles 80% of tasks directly
- Fresh empty environment is a first-class concept — all prompts know everything must be created
- lookup_api tool gives agents self-serve access to OpenAPI spec (field names, enums, endpoints)
- BETA endpoints flagged and blocked — only non-BETA endpoints used in workflows
- 100s deadline with 20s buffer before 120s cloudflare timeout
- Workflows search-before-create (e.g., employee by email) to handle pre-existing resources

## Current Phase
Phase 10 COMPLETE — API Knowledge Tool deployed. Model testing in progress. Senior mode is default for competition.

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

### Phase 9: Test + Deploy Multi-Agent — MOSTLY COMPLETE
- [x] **9a: Local testing** — supplier, employee, travel expense all working
- [x] **9b: Deploy v02 to Cloud Run** — deployed and receiving competition tasks
- [x] **9c: Fix Vertex crash** — NoneType on empty/blocked Gemini response → added None guard in client.py
- [x] **9d: Fix step result passing** — Sub-agent returns `workflow_result` with key fields, orchestrator passes via `prior_context`
- [x] **9e: Fix customer name merge** — `_ensure_customer` merges `customerName` into customer object
- [x] **9f: Fix fallback behavior** — fallback allows workflows for known sub-tasks + raw API for unknown
- [x] **9g: Fix 422 error detection** — checks `"status": 4xx` in addition to `"error"` key
- [ ] **9h: Improve Chief planning for implicit prerequisites** — deferred to hybrid mode (Phase 11)
- [x] **9i: Increase Chief plan max_tokens** — bumped to 4096
- [ ] **9j: Add supplier invoice workflow** — still needed
- [ ] **9k/9l: Agent QoL improvements** — lower priority while Senior mode is default
- [x] **9m: Senior Accountant mode** — single agent fast path, default for competition
- [x] **9n: 120s timeout handling** — 100s deadline, 20s buffer
- [x] **9o: BETA endpoint audit** — entitlement removed, lookup_api flags BETA
- [x] **9p: Employee search-before-create** — finds existing by email
- [x] **9q: Project startDate default** — defaults to today
- [x] **9r: VAT cache fix** — resets per client instance
- [x] **9s: Deployed Senior mode to competition**
- [ ] **9t: Monitor competition scores** — ongoing
- [x] **9u: Gemini 3.x location routing** — auto-routes to "global" for gemini-3.x models
- [ ] **9v: Model selection** — tested 3 models, all score 0/8 on Nynorsk invoice+payment (VAT interpretation issue)

### Phase 10: API Knowledge Tool (OpenAPI Lookup) — COMPLETE
- [x] **10a: Build lookup module** — `src/agent/api_spec.py` with search_endpoints, get_endpoint, find_enum, lookup
- [x] **10b: Expose as agent tool** — `lookup_api` added to Senior + Sub-agent tools
- [x] **10c: Error recovery pattern** — prompts say "use lookup_api BEFORE retrying on 4xx"
- [x] **10d: Chief auto-includes spec** — auto-looks up relevant endpoint when answering field questions
- [ ] **10e (future): MCP server** — wrap with `mcp` Python SDK if needed for interoperability

### Phase 11: Hybrid Mode — Chief Plans, Senior Executes
Combine the Chief's strategic planning with the Senior's direct execution.
Eliminates the communication overhead that killed Chief mode performance.

**Evidence from competition testing (same Spanish invoice+payment prompt):**

| | Senior mode | Chief mode |
|---|---|---|
| Time | **20.2s** | 88.8s |
| Iterations | **6** | 10 |
| API errors | **1** | 3 |
| Completed? | **Yes** | No — invoice failed 3x, payment never attempted |

Chief's planning was actually good (correct 2-step plan), but sub-agents wasted time
on communication, couldn't see each other's results, and got stuck on VAT errors.

**Architecture:**
```
POST /solve
  → Chief THINKS + produces plan (1 LLM call, ~3s)
  → Plan injected as preamble into Senior's system prompt
  → Senior executes with: original prompt + Chief's thinking + plan + all tools
  → Senior follows the plan but can deviate if needed
```

**Implementation:**
- [ ] **11a: New mode "hybrid"** in orchestrator.py — Chief plans, Senior executes with plan
- [ ] **11b: Inject Chief thinking into Senior prompt** — add plan as preamble section
- [ ] **11c: Update AGENT_MODE** — support "senior", "hybrid", "chief"
- [ ] **11d: Test + compare** — run same prompts across all 3 modes

**Config:** `AGENT_MODE=senior | hybrid | chief`

### Phase 12: Specialist Domain Agents
Build AFTER Phase 11 — each specialist inherits the `lookup_api` tool as failsafe.
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

### Phase 13: Tier 3 Workflows + Iteration
- [ ] Complex scenarios (opens Saturday — wait and assess complexity)
- [ ] Iterate based on leaderboard scores

## Key Decisions
| Decision | Rationale |
|----------|-----------|
| Python + FastAPI | User preference, async, fast to build |
| Schema-driven workflows | Field names/types from OpenAPI spec, not LLM guessing |
| Pre-built workflows per task type | Exact minimum API calls, max efficiency bonus |
| Vertex AI (Gemini) | Tested 2.5-flash (42s), 2.5-pro (19s), 3.1-pro-preview (42s). All score 0/8 on same prompt — issue is VAT interpretation, not model. 2.5-pro fastest. 3.x needs "global" location. |
| **Senior Accountant as default** | Single agent loop beats multi-agent for competition tasks. 20s vs 89s on same prompt. Direct tool access, no communication overhead. Chief mode available for complex tasks. |
| **lookup_api tool** | Agents self-serve from OpenAPI spec. Prevents field name guessing (saved 5+ iterations on Elvdal/Portuguese prompts). Flags BETA endpoints. |
| **100s deadline** | Cloudflare kills at 120s. 20s buffer ensures we always return "completed". |
| **Search-before-create pattern** | Competition accounts may have pre-existing resources. Employee workflow searches by email first. |
| **No BETA endpoints** | Competition blocks BETA endpoints with 403. All workflows audited, entitlement endpoint removed. |
| **Fresh empty environment as first-class concept** | All prompts know account starts empty. "Not found" = "create it". |
| **Hybrid mode (planned)** | Chief's strategic planning + Senior's direct execution. Best of both worlds. |
| **Specialist agents (planned)** | Domain experts for invoice, AP, employee, travel. Each knows their API patterns. |

## Errors from Competition (Phase 7)
| Task | Error | Root Cause | Fix |
|------|-------|------------|-----|
| Supplier (T1) | Classified as "unknown" → fallback | Classifier didn't know "leverandør" | **Fixed** — added mapping in classifier |
| Invoice (T1) | 422 "bankkontonummer mangler" | Fresh account has no bank account | **7a** — register bank account first |
| Invoice (T1) | Wrong product data | Product numbers + VAT rates not extracted | **7b** — extract and create products |
| Travel expense (T2) | Wrong employee | Named employee ignored, used first available | **7c** — create employee from prompt |
| Travel expense (T2) | Per diem as cost line | Tagegeld should use perDiemCompensations | **7d** — separate per diem handling |

## Known Weaknesses (as of Phase 10 complete, model testing in progress)

**Critical — affects scoring:**
1. **VAT interpretation ambiguity** — "til 25500 kr" in Nynorsk: all models interpret as excl-VAT
   prices, but competition may expect incl-VAT. Scored 0/8 across gemini-2.5-flash, 2.5-pro,
   3.1-pro-preview. This is a prompt interpretation issue, not model capability.
2. **Missing workflows** — supplier invoices (AP), time registration, project invoices.
   Senior handles via raw API + lookup_api, but no dedicated workflow.

**Medium — affects efficiency:**
3. **Workflow results flood context** — full API response JSON. Need concise summaries.
4. **No iteration budget awareness** — Senior should know "be decisive, ~15 iterations max".
5. **Model selection undecided** — 2.5-pro fastest (19s) but all models tie on accuracy.

**Low priority (Chief mode):**
6. Chief QoL improvements (9k/9l) — deferred while Senior is default.

## Notes
- Competition is LIVE (March 19-22, 2026)
- Tier 3 tasks open early Saturday
- 56 variants per task (7 languages × 8 data sets)
- Rate limit: 10 submissions per task per day
- Fresh Tripletex account per competition submission — session token single-use
- **120s cloudflare timeout** — must return before this or score = 0
- **BETA endpoints return 403** — do not use any [BETA] endpoint
- Cloud Run project: ainm26osl-722
- Default mode: AGENT_MODE=senior
