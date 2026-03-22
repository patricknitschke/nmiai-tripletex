# NMiAI - Tripletex Challenge

AI accounting agent built for the **NM i AI** competition (March 19–22, 2026). Receives accounting task prompts, plans with an LLM, and executes pre-built workflows against the [Tripletex](https://tripletex.no) API — fully autonomously.

## Architecture

```
POST /solve (280s deadline)
  → Chief Agent (1 LLM call, ~6-10s)
      Plans up to 5 steps from the task prompt (no files)
  → Senior Agent (tool loop, max 15 iterations)
      Executes the plan using workflows + raw API tools
      Receives full prompt, file attachments (PDFs, CSVs), and Chief's plan
```

- **Chief** — lightweight planner. Reads the prompt, reasons about dependencies, outputs a step-by-step plan. 30s timeout with fallback.
- **Senior** — the workhorse. Runs pre-built workflows and raw Tripletex API calls in a tool loop. Handles ~80% of tasks in 2–3 iterations.
- **Orchestrator** — routes tasks. Some categories (bank reconciliation, month-end closing, dimensions) skip the Chief entirely via keyword rules.

## Stack

| Layer | Tech |
|-------|------|
| Runtime | Python 3.11, FastAPI, uvicorn |
| LLM | Google Vertex AI (Gemini) |
| HTTP client | httpx (async) |
| Deployment | GCP Cloud Run, Cloud Build |
| Container | Docker (python:3.11-slim) |

## Workflows

26 pre-built workflows covering Tier 1–3 competition tasks:

| Category | Workflows |
|----------|-----------|
| **Customers & Suppliers** | `create_customer`, `create_supplier_invoice` |
| **Employees** | `create_employee`, `register_employment` |
| **Invoicing** | `create_invoice`, `create_order`, `create_credit_note`, `create_project_invoice` |
| **Payments** | `register_payment`, `register_fx_payment` |
| **Projects** | `create_project`, `create_projects_batch`, `register_time` |
| **Expenses** | `create_travel_expense`, `delete_travel_expense`, `register_expense` |
| **Accounting** | `create_voucher`, `create_dimension`, `create_dimension_voucher` |
| **Payroll** | `register_payroll` |
| **Overdue** | `find_overdue_invoices`, `send_reminder` |
| **Analysis** | `analyze_ledger`, `compare_expenses`, `verify_trial_balance` |
| **Bank** | `reconcile_bank_statement` |
| **Other** | `create_department`, `create_product` |

Each workflow is self-contained — handles prerequisite lookups (VAT, bank accounts, departments) internally. Search-before-create prevents duplicates.

## Project Structure

```
src/agent/
├── server.py            # FastAPI app (/solve, /health)
├── orchestrator.py      # Routes tasks → Chief/Senior
├── tripletex.py         # Async Tripletex API client
├── api_spec.py          # OpenAPI spec lookup tool
├── models.py            # Request/response models
├── utils.py             # Shared utilities
├── agents/
│   ├── chief.py         # Planning agent (1 LLM call)
│   └── senior.py        # Execution agent (tool loop)
├── llm/
│   └── client.py        # Vertex AI / Gemini client
└── workflows/
    ├── __init__.py      # Workflow registry
    ├── schemas.py       # Task type schemas
    ├── invoice.py       # Invoice workflows
    ├── customer.py      # Customer/supplier workflows
    ├── ...              # 20+ workflow modules
    └── bank_reconciliation.py
```

## Quick Start

### Local Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Set environment variables
cp .env.example .env  # Add your credentials

# Run locally
uvicorn src.agent.server:app --host 0.0.0.0 --port 8080

# Or use the script
bash scripts/start_local.bash
```

### Deploy to Cloud Run

```bash
bash scripts/deploy.bash
```

### Testing

```bash
# Run tests (fast, no LLM calls)
pytest

# Run tests including slow LLM integration tests
pytest -m "slow"
```


## API

### `POST /solve`

Receives a task prompt and Tripletex credentials. Returns when the task is solved.

```json
{
  "prompt": "Create a customer named Acme Corp with org number 123456789",
  "tripletex_credentials": {
    "base_url": "https://api.tripletex.io",
    "session_token": "..."
  },
  "files": []
}
```

### `GET /health`

Returns `{"status": "ok"}`.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AGENT_MODE` | Execution mode: `hybrid` or `senior` | `hybrid` |
| `GOOGLE_CLOUD_PROJECT` | GCP project for Vertex AI | — |
| `GOOGLE_CLOUD_LOCATION` | GCP region | `europe-west1` |

## Competition Context

**NM i AI** (Norwegian Championship in AI) — March 19–22, 2026. Teams build AI agents that solve real-world accounting tasks in Tripletex. Scoring is based on task correctness with an efficiency multiplier (fewer API calls + fewer errors = up to 2x bonus).

## License

See [LICENSE](LICENSE).