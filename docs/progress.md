# Progress Log

## Session: 2026-03-20

### Phase 1: Project Scaffolding — COMPLETE
- pyproject.toml, Dockerfile, .env.example, project structure
- venv created, all deps installed (Python 3.11)

### Phase 2: Core Infrastructure — COMPLETE
- models.py — Pydantic models for /solve payload
- server.py — FastAPI with /solve + /health, error handling, comprehensive logging
- tripletex.py — async httpx client, Basic Auth (0:token), call/error counting

### Phase 3: LLM + Interpreter + Router + Fallback — COMPLETE
- llm.py — abstraction for Anthropic + Vertex AI (Gemini), supports both completion and tool-use loops
- interpreter.py — single LLM call extracts {task_type, data} from prompt + files
- router.py — dispatches to workflow or fallback agent
- fallback.py — generic agent loop with 4 Tripletex tools (get/post/put/delete)
- Draft workflows created: employee, customer, department, product, invoice (NOT validated against real API)

### LLM Provider Setup — COMPLETE (with issues)
- Vertex AI (Gemini 2.5 Flash) working via `us-central1`
- Required: `gcloud auth application-default login`, billing enabled, aiplatform API enabled
- europe-north1 does NOT have Gemini models — must use us-central1

### End-to-End Test Results
| Test | Result | Notes |
|------|--------|-------|
| Health endpoint | PASS | GET /health → {"status": "ok"} |
| Interpreter (Norwegian prompt) | PASS | Correctly extracted: create_employee, {firstName: Ola, lastName: Nordmann, email, role: administrator} |
| Router dispatch | PASS | Routed to create_employee workflow |
| Employee creation | PARTIAL | 201 Created, but role assignment failed (userType "ADMINISTRATOR" not valid) |
| Employee with department | PASS | POST /employee with userType: STANDARD + department.id → 201 |

### Phase 4: API Research — COMPLETE (research portion)
- Downloaded tripletex_openapi.json (3.7MB)
- Built scripts/parse_openapi.py to extract endpoint schemas
- Parsed ALL key endpoints: employee, customer, department, product, order, invoice, travelExpense, project, entitlements
- Compiled distilled API reference in findings.md with exact payloads + required fields
- Key discoveries:
  - Admin roles: via `PUT /employee/entitlement/:grantEntitlementsByTemplate` (not userType)
  - Invoice: can include orderLines inline OR reference existing orders
  - Invoice payment: `PUT /invoice/{id}/:payment` with paymentDate, paymentTypeId, paidAmount (all query params)
  - Credit note: `PUT /invoice/{id}/:createCreditNote` with date param
  - Travel expense: POST /travelExpense (header) + POST /travelExpense/cost (line items)
  - Project: needs name + projectManager ref
  - Lookup endpoints identified for all dependent IDs (dept, vatType, paymentType)
- NEXT: Test workflows against sandbox, then deploy

### Phase 4b: Workflow Rebuild — COMPLETE
All 11 workflows rebuilt with correct API contracts:
- T1 (6): employee, customer, department, product, order, invoice
- T2 (5): payment, credit_note, travel_expense, delete_travel_expense, project
- Key fixes: invoice uses PUT /order/{id}/:invoice, employee uses entitlement API for admin, dept requires departmentNumber
- TripletexClient.put() now supports query params
- Router updated: 11 workflows, all imports verified
- Interpreter prompt updated with exact field names

### Files Created/Modified
- src/agent/workflows/{employee,customer,department,product,invoice}.py — rebuilt
- src/agent/workflows/{payment,credit_note,travel_expense,project}.py — new T2 workflows
- src/agent/{router,interpreter,tripletex}.py — updated
- docs/{findings.md, api_reference.json} — API research
- .gitignore — updated

### Phase 5-7: Schema-Driven Interpreter + Competition Fixes — COMPLETE
- Schema registry with all 11 task types
- 2-call interpreter (classify → schema-guided extract)
- Deployed to Cloud Run, first competition submissions
- Fixed: bank account registration, product numbers, VAT types, named employees, per diem

### Phase 8: Multi-Agent Chief Accountant — COMPLETE
**Session: 2026-03-20 (continued)**

**v1 → v2 → v3 evolution:**
1. v1: Single agent loop (Chief calls tools directly) — too flat, no planning
2. v2: Chief plans JSON → sub-agents execute → but sub-agents used wrong field names, no memory
3. v3 (current): Full multi-agent with memory and thinking

