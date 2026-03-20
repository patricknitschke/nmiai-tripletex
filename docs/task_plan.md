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
- [ ] **9a: Local testing** — test with real competition prompts (T1 + T2)
- [ ] **9b: Fix issues** from test results
- [ ] **9c: Deploy to Cloud Run**
- [ ] **9d: Submit to competition + verify scores**

### Phase 10: Tier 3 Workflows + Iteration
- [ ] Complex scenarios (opens Saturday — wait and assess complexity)
- [ ] Iterate based on leaderboard scores

### Phase 9: Tier 3 Workflows + Iteration
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
