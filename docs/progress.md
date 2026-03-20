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

**Files modified:**
- `src/agent/orchestrator.py` — full rewrite (3 iterations)
- `src/agent/server.py` — updated to call `solve_task()`
- `src/agent/schemas.py` — all 11 schemas updated with missing fields

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | Phase 9 — testing + deploying the multi-agent orchestrator |
| Where am I going? | Test with real prompts, fix issues, deploy to Cloud Run |
| What's the goal? | Robust multi-agent that handles complex/unknown tasks via Chief planning + sub-agent execution |
| What have I learned? | Sub-agents need field specs, Chief needs persistent memory, "empty environment" must be explicit |
| What have I done? | Full multi-agent orchestrator with Chief memory, ask_chief, conversation logs, thinking step |

---
*Update after completing each phase or encountering errors*
