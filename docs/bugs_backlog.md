# Bugs & Backlog

## Bugs

### B1: `lookup_api` crashes on GCP (FIXED)
- **Status:** FIXED
- **Impact:** CRITICAL — kills any task where the LLM calls lookup_api
- **Symptom:** `FileNotFoundError: '/app/src/agent/../../docs/tripletex_openapi.json'`
- **Root cause:** Dockerfile only copies `src/`, not `docs/`. The OpenAPI spec file isn't in the container.
- **Fix:** Added `COPY docs/tripletex_openapi.json docs/tripletex_openapi.json` to Dockerfile
- **Affected tasks:** Spanish payroll (Fernando López), English dimensions (Kostsenter)
- **Date:** 2026-03-20

### B2: Hybrid mode routes to Senior instead of specialists (OPEN)
- **Status:** OPEN — code is built but not yet deployed
- **Impact:** MEDIUM — specialists exist but aren't used in production
- **Symptom:** Logs show "HYBRID MODE: Senior executing with plan..." instead of specialist routing
- **Root cause:** Deployed v08 has old hybrid code (plan preamble to Senior). New specialist routing code not yet deployed.
- **Fix:** Deploy latest code

### B3: Chief fails "fresh empty environment" for implicit prerequisites (FIXED)
- **Status:** OPEN
- **Impact:** CRITICAL — fails ALL "register payment on invoice" tasks (scored 0 on these)
- **Symptom:** Chief says "invoice number is missing, can't proceed" → Senior trusts bad plan → 0 API calls, task abandoned
- **Root cause:** Chief doesn't reason that "register payment on invoice" in an empty account means create the invoice first. The fresh-env instruction IS in the Chief's prompt, but it's not strong enough for implicit prerequisite reasoning.
- **Example prompt:** `"Kunden Tindra AS (org.nr 923598324) har en utestående faktura på 15700 kr eksklusiv MVA for 'Konsulenttimer'. Registrer full betaling på denne fakturaen."`
- **Expected behavior:** Create customer → Create invoice (15700 kr excl VAT, "Konsulenttimer") → Register full payment
- **Actual behavior:** Chief plan = "fallback, invoice number missing". Senior does nothing (0 API calls, 5.9s).
- **Note:** Senior alone (without Chief's plan) would likely handle this correctly — the Chief's bad plan POISONS the Senior.
- **Fix applied (both):**
  1. Strengthened Chief's fresh-env prompt with explicit examples ("Customer has outstanding invoice → CREATE it first") and hard rule: "NEVER say you can't proceed because X doesn't exist"
  2. Added safety net in orchestrator: if Chief produces a single fallback step with "give up" language (missing, can't proceed, please provide), Senior ignores the plan and handles the task directly
- **Date:** 2026-03-20, fixed 2026-03-21

### B6: "Fresh empty environment" assumption is WRONG for some tasks (FIXED)
- **Status:** FIXED
- **Impact:** CRITICAL — credit note and payment tasks fail because we create duplicates instead of finding existing resources
- **Symptom:** Credit note task scored 4/5 failed. Invoice `invoiceNumber: 2` (not 1) and bank account already existed. Task expected us to FIND the pre-existing invoice #1 and credit note it, but we created a duplicate #2.
- **Root cause:** We assumed all competition accounts are completely empty. WRONG — some T2 tasks (credit notes, payments) pre-populate invoices/customers. Our agent creates duplicates instead of searching.
- **Evidence:**
  - Viento SL (ES): 4/5 failed — created invoice #2, credit noted it. Invoice #1 pre-existed.
  - Brückentor GmbH (DE): 4/5 failed — same pattern. Bank account already set up, invoiceNumber=2.
- **Fix:** Updated Chief and Senior prompts: "ALWAYS search before creating. Some tasks have pre-existing data." Agent now searches for existing invoices/customers before creating new ones.
- **Date:** 2026-03-21

---

## Missing Workflows

### W1: Payroll / Salary
- **Example prompt (ES):** `"Ejecute la nómina de Fernando López (fernando.lopez@example.org) para este mes. El salario base es de 37850 NOK. Añada una bonificación única de 9200 NOK además del salario base."`
- **What's needed:** Create employee → Create payslip with base salary + bonus
- **API endpoints:** Unknown — need to look up `/salary/payslip` or similar in OpenAPI spec
- **Date seen:** 2026-03-20

### W2: Supplier Invoice (AP)
- **Example prompt (FR):** `"Nous avons reçu la facture INV-2026-4914 du fournisseur Océan SARL (nº org. 853705209) de 56300 NOK TTC. Le montant concerne des services de bureau (compte 6500). Enregistrez la facture fournisseur avec 25% TVA."`
- **What's needed:** Create supplier → Post voucher with debit (expense account) + credit (supplier account)
- **API endpoints:** POST /ledger/voucher (double-entry bookkeeping)
- **Status:** AP specialist placeholder exists, needs real workflow (9j)
- **Date seen:** 2026-03-20

### W3: Time Registration
- **Example prompts:**
  - (PT) `"Registe 17 horas para Carolina Pereira (carolina.pereira@example.org) na atividade 'Testing' do projeto 'Auditoria de segurança' para Estrela Lda (org. nº 834219662). Taxa horária: 1400 NOK/h."`
  - (PT) `"Registe 4 horas para Maria Ferreira (maria.ferreira@example.org) na atividade 'Utvikling' do projeto 'Desenvolvimento de app' para Estrela Lda (org. nº 909621682). Taxa horária: 1050 NOK/h."`
- **What's needed:** Create employee → Create customer → Create project → Create activity → Register timesheet entries
- **API endpoints:** POST /timesheet/entry, project activities
- **Status:** T3 task, not built. Chief correctly plans prerequisites but falls back for time registration.
- **Date seen:** 2026-03-20

### W4: Project Invoice
- **Example prompts:**
  - (PT) `"Gere uma fatura de projeto ao cliente com base nas horas registadas."`
  - Often combined with W3: `"...Gere uma fatura de projeto ao cliente com base nas horas registadas."`
- **What's needed:** Generate invoice from project's registered billable hours
- **API endpoints:** POST /invoice/projectInvoice or similar
- **Status:** T3 task, not built. Usually appears as last step after W3.
- **Date seen:** 2026-03-20

### W5: Custom Accounting Dimensions
- **Example prompt (EN):** `"Create a custom accounting dimension 'Kostsenter' with the values 'Logistikk' and 'Produktutvikling'. Then post a voucher on account 6300 for 14800 NOK, linked to the dimension value 'Logistikk'."`
- **What's needed:** Create dimension → Add dimension values → Post voucher linked to dimension
- **API endpoints:** Unknown — need to look up dimension endpoints in OpenAPI spec
- **Status:** T3 task, not built
- **Date seen:** 2026-03-20

### W6: Manual Voucher / Journal Entry
- **Example prompt (EN):** (part of W5 above) `"Post a voucher on account 6300 for 14800 NOK"`
- **What's needed:** Post manual journal entry with debit/credit lines
- **API endpoints:** POST /ledger/voucher
- **Status:** T3 task, not built. Overlaps with W2 (AP specialist uses same endpoint).
- **Date seen:** 2026-03-20

---

## Competition Test Results (v08, 2026-03-20)

### Successes
| Prompt | Lang | Model | Time | API Calls | Errors | Notes |
|--------|------|-------|------|-----------|--------|-------|
| Invoice: Prairie SARL, 3 product lines (FR) | FR | gemini-3.1-pro | 31.7s | 9 | 0 | 3 products with different VAT rates, all resolved correctly |
| Invoice: Bergwerk GmbH + send (DE) | DE | gemini-2.5-flash | 27.8s | 6 | 0 | Single line, sendToCustomer=true handled |
| Project: Montaña SL fixed price + partial invoice (ES) | ES | gemini-2.5-flash | 23.9s | 9 | 0 | 4-step plan, all 201s |

### Failures
| Prompt | Lang | Model | Time | API Calls | Errors | Bug | Notes |
|--------|------|-------|------|-----------|--------|-----|-------|
| Payroll: Fernando López (ES) | ES | gemini-3.1-pro | — | 2 | 1 | B1 | Created employee OK, then lookup_api crashed |
| Dimensions + voucher: Kostsenter (EN) | EN | gemini-3.1-pro | — | 0 | 1 | B1 | lookup_api crashed immediately |
| Payment: Tindra AS (NO) | NO | gemini-2.5-flash | 5.9s | 0 | 0 | B3 | Chief gave up ("invoice number missing"), 0 API calls |
| Order→Invoice→Payment: Waldstein GmbH (DE) | DE | gemini-3.1-pro | 47.0s | 10 | 0 | B4 | Step 1 specialist did invoice+payment, Step 2 double-paid → amountOutstanding: -63500 |
| Time reg + project invoice: Maria Ferreira (PT) | PT | gemini-3.1-pro | 100.1s | 4 | 0 | W3+W4 | Steps 1-3 OK, Step 4 spiraled 9 iterations on lookup_api, hit deadline |

### B4: Specialists duplicate work — each sees full prompt (FIXED)
- **Status:** OPEN
- **Impact:** HIGH — creates duplicate resources (2 projects), wastes time (86s for a 20s task)
- **Symptom:** Step 1 general specialist executed all 3 workflows itself. Steps 2+3 ran anyway → duplicate project.
- **Root cause:** Each specialist receives the full original prompt, so smart specialists do more than their assigned step.
- **Example prompt (FR):** `"Créez le projet 'Migration Étoile' lié au client Étoile SARL (nº org. 964531161). Le chef de projet est Arthur Dubois (arthur.dubois@example.org)."`
- **Expected:** 1 project. **Actual:** 2 projects (numbers "1" and "2"). 86.3s, 7 API calls across 3 specialist loops.
- **Fix:** Replaced specialist step-by-step routing with Senior-with-plan-preamble. Chief plans → plan injected into Senior's system prompt → Senior executes everything in one loop. No duplication, no multi-loop overhead.
- **Date:** 2026-03-20, fixed 2026-03-21

### B5: Customer email set as invoiceEmail but not email (FIXED)
- **Status:** FIXED
- **Impact:** MEDIUM — scoring checks `email` field which was empty
- **Symptom:** Prompt says "E-post: faktura@skogheim.no", LLM maps to `invoiceEmail` because of "faktura@" prefix. General `email` field left empty.
- **Fix:** Customer workflow now copies `invoiceEmail` to `email` when `email` isn't explicitly set.
- **Date:** 2026-03-21

### Partial / Potential Issues
| Prompt | Lang | Model | Time | API Calls | Errors | Notes |
|--------|------|-------|------|-----------|--------|-------|
| Project fixed price + partial invoice: Montaña SL (ES) | ES | gemini-2.5-flash | 23.9s | 9 | 0 | 4-step plan executed perfectly. BUT: project `fixedprice` returned 0 — workflow doesn't set it. May lose points on project fields. Also: 0% VAT assumed for partial invoice (no VAT specified in prompt) — may be wrong. |

---

## Future Ideas
- Concise workflow results (9k-1) — reduce context flooding from full API response JSON
- Iteration budget awareness (9k-3) — "be decisive, ~12 iterations max"
- Model selection — gemini-2.5-pro fastest (19s) but all models tie on accuracy
- Senior override for bad Chief plans — if Chief says "can't proceed", Senior should ignore and try anyway
