# Findings & Decisions

## Requirements
- Build HTTPS `/solve` endpoint that accepts POST with task prompt + Tripletex credentials
- Use LLM to interpret multilingual accounting task prompts (7 languages)
- Execute correct Tripletex API calls via provided proxy URL
- Return `{"status": "completed"}` when done
- Handle PDF/image attachments (invoices, receipts)
- Deploy to GCP Cloud Run
- 30 task types across 7 categories

## /solve Payload Format
```json
{
  "prompt": "Opprett en ansatt med navn Ola Nordmann, ola@example.org. Han skal vaere kontoadministrator.",
  "files": [
    {
      "filename": "faktura.pdf",
      "content_base64": "JVBERi0xLjQg...",
      "mime_type": "application/pdf"
    }
  ],
  "tripletex_credentials": {
    "base_url": "https://<provided-per-submission>/v2",
    "session_token": "abc123..."
  }
}
```

## Tripletex API Basics
- REST API v2, OpenAPI spec downloaded locally as `tripletex_openapi.json` (3.7MB)
- Auth: Basic Auth with username `0` and session_token as password
- Special path conventions: `:action` for operations (e.g., `/hours/123/:approve`), `>` for aggregated results
- PUT with optional fields (not PATCH) for partial updates
- Parse script: `python scripts/parse_openapi.py <keyword>` to extract endpoint schemas

## Distilled API Reference (from OpenAPI spec)

### 1. POST /employee — Create employee
**Required:** `userType`, `department.id`
```json
{
  "firstName": "Ola", "lastName": "Nordmann",
  "email": "ola@example.org",
  "userType": "STANDARD",  // enum: STANDARD, EXTENDED, NO_ACCESS
  "department": {"id": 123}
}
```
Optional: `employeeNumber`, `dateOfBirth`, `phoneNumberMobile`, `address`, `bankAccountNumber`, `nationalIdentityNumber`, `allowInformationRegistration`

**Admin/role assignment:** `PUT /employee/entitlement/:grantEntitlementsByTemplate`
- Query params: `employeeId` (required), `template` (required string — template name unknown, test with sandbox)
- This is how you make someone an administrator — NOT via userType

### 2. POST /customer — Create customer
**Required:** `name`
```json
{
  "name": "Acme AS",
  "isCustomer": true,
  "email": "contact@acme.no",
  "organizationNumber": "123456789",
  "phoneNumber": "12345678",
  "language": "NO",  // enum: NO, EN
  "invoiceSendMethod": "EMAIL",  // enum: EMAIL, EHF, EFAKTURA, AVTALEGIRO, VIPPS, PAPER, MANUAL
  "postalAddress": {"addressLine1": "Gate 1", "postalCode": "0001", "city": "Oslo"}
}
```
Optional: `isSupplier`, `invoiceEmail`, `physicalAddress`, `deliveryAddress`, `isPrivateIndividual`, `invoicesDueIn`, `invoicesDueInType` (DAYS/MONTHS/RECURRING_DAY_OF_MONTH), `currency`

### 3. POST /department — Create department
**Required:** `name`, `departmentNumber`
```json
{
  "name": "Salg",
  "departmentNumber": "2",
  "departmentManager": {"id": 123}  // optional Employee ref
}
```
**Lookup:** `GET /department?count=1` — fresh accounts have default "Avdeling"

### 4. POST /product — Create product
**Required:** `name`
```json
{
  "name": "Konsulenttjeneste",
  "number": "1001",
  "priceExcludingVatCurrency": 1500.00,
  "priceIncludingVatCurrency": 1875.00,
  "costExcludingVatCurrency": 1000.00,
  "vatType": {"id": 3},  // lookup via GET /ledger/vatType
  "currency": {"id": 1},
  "department": {"id": 123}
}
```

