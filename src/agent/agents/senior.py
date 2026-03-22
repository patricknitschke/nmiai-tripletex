"""
Senior Accountant — single-agent fast path.

A single agent loop that has everything it needs: the full prompt,
workflow tools, API lookup, and raw API tools. No planning layer,
no ask_chief round-trips. Just reads the prompt and does the work.

Handles ~80% of competition tasks in 2-3 iterations.
"""

import json
import logging
from datetime import date

from ..api_spec import lookup as api_lookup
from ..llm import complete, tool_use_loop
from ..models import FileAttachment
from ..tripletex import TripletexClient
from ..utils import build_content
from ..workflows import WORKFLOWS
from ..workflows.schemas import TASK_SCHEMAS

logger = logging.getLogger("agent.senior")


# ---------------------------------------------------------------------------
# Workflow catalog (same as chief.py)
# ---------------------------------------------------------------------------

def _build_workflow_catalog() -> str:
    """Generate workflow reference for the system prompt."""
    lines = []
    for task_type, schema in TASK_SCHEMAS.items():
        lines.append(f"### {task_type}")
        lines.append(f"  Endpoint: {schema['api_endpoint']}")
        lines.append(f"  Notes: {schema['notes']}")
        for field in schema["fields"]:
            req = "REQUIRED" if field.get("required") else "optional"
            lines.append(f"  - {field['name']} ({field['type']}, {req}): {field['description']}")
            if "items" in field:
                for item in field["items"]:
                    lines.append(f"      - {item['name']} ({item['type']}): {item['description']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a Senior Accountant AI that solves Tripletex accounting tasks directly.
The task prompt may be in Norwegian, English, Spanish, Portuguese, Nynorsk, German, or French.

Today's date: {today}

## CRITICAL: Environment Rules
The Tripletex account may be EMPTY or may have PRE-EXISTING data depending on the task.

**ALWAYS search before creating:** Before creating an invoice, customer, or other resource, \
check if it already exists using tripletex_get. For example:
- "Register payment on invoice" → FIRST search for the existing invoice (GET /invoice with customer name/number). \
  Only create a new invoice if none is found.
- "Issue credit note for invoice" → FIRST search for the existing invoice. Only create if not found.
- "Create customer X" → The customer might already exist. The workflow handles search-before-create.

**If resources don't exist, create them.** Many tasks require creating prerequisites from scratch \
(customers, employees, products, bank accounts). Workflows handle this automatically.

## Your Workflows (PREFER these over raw API calls)
{workflow_catalog}

## Your Tools
1. **execute_workflow** — Run a pre-built workflow. PREFERRED. Use EXACT field names from \
the specs above. Workflows handle dependency lookups (departments, VAT, bank accounts) automatically.

2. **lookup_api** — Look up API endpoint schemas, field names, and enum values from the \
OpenAPI spec. Use this BEFORE guessing field names and AFTER any 4xx error. \
Examples: "POST /employee", "invoice", "template enum".

3. **tripletex_get/post/put/delete** — Raw API access. Use only when no workflow fits. \
Always use lookup_api first to get the correct endpoint schema.

## Rules
- Read the prompt carefully. Extract ALL data you need, then execute.
- Use EXACT field names from the workflow specs — do not rename them.
- Chain workflows when needed: create customer first, then invoice, then payment. \
  Pass IDs from one result to the next.
- Creating a SUPPLIER (leverandør/Lieferant/fournisseur) = create_customer with isSupplier: true.
- **VAT:** Standard Norwegian VAT is 25%. ALWAYS use vatRatePercent: 25 on order lines \
  unless the prompt explicitly says the service is TAX EXEMPT ("fritatt"/"exonéré"/"0% MVA"/"sin impuestos"). \
  CRITICAL: "sin IVA"/"ohne MwSt"/"excl MVA"/"hors TVA"/"eksklusiv MVA" means the PRICE is stated \
  excluding VAT — it does NOT mean 0% VAT. The 25% rate STILL applies. \
  Even if the Chief's plan says vatRatePercent: 0, OVERRIDE it to 25 unless the prompt says EXEMPT.
- **Norwegian VAT rates by category (IMPORTANT for receipts/expenses):** \
  25% = general goods & services (default) \
  15% = food/groceries (matvarer/næringsmidler) \
  12% = passenger transport (persontransport: flights/flybillett, trains/tog, bus, taxi, ferge), hotels/overnatting, cinema/kino, amusement parks \
  0% = tax exempt (international transport, healthcare, education, financial services) \
  When registering expenses from receipts, use the CORRECT rate for each item category, NOT just 25% for everything.
- **Receipts/kvitteringer with MULTIPLE items:** If a receipt has items at different VAT rates or \
  different expense accounts, call register_expense ONCE PER LINE ITEM. For example, a receipt with \
  a flight ticket (12% VAT, account 7140) and office supplies (25% VAT, account 6800) needs TWO \
  separate register_expense calls. Use search_pdf to extract each line item before calling workflows.- **Overdue invoice / reminder fee / Mahngebühr tasks:** Use find_overdue_invoices FIRST to find the real \
  invoice and customer. NEVER invent customer names. The result gives you customerName, customerId, \
  invoiceId, and amountOutstanding. Pass customerName to create_voucher (REQUIRED for account 1500 \
  AR postings) and invoiceId to register_payment. The create_invoice for the reminder fee is a separate \
  billing document — it does not double-book with the voucher because the invoice posts to default \
  revenue accounts while the voucher uses specific GL accounts (e.g. 1500/3400).- **PDF files:** You do NOT see PDF contents directly. Use the search_pdf tool to extract data from \
  attached PDFs. Ask SPECIFIC questions: items, amounts, dates, supplier name, etc. For receipts, \
  ALWAYS ask for ALL individual line items with their amounts and categories.
- **Employment contracts (tilbudsbrev/arbeidskontrakt/carta de oferta/Arbeitsvertrag):** Use register_employment. \
  Extract ALL fields from the PDF: firstName, lastName, dateOfBirth, nationalIdentityNumber, bankAccountNumber, \
  departmentName, startDate, occupationCode (STYRK/yrkeskode — a 4-digit code like "2411"), \
  percentageOfFullTimeEquivalent, annualSalary, hoursPerDay. Don't skip any field that's in the document.
- **Month-end/year-end closing (avskrivning/periodisering/accrual/depreciation/salary provision):** \
    Follow this exact sequence for multi-entry closing tasks to avoid 422 errors and unnecessary writes: \
    (1) Gather all account numbers needed across ALL entries, then do ONE GET /ledger/account with comma-separated numbers. \
    (2) If accounts are missing, create them BEFORE voucher POST: use POST /ledger/account/list when more than one account is missing, otherwise POST /ledger/account. \
    (3) Post EACH journal entry as a SEPARATE create_voucher call (not all in one). \
    (4) If the task asks to verify trial balance (e.g. "kontroller at saldobalansen går i null"), call \
    verify_trial_balance with dateTo=YYYY-MM+1-01 (dateTo is exclusive) and confirm balanced=true. \
    For this saldobalanse check, do NOT include dateFrom (point-in-time snapshot). \
    Do NOT invent amounts. If an amount is missing in the prompt (for example salary accrual), either derive it from \
    authoritative payroll GET data or explicitly report the amount as missing. \
    For prepaid-expense periodization from account 1700-1799, do NOT guess the debit expense account. \
    First inspect historical postings on the prepaid account and reuse the original counterpart expense account. \
    If counterpart account cannot be determined reliably, report missing mapping instead of guessing. \
    If payroll endpoints fail with 5xx and the accrual amount is unspecified, do NOT infer from rough 5000 ledger turnover alone. \
    Report missing/failed source and continue with the other closing entries. \
    For linear depreciation, use expense account on debit (e.g. 6030) and accumulated depreciation contra-account on credit \
    (e.g. 1209), NOT the gross asset account (1200). Prefer existing accumulated-depreciation accounts before creating new ones. \
    **DEPRECIATION PRECISION:** Always use 2 decimal places: round(cost / years, 2) for annual, \
    round(cost / years / 12, 2) for monthly. Example: 280000 / 9 = 31111.11, NOT 31111. \
    484650 / 8 = 60581.25, NOT 60581. Integer truncation understates expense and cascades into wrong tax. \
    **BALANCE SHEET dateTo IS EXCLUSIVE:** /balanceSheet dateTo excludes that day. \
    For year-end 2025 profit, use dateFrom=2025-01-01&dateTo=2026-01-01 (NOT dateTo=2025-12-31). \
    For month-end March, use dateTo=2026-04-01. This is the #1 cause of wrong tax provisions.
- **Ledger error correction (Hauptbuch/grand livre/livro razão):** Use analyze_ledger first, then fix each error: \
    (1) Wrong account → create_voucher: debit correct account, credit wrong account (or vice versa). \
    (2) Duplicate voucher → create_voucher: reverse the duplicate (negate both postings). \
    (3) Missing VAT line (fehlende MwSt/MVA manquante) → ONE corrective create_voucher with only the missing delta \
    (for 25% VAT: debit 2710 by amount×0.25, credit the original balancing account, typically 1920/2400/1500). \
    Do NOT re-post the full expense and do NOT reverse/re-register if a delta correction is sufficient. \
    (4) Wrong amount → create_voucher: reverse the difference (e.g. if 22550 was booked instead of 5150, \
    reverse 22550-5150=17400 from the expense account back to bank 1920). \
    Post each correction with the minimum number of writes needed.
- **Time registration dates:** Newly created projects have startDate=today. You CANNOT register hours before \
  the project start date. Use today's date for all time entries on new projects. If you need to register many \
  hours, put them all on today (or split across today and future dates). NEVER use past dates for new projects.
- **Project manager:** Pass projectManagerEmail to the create_project workflow to set the correct person.
- **Batch project creation:** When creating MULTIPLE projects, use create_projects_batch — it resolves PM once \
  and loops POST /project for each project. Pass a "projects" array with each \
  project's name, activityName, etc. More efficient than calling create_project in a loop (PM resolved only once).
- **Activities in projects:** Pass activityName or projectActivities to create_project / create_projects_batch. \
  Activities are created separately via POST /project/projectActivity after project creation (ensures checkers can detect them).
- **Expense comparison across months:** Use compare_expenses (not analyze_ledger) when comparing expenses across \
  months or finding top accounts by amount. It uses GET /ledger/posting to fetch actual expense data and \
    aggregates by account per month — returning ONE canonical ranking in top_increases. \
    Use only top_increases for downstream actions and final reporting. If topN=3, your final response must list 3 lines.
- **Cache resolved IDs:** After resolving an employee, customer, or entity by name/email, REUSE the ID for \
  subsequent calls. Do NOT call GET /employee?email=... repeatedly for the same person. Store the ID and pass it \
  directly (e.g. projectManagerId instead of projectManagerEmail on the 2nd+ call).
- **Trust 201 responses:** After a successful POST that returns 201 with the created object, use the ID/data from \
  the response directly. Do NOT immediately GET the same resource to "verify" — that wastes a call.
- **Partial failures:** Workflows may return `"ok": false` with `"errors"` and `"_needs_repair"`. \
  This means the main resource was created but sub-steps failed (e.g. cost lines, employment details, \
  dimension values). READ the `_needs_repair` message — it tells you exactly what to fix with raw API calls. \
  Do NOT assume the task is complete when you see `ok: false`. Also check for `"warnings"` — these indicate \
  potential issues (e.g. unresolved accounts) that may need attention.
- If a call returns a 4xx error, use lookup_api to check correct fields, then retry ONCE.
- Do NOT guess field names. Use lookup_api or the workflow specs above.
- Be EFFICIENT and DECISIVE. Aim to complete the task in 3-5 tool calls.
- When done, stop calling tools and briefly confirm what you created.
"""


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "execute_workflow",
        "description": (
            "Execute a pre-built Tripletex workflow. PREFERRED over raw API calls. "
            "Workflows handle dependency lookups, resource creation, and error handling automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workflow_name": {
                    "type": "string",
                    "enum": list(WORKFLOWS.keys()),
                    "description": "Which workflow to run",
                },
                "data": {
                    "type": "object",
                    "description": "Data fields for the workflow — use EXACT field names from the spec",
                },
            },
            "required": ["workflow_name", "data"],
        },
    },
    {
        "name": "lookup_api",
        "description": (
            "Look up Tripletex API endpoint details from the OpenAPI spec. "
            "Use BEFORE guessing field names and AFTER any 4xx error. "
            "Examples: 'POST /employee', 'invoice', 'template enum'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Endpoint path, keyword, or enum query",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "tripletex_get",
        "description": "GET request to Tripletex API.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path"},
                "params": {
                    "type": "object",
                    "description": "Query parameters",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["endpoint"],
        },
    },
    {
        "name": "tripletex_post",
        "description": "POST request to Tripletex API.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path"},
                "payload": {"type": "object", "description": "JSON body"},
                "params": {
                    "type": "object",
                    "description": "Query parameters",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["endpoint", "payload"],
        },
    },
    {
        "name": "tripletex_put",
        "description": "PUT request to Tripletex API.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path"},
                "payload": {"type": "object", "description": "JSON body"},
                "params": {
                    "type": "object",
                    "description": "Query parameters",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["endpoint"],
        },
    },
    {
        "name": "tripletex_delete",
        "description": "DELETE request to Tripletex API.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path"},
            },
            "required": ["endpoint"],
        },
    },
    {
        "name": "search_pdf",
        "description": (
            "Extract specific information from attached PDF files (receipts, invoices, contracts). "
            "Ask a TARGETED question and get a precise answer. "
            "Examples: 'List all line items with amounts and categories', "
            "'What is the receipt date and supplier name?', "
            "'Extract employee details: name, DOB, salary, department'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Specific question about the PDF content",
                },
            },
            "required": ["query"],
        },
    },
]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _search_pdf(query: str, pdf_files: list[FileAttachment]) -> dict:
    """Use a fast model to answer specific questions about PDF files."""
    if not pdf_files:
        return {"error": "No PDF files attached to this task."}

    # Build content: query + all PDFs
    content = [{"type": "text", "text": query}]
    for f in pdf_files:
        content.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": f.content_base64,
            },
        })

    system = (
        "You are a precise document extraction assistant. "
        "Answer the user's question based ONLY on the attached PDF content. "
        "Be exact with numbers, dates, and names — do not round or approximate. "
        "If the PDF contains a table or list of items, extract ALL of them. "
        "Return structured data when possible (JSON or clear labeled format). "
        "If information is not found in the PDF, say so explicitly."
    )

    try:
        result = await complete(system, content, max_tokens=2048, model="gemini-2.5-flash")
        logger.info("search_pdf result: %s", result)
        return {"result": result}
    except Exception as e:
        logger.exception("search_pdf failed")
        return {"error": f"PDF extraction failed: {str(e)}"}


async def run_senior_accountant(
    prompt: str,
    files: list,
    client: TripletexClient,
    deadline: float | None = None,
    plan_preamble: str | None = None,
) -> dict:
    """Run the Senior Accountant — single agent, fast path.

    If plan_preamble is provided (hybrid mode), it's injected into the system prompt
    so the Senior can follow the Chief's strategic plan while executing directly.
    """

    today = date.today().isoformat()
    catalog = _build_workflow_catalog()
    system = SYSTEM_PROMPT.format(today=today, workflow_catalog=catalog)

    if plan_preamble:
        system += f"\n\n## Chief Accountant's Plan (follow this strategy)\n{plan_preamble}\n"

    # Separate PDF files from non-PDF files
    # PDFs are accessible ONLY via search_pdf tool (targeted extraction)
    # Non-PDF files (CSVs, images, text) are attached directly to context
    pdf_files = [f for f in files if f.mime_type == "application/pdf"]
    non_pdf_files = [f for f in files if f.mime_type != "application/pdf"]

    if pdf_files:
        logger.info("PDF files stored for search_pdf tool: %s", [f.filename for f in pdf_files])

    content = build_content(prompt, non_pdf_files)

    if pdf_files:
        # Tell Senior that PDFs are available via search_pdf
        pdf_names = ", ".join(f.filename for f in pdf_files)
        content.append({"type": "text", "text": f"[PDF files attached: {pdf_names}. Use the search_pdf tool to extract information from them.]"})

    async def execute_tool(name: str, input_data: dict) -> dict:
        # Circuit breaker: if token is dead, tell the LLM to stop immediately
        if client.token_dead and name != "lookup_api":
            logger.warning("Senior → %s BLOCKED — token is dead", name)
            return {
                "error": "FATAL: Tripletex API token has expired. All API calls will fail. "
                         "Stop calling tools and report that the task could not be completed "
                         "due to an expired authentication token.",
                "_token_dead": True,
            }

        if name == "search_pdf":
            query = input_data.get("query", "")
            logger.info("Senior → search_pdf('%s')", query)
            return await _search_pdf(query, pdf_files)

        if name == "lookup_api":
            query = input_data.get("query", "")
            logger.info("Senior → lookup_api('%s')", query)
            return {"result": api_lookup(query)}

        if name == "execute_workflow":
            wf_name = input_data.get("workflow_name", "")
            data = input_data.get("data", {})
            if wf_name not in WORKFLOWS:
                return {"error": f"Unknown workflow '{wf_name}'. Available: {list(WORKFLOWS.keys())}"}
            logger.info("Senior → execute_workflow('%s', %s)", wf_name, json.dumps(data))
            try:
                result = await WORKFLOWS[wf_name](data, client)
                has_error = "error" in result or (isinstance(result.get("status"), int) and result["status"] >= 400)
                is_partial = result.get("ok") is False or bool(result.get("_needs_repair"))
                has_warnings = bool(result.get("warnings"))
                if has_error:
                    logger.info("Workflow '%s' FAILED: %s", wf_name, json.dumps(result))
                elif is_partial:
                    logger.warning("Workflow '%s' PARTIAL FAILURE: %s", wf_name, result.get("errors", result.get("_needs_repair", "")))
                elif has_warnings:
                    logger.info("Workflow '%s' OK (with warnings): %s", wf_name, result.get("warnings"))
                else:
                    logger.info("Workflow '%s' OK: %s", wf_name, json.dumps(result))
                return result
            except Exception as e:
                logger.exception("Workflow '%s' raised exception", wf_name)
                return {"error": str(e)}

        # Raw API tools
        endpoint = input_data.get("endpoint", "")
        params = input_data.get("params")
        payload = input_data.get("payload")
        logger.info("Senior → raw %s %s", name, endpoint)

        if name == "tripletex_get":
            return await client.get(endpoint, params=params)
        elif name == "tripletex_post":
            return await client.post(endpoint, payload=payload, params=params)
        elif name == "tripletex_put":
            return await client.put(endpoint, payload=payload, params=params)
        elif name == "tripletex_delete":
            return await client.delete(endpoint)
        else:
            return {"error": f"Unknown tool: {name}"}

    logger.info("Starting Senior Accountant (single-agent fast path)")
    return await tool_use_loop(
        system=system,
        user_content=content,
        tools=TOOLS,
        execute_tool=execute_tool,
        max_iterations=20,
        deadline=deadline,
    )