**v3 features implemented:**
- Chief as "solution architect" — has a thinking step to reason about dependencies and approach
- `ask_chief` tool — sub-agents dynamically pull data from Chief instead of Chief pushing everything
- Persistent Chief memory — thinking + plan stored, included in every Chief answer call
- Per-step conversation logs — Chief remembers all prior Q&A with each sub-agent
- Workflow field specs in sub-agent prompt — exact field names, no guessing
- Fresh empty environment awareness — all prompts know account starts empty
- All 11 schemas audited and updated to match what workflows actually read

**First test results (invoice prompt):**
- Sub-agent used wrong field names (`customer_id` vs `customerId`) → fixed by adding workflow spec
- Sub-agent made too many ask_chief calls → fixed by "ask ONE comprehensive question" rule
- Chief had no memory between questions → fixed with conversation_log + chief_memory
- Chief didn't know environment was empty → fixed with explicit "fresh environment" in all prompts

**Codebase refactored:**
```
src/agent/
  server.py, orchestrator.py, models.py, tripletex.py, utils.py
  agents/ → chief.py, sub_agent.py
  llm/ → client.py
  workflows/ → __init__.py (WORKFLOWS dict), schemas.py, 9 workflow files
DELETED: interpreter.py, router.py, fallback.py (all dead code)
```

**Competition deployment (v02-v03):**
- Deployed to Cloud Run, receiving real competition tasks
- Supplier prompt (Norwegian): 1 API call, 0 errors, 8.3s — perfect
- Employee prompt: 3 API calls, 1 error (entitlement template wrong) → fixed ALL_PRIVILEGES
- Travel expense (German): 9 API calls, 1 error (duplicate employee in sandbox) — would be clean in competition

**Competition bugs found + fixed (v04):**
- P1 (CRITICAL): Steps couldn't pass results — Step 2 sub-agent had no invoice ID from Step 1
  → Fixed: `workflow_result` with key fields, `prior_context` includes actual IDs, chief_memory updated
- P2: Customer name not merged — `_ensure_customer` ignored `customerName` when customer object present
  → Fixed: merges `customerName` into customer object as fallback
- P3: Sub-agent ignored "fallback" — tried `execute_workflow` even when Chief said use fallback
  → Fixed: fallback system prompt says "Do NOT call execute_workflow, use raw API tools"
- P4: Vertex NoneType crash — Gemini returned empty response parts
  → Fixed: None guard in client.py

### Phase 9 continued: Senior mode + bug fixes — MOSTLY COMPLETE
**Session: 2026-03-20 (evening)**

**Senior Accountant mode deployed as default:**
- Built single-agent fast path (run_senior_accountant) — 20.2s vs 88.8s for Chief mode on same prompt
- AGENT_MODE env var: "senior" (default) or "chief"
- Senior uses: execute_workflow + lookup_api + raw API tools, 15 max iterations, 100s deadline

**Competition bug fixes deployed:**
- 100s deadline with 20s buffer before 120s cloudflare timeout
- BETA endpoint audit: only entitlement was BETA → removed entirely
- Employee search-before-create by email (handles pre-existing resources)
- Project startDate defaults to today
- VAT cache resets per client instance
- Gemini 3.x location routing: auto-routes to "global" location

**Phase 10: API Knowledge Tool — COMPLETE:**
- Built src/agent/api_spec.py: search_endpoints, get_endpoint, find_enum, lookup
- Exposed as lookup_api tool for Senior + Sub-agent
- BETA endpoints flagged with warnings
- Sub-agent prompt: "use lookup_api BEFORE retrying on 4xx"

**Model comparison testing (same Nynorsk invoice+payment prompt):**
| Model | Time | Iterations | Errors | Invoice Total | Score |
|-------|------|-----------|--------|---------------|-------|
| gemini-2.5-flash | 42.1s | 3 | 0 | 61,312.50 (with 25% VAT) | 0/8 |
| gemini-2.5-pro | 18.6s | 3 | 0 | 49,050 (no VAT added) | 0/8 |
| gemini-3.1-pro-preview | 42.0s | 4 | 2 (dup products) | 49,050 (no VAT added) | 0/8 |

All models score 0/8 — the issue is price/VAT interpretation ("til 25500 kr"), not model capability.
Gemini 3.x models need "global" location (not us-central1).

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | Phase 10 complete. Model testing + competition monitoring. |
| Where am I going? | Resolve VAT interpretation issue, decide model, Phase 11 (hybrid mode) |
| What's the goal? | Score >0 on the invoice+payment prompts. Deploy best model for competition. |
| What have I learned? | All models interpret "til X kr" as excl-VAT. This is likely wrong for competition scoring. VAT handling is the #1 blocker. |
| What have I done? | API knowledge tool, Senior mode, all competition bug fixes, model comparison across 3 Gemini variants. |

---
*Update after completing each phase or encountering errors*