### 5. POST /order — Create order
**Required:** `customer`, `orderDate`, `deliveryDate`
```json
{
  "customer": {"id": 456},
  "orderDate": "2026-03-20",
  "deliveryDate": "2026-03-25",
  "invoiceComment": "Ref: prosjekt X",
  "orderLines": [
    {
      "product": {"id": 789},
      "description": "Konsulenttime",
      "count": 10,
      "unitPriceExcludingVatCurrency": 1500.00,
      "vatType": {"id": 3}
    }
  ]
}
```
Optional: `receiverEmail`, `reference`, `ourContact`, `deliveryAddress`

### 6. POST /invoice — Create invoice
Can include orders/orderLines inline OR reference existing order.
**Required:** `customer`, `invoiceDate`, `invoiceDueDate`
```json
{
  "invoiceNumber": 0,  // 0 = auto-generate
  "invoiceDate": "2026-03-20",
  "invoiceDueDate": "2026-04-20",
  "customer": {"id": 456},
  "invoiceComment": "Faktura for prosjekt X",
  "orders": [{"id": 123}],  // reference existing order
  "orderLines": [  // OR inline order lines
    {
      "product": {"id": 789},
      "description": "Konsulenttime",
      "count": 10,
      "unitPriceExcludingVatCurrency": 1500.00,
      "vatType": {"id": 3}
    }
  ]
}
```
Query params: `sendToCustomer` (bool), `paymentTypeId` (int), `paidAmount` (number)

### 7. PUT /invoice/{id}/:payment — Register payment
All query params, **all required:**
- `id`: invoice ID
- `paymentDate`: "2026-03-20"
- `paymentTypeId`: int (lookup via `GET /invoice/paymentType`)
- `paidAmount`: number (in invoice currency)
- `paidAmountCurrency`: optional

### 8. PUT /invoice/{id}/:createCreditNote — Create credit note
Query params:
- `id`: invoice ID (required)
- `date`: "2026-03-20" (required)
- `comment`: optional
- `sendToCustomer`: bool, optional

### 9. POST /travelExpense — Create travel expense
```json
{
  "employee": {"id": 123},
  "title": "Reise til Oslo",
  "project": {"id": 456},  // optional
  "department": {"id": 789},  // optional
  "isCompleted": false
}
```
Key fields: `employee` (ref), `title`, `project` (ref), `department` (ref), `date`, `travelDetails`, `isChargeable`

### 10. POST /travelExpense/cost — Add cost to travel expense
```json
{
  "travelExpense": {"id": 123},
  "vatType": {"id": 3},
  "currency": {"id": 1},
  "category": {"id": 1},
  "date": "2026-03-20",
  "description": "Togbillett",
  "rate": 1,
  "count": 1,
  "amountCurrencyIncVat": 500.00,
  "paymentType": "CASH"  // or COMPANY_CARD etc.
}
```
Key fields: `travelExpense` (ref), `vatType` (ref), `date`, `description`, `rate`, `count`, `amountCurrencyIncVat`

### 11. POST /project — Create project
**Required:** `name`, `projectManager`
```json
{
  "name": "Prosjekt Alpha",
  "number": null,  // null = auto-generate
  "description": "Konsulentprosjekt",
  "projectManager": {"id": 123},
  "department": {"id": 456},
  "customer": {"id": 789},
  "startDate": "2026-03-20",
  "endDate": "2026-06-30",
  "isInternal": false,
  "isFixedPrice": false
}
```
Optional: `mainProject` (ref for sub-projects), `projectCategory`, `isOffer`

### Lookup endpoints (for IDs needed by workflows)
| Need | Endpoint | Notes |
|------|----------|-------|
| Department ID | `GET /department?count=1` | Default dept exists in fresh accounts |
| Employee ID | `GET /employee?count=1` | To find existing employees |
| Customer ID | `GET /customer?name=X` | Search by name |
| Product ID | `GET /product?number=X` | Search by product number |
| VAT type ID | `GET /ledger/vatType` | Standard Norwegian MVA types |
| Payment type ID | `GET /invoice/paymentType` | For payment registration |
| Order ID | `GET /order?orderDateFrom=X&orderDateTo=Y` | Both date params required |
| Invoice ID | `GET /invoice?invoiceDateFrom=X&invoiceDateTo=Y` | Both date params required |

## Task Tiers (from competition docs)
56 variants per task (7 languages × 8 data sets). Weighted toward tasks attempted less.

| Tier | Max Score | Examples | Our Workflows |
|------|-----------|----------|---------------|
| **T1** (×1) | 2 | create employee, create customer, create invoice | create_employee, create_customer, create_department, create_product, create_order, create_invoice |
| **T2** (×2) | 4 | invoice with payment, credit notes, project billing | register_payment, create_credit_note, create_travel_expense, create_project |
| **T3** (×3) | 6 | bank reconciliation from CSV, ledger error correction, year-end closing | TBD (opens Saturday) |

**Priority:** T2 workflows give 2× points per task, so finishing those after T1 is high value.

## Scoring System
- Field-by-field verification, normalized to 0-1
- Tier multiplier: T1 ×1, T2 ×2, T3 ×3
- Efficiency bonus (only on perfect scores): up to 2x based on call count + error count
- Max score per task: 2/4/6 for T1/T2/T3
- Best score kept, bad runs don't hurt
- Benchmarks recalculated every 12 hours

## Sandbox vs Competition
| Aspect | Sandbox | Competition |
|--------|---------|-------------|
| Account | Persistent, yours to keep | Fresh empty account per submission |
| API access | Direct to Tripletex | Via authenticated proxy |
| Data | Accumulates over time | Starts empty each time |
| Scoring | None | Automated field-by-field |

**Implication**: Workflows may need to create dependent resources first (e.g., fetch/create department before creating employee).

## Hosting: GCP Cloud Run
- User has GCP account, billing enabled, Vertex AI API enabled
- Deploy: `gcloud run deploy nmiai-tripletex --source . --region europe-north1 --allow-unauthenticated`
- Vertex AI for Gemini: use `us-central1` (not available in europe-north1)

## Resources
- OpenAPI spec: `tripletex_openapi.json` (local)
- Parse script: `scripts/parse_openapi.py`
- Tripletex GitHub: https://github.com/Tripletex/tripletex-api2
- Tripletex Developer Portal: https://developer.tripletex.no
- Competition: https://app.ainm.no
- MCP reference code: `tripletex-mcp-main/` (endpoint paths + payload shapes)

## Multi-Agent Architecture Design (Phase 8 — v2 dynamic)

### The Flow

```
Stage 1: CHIEF PLANS (1 LLM call via complete())
  Input: original prompt + files + workflow catalog
  Output: JSON plan = high-level steps (task descriptions, suggested workflows)
  Chief does NOT pre-extract all fields — just gives direction.

Stage 2: SUB-AGENT EXECUTES (1 tool_use_loop per step)
  The sub-agent has 3 kinds of tools:
    - ask_chief: "What's the org number for this customer?" → complete() to Chief
    - execute_workflow: call a pre-built workflow function
    - tripletex_get/post/put/delete: raw API access

  The sub-agent dynamically pulls information from the Chief as needed.
  It can ask multiple questions, retry workflows, and use raw APIs — all in one loop.

Stage 3: CHIEF REVIEWS (1 LLM call via complete() between steps)
  After each sub-agent finishes, Chief reviews result.
  Injects IDs from prior steps into the next step's context.
  Can adjust remaining steps if something unexpected happened.
```

### Why `ask_chief` is the key insight
- Chief sees the original prompt + files (multilingual, PDFs, etc.)
- Sub-agent NEVER sees the raw prompt — only the Chief's task description
- When sub-agent needs a specific detail, it asks the Chief in plain English
- Chief re-reads the original prompt and answers with the specific data
- This means the Chief doesn't need to extract every field upfront
- Sub-agent pulls exactly what it needs, when it needs it

### Chief's Plan Format (lightweight)
```json
{
  "steps": [
    {
      "task": "Create customer Fjelltopp AS as specified in the prompt",
      "suggested_workflow": "create_customer"
    },
    {
      "task": "Create an invoice for the customer with the product lines from the prompt",
      "suggested_workflow": "create_invoice"
    }
  ]
}
```
Note: NO detailed data extraction. Just high-level tasks. Sub-agents pull details via ask_chief.

### `ask_chief` tool implementation
When sub-agent calls ask_chief("What is the org number for Fjelltopp AS?"):
1. We call complete() with:
   - System: "You are answering a question from a sub-agent about an accounting task."
   - Content: original prompt + files + the sub-agent's question + context from prior steps
2. Chief answers: "The org number is 862382900"
3. Answer returned as tool result to the sub-agent
4. Sub-agent continues with this data

### LLM Call Budget (within 300s)
- Simple T1: 1 plan + sub-agent (1 ask_chief + 1 workflow call) = ~4 LLM iterations
- Complex T2: 1 plan + sub-agent (2-3 ask_chief + 2 workflow calls) + 1 review = ~8 iterations
- Each LLM call ~2-3s with Gemini Flash → well within budget

### Files to Modify
- `src/agent/orchestrator.py` — Rewrite with Chief planner + sub-agent with ask_chief
- Everything else unchanged (server.py, llm.py, schemas.py, workflows, router.py)

## Real Competition Test Prompts

### T1: Supplier registration (Norwegian) — PASS
```
Registrer leverandøren Berghaven Consulting AS (org.nr 998877665), e-post faktura@berghaven.no, adresse Storgata 15, 0184 Oslo.
```
Result: 1 API call, 0 errors, 8.3s. Chief mapped leverandør → create_customer with isSupplier=true.

### T1: Employee creation (Norwegian) — PASS (1 error)
```
Opprett en ansatt med navn Kari Nordmann, e-post kari@example.no. Hun skal være kontoadministrator.
```
Result: 3 API calls, 1 error (entitlement template "administrator" → 404). Fixed with ALL_PRIVILEGES.

### T2: Travel expense with per diem (German) — PASS (1 error)
```
Erstellen Sie eine Reisekostenabrechnung für den Mitarbeiter Paul Hoffmann (paul.hoffmann@example.org). Reise nach Bergen, 15.-18. März 2026. Kosten: Flugticket Berlin-Bergen 2800 NOK, Taxi zum Hotel 450 NOK, Hotel 3 Nächte à 1200 NOK pro Nacht. Außerdem 4 Tage Tagegeld mit einem Tagessatz von 800 NOK. Erstellen Sie zuerst den Mitarbeiter und dann die Reisekostenabrechnung mit allen Kosten.
```
Result: 9 API calls, 1 error (duplicate employee email in sandbox — would be clean in competition). Chief optimized to 1 step.

### T2: Invoice + payment (Spanish) — FAILED (0/8)
```
Crea un pedido para el cliente Luna SL (org. nº 800572525) con los productos Informe de análisis (6174) a 7950 NOK y Diseño web (5787) a 2350 NOK. Convierte el pedido en factura y registra el pago completo.
```
Failure: Step 2 (register_payment) couldn't see Step 1's invoice ID. Chief hallucinated "simulated environment". Fixed in v04 with result passing between steps.

### T2: Supplier invoice (French) — FAILED (crashed)
```
Nous avons reçu la facture INV-2026-4914 du fournisseur Océan SARL (nº org. 853705209) de 56300 NOK TTC. Le montant concerne des services de bureau (compte 6500). Enregistrez la facture fournisseur avec 25% TVA.
```
Failure: 1) Vertex NoneType crash on empty response. 2) No supplier invoice workflow. 3) Sub-agent ignored fallback and tried create_invoice. All three fixed in v04 (crash guard, fallback behavior). Supplier invoice workflow still needed (Phase 9g).

### T1: Invoice with product lines (Nynorsk) — used for local testing
```
Opprett ein faktura til kunden Fjelltopp AS (org.nr 862382900) med tre produktlinjer: Analyserapport (3271) til 1950 kr med 25% MVA, Skylagring (8738) til 6850 kr med 15% MVA, og Nettverksoppsett (4410) til 3200 kr utan MVA.
```

### T2: Register payment on existing invoice (Nynorsk) — FAILED (0/8, hit max iterations)
```
Kunden Elvdal AS (org.nr 963143230) har ein uteståande faktura på 19600 kr eksklusiv MVA for "Datarådgjeving". Registrer full betaling på denne fakturaen.
```
Failure: Chief planned as fallback (couldn't find invoice ID). Sub-agent spiralled for 15 iterations:
- Found customer OK, but couldn't find invoice (empty account — invoice doesn't exist yet)
- Wasted 5 iterations guessing invalid invoice field names (status, isPaid, openAmount, totalAmount)
- Chief eventually realized invoice needs to be created first, but gave wrong field names
- Sub-agent tried create_invoice and create_customer with wrong field names, both failed
- BUG: 422 response detected as "OK" because no `"error"` key in response
Key lesson: Chief must reason that "register payment on invoice" in empty account = create invoice first, then register payment. This is exactly what specialist agents (Phase 10) would handle.

### T2: Invoice + payment (Spanish) — competition prompt (scored 0/8)
```
Crea un pedido para el cliente Luna SL (org. nº 800572525) con los productos Informe de análisis (6174) a 7950 NOK y Diseño web (5787) a 2350 NOK. Convierte el pedido en factura y registra el pago completo.
```
Step 1 (invoice) succeeded after 1 retry (customer name merge issue). Step 2 (payment) failed because
sub-agent couldn't see Step 1's invoice ID — Chief hallucinated "simulated environment". Fixed in v04
with result passing between steps.

### T3: Project time registration + project invoice (Portuguese) — FAILED (0/8, 7/7 API errors)
```
Registe 17 horas para Carolina Pereira (carolina.pereira@example.org) na atividade "Testing" do projeto "Auditoria de segurança" para Estrela Lda (org. nº 834219662). Taxa horária: 1400 NOK/h. Gere uma fatura de projeto ao cliente com base nas horas registadas.
```
Failure chain: 1) Chief produced good 3-step plan but JSON was truncated mid-output → parse failed
→ fell back to generic "complete the task". 2) Fallback prompt said "Do NOT call execute_workflow"
→ sub-agent forced to use raw API. 3) Chief hallucinated fake employee fields (salaryType, active,
startDate, nationality) → 7 consecutive 422s. 4) Hit max iterations.
Key lesson: "Do NOT call execute_workflow" in fallback was too aggressive — sub-agent should still
use workflows for known sub-tasks. Also needs: time registration workflow (POST /timesheet/entry)
and project invoice workflow (POST /invoice/projectInvoice) for T3.

## Model Comparison (2026-03-20)

Tested on Nynorsk order→invoice→payment prompt:
```
Opprett ein ordre for kunden Bølgekraft AS (org.nr 908252764) med produkta
Nettverksteneste (6065) til 25500 kr og Systemutvikling (2511) til 23550 kr.
Konverter ordren til faktura og registrer full betaling.
```

| Model | Time | Iterations | Errors | Invoice Total | Notes |
|-------|------|-----------|--------|---------------|-------|
| gemini-2.5-flash | 42.1s | 3 | 0 | 61,312.50 | Added 25% VAT on top |
| gemini-2.5-pro | 18.6s | 3 | 0 | 49,050 | Treated prices as-is |
| gemini-3.1-pro-preview | 42.0s | 4 | 2 | 49,050 | Duplicate product errors |

**Key finding:** All models score 0/8 in competition. The issue is how "til X kr" is interpreted
(excl-VAT vs incl-VAT), not model quality. Competition scoring expects specific price treatment.

**Gemini 3.x location:** Models with prefix "gemini-3" require `location="global"` instead of
`us-central1`. Added automatic routing in client.py.

**Model availability:** gemini-3-flash-preview was NOT available (404). gemini-3.1-pro-preview
works after enabling in Model Garden.

---
*Update this file after every 2 view/browser/search operations*
