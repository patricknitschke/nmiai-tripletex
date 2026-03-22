"""Shared test fixtures: MockTripletexClient, CSV loader, response factories."""

import csv
import re
from pathlib import Path

import pytest

TASKS_CSV = Path(__file__).parent.parent / "docs" / "tasks.csv"


# ---------------------------------------------------------------------------
# MockTripletexClient
# ---------------------------------------------------------------------------

class MockTripletexClient:
    """Records API calls and returns configurable responses.

    Usage:
        client = MockTripletexClient()
        client.when_get("/employee", {"values": [{"id": 1, "firstName": "Test"}]})
        client.when_post("/salary/transaction", {"value": {"id": 99}})

        result = await some_workflow(data, client)

        assert client.get_calls("POST", "/salary/transaction")
    """

    def __init__(self):
        self.calls: list[dict] = []
        self.call_count: int = 0
        self.error_count: int = 0
        self.token_dead: bool = False
        # (method, pattern) -> response or [response1, response2, ...]
        self._responses: dict[tuple[str, str], dict | list] = {}

    # --- Response configuration ---

    def when_get(self, pattern: str, response: dict) -> "MockTripletexClient":
        self._responses[("GET", pattern)] = response
        return self

    def when_post(self, pattern: str, response: dict) -> "MockTripletexClient":
        self._responses[("POST", pattern)] = response
        return self

    def when_put(self, pattern: str, response: dict) -> "MockTripletexClient":
        self._responses[("PUT", pattern)] = response
        return self

    def when_delete(self, pattern: str, response: dict) -> "MockTripletexClient":
        self._responses[("DELETE", pattern)] = response
        return self

    # --- API methods (same signature as TripletexClient) ---

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        return self._record_and_respond("GET", endpoint, params=params)

    async def post(self, endpoint: str, payload: dict | None = None, params: dict | None = None) -> dict:
        return self._record_and_respond("POST", endpoint, payload=payload, params=params)

    async def put(self, endpoint: str, payload: dict | None = None, params: dict | None = None) -> dict:
        return self._record_and_respond("PUT", endpoint, payload=payload, params=params)

    async def delete(self, endpoint: str) -> dict:
        return self._record_and_respond("DELETE", endpoint)

    # --- Assertion helpers ---

    def get_calls(self, method: str | None = None, endpoint_pattern: str | None = None) -> list[dict]:
        """Filter recorded calls by method and/or endpoint substring."""
        result = self.calls
        if method:
            result = [c for c in result if c["method"] == method]
        if endpoint_pattern:
            result = [c for c in result if endpoint_pattern in c["endpoint"]]
        return result

    def assert_called(self, method: str, endpoint_pattern: str):
        """Assert that at least one matching call was made."""
        matching = self.get_calls(method, endpoint_pattern)
        assert matching, (
            f"Expected {method} call matching '{endpoint_pattern}', "
            f"but got: {[(c['method'], c['endpoint']) for c in self.calls]}"
        )

    def assert_not_called(self, method: str, endpoint_pattern: str):
        """Assert that no matching call was made."""
        matching = self.get_calls(method, endpoint_pattern)
        assert not matching, f"Did not expect {method} call matching '{endpoint_pattern}', but found {len(matching)}"

    # --- Internal ---

    def _record_and_respond(self, method: str, endpoint: str, payload: dict | None = None, params: dict | None = None) -> dict:
        self.calls.append({
            "method": method,
            "endpoint": endpoint,
            "payload": payload,
            "params": params,
        })
        self.call_count += 1
        return self._find_response(method, endpoint)

    def _find_response(self, method: str, endpoint: str) -> dict:
        # Try exact match first, then substring match (most specific wins)
        best_match = None
        best_len = 0
        for (m, pattern), response in self._responses.items():
            if m == method and pattern in endpoint and len(pattern) > best_len:
                best_match = response
                best_len = len(pattern)

        if best_match is None:
            # Default: empty values for GET, empty value for POST/PUT
            if method == "GET":
                return {"values": []}
            return {"value": {}}

        # Support sequence: list of responses, pop first each time
        if isinstance(best_match, list):
            if len(best_match) > 1:
                return best_match.pop(0)
            return best_match[0]

        return best_match


@pytest.fixture
def mock_client():
    """Fresh MockTripletexClient for each test."""
    return MockTripletexClient()


# ---------------------------------------------------------------------------
# CSV task loader
# ---------------------------------------------------------------------------

def load_tasks() -> list[dict]:
    """Load tasks.csv, skip empty/summary rows."""
    tasks = []
    with open(TASKS_CSV) as f:
        reader = csv.DictReader(f)
        for row in reader:
            prompt = (row.get("prompt") or "").strip()
            task_type = (row.get("task_type") or "").strip()
            if prompt and task_type:
                tasks.append(row)
    return tasks


def parse_expected_workflows(task_type: str) -> list[str]:
    """Parse task_type column into list of expected workflow names.

    Examples:
        "create_customer" -> ["create_customer"]
        "create_customer+create_invoice" -> ["create_customer", "create_invoice"]
        "register_payroll(fallback)" -> ["register_payroll"]  # we WANT this workflow
        "create_customer(supplier)" -> ["create_customer"]
        "fallback(dimensions)+create_voucher" -> ["create_voucher"]  # skip pure fallback
        "create_department x3" -> ["create_department"]
    """
    parts = task_type.split("+")
    workflows = []
    for part in parts:
        part = part.strip()

        # Handle "x3" multiplier notation
        part = re.sub(r"\s*x\d+$", "", part)

        if "(" in part:
            base = part.split("(")[0].strip()
            inner = part.split("(")[1].rstrip(")").strip()
            if base == "fallback":
                # "fallback(dimensions)" — no dedicated workflow, skip
                continue
            else:
                # "register_payroll(fallback)" → we want register_payroll
                # "create_customer(supplier)" → we want create_customer
                workflows.append(base)
        else:
            if part == "fallback":
                continue
            workflows.append(part)

    return workflows


# ---------------------------------------------------------------------------
# Response factories — realistic Tripletex-shaped JSON
# ---------------------------------------------------------------------------

def make_employee(id=1, first_name="Test", last_name="User", email="test@example.org", **kwargs):
    emp = {
        "id": id,
        "version": 1,
        "firstName": first_name,
        "lastName": last_name,
        "email": email,
        "displayName": f"{first_name} {last_name}",
        "dateOfBirth": kwargs.get("dateOfBirth"),
        "employments": kwargs.get("employments", []),
        "department": {"id": kwargs.get("department_id", 100)},
    }
    emp.update(kwargs)
    return emp


def make_customer(id=1, name="Test AS", org_number="123456789", **kwargs):
    cust = {
        "id": id,
        "version": 1,
        "name": name,
        "organizationNumber": org_number,
        "isCustomer": True,
        "isSupplier": False,
    }
    cust.update(kwargs)
    return cust


def make_invoice(id=1, amount=10000, amount_outstanding=None, **kwargs):
    inv = {
        "id": id,
        "version": 1,
        "amount": amount,
        "amountOutstanding": amount_outstanding if amount_outstanding is not None else amount,
        "isCreditNote": False,
        "isCredited": False,
    }
    inv.update(kwargs)
    return inv


def make_account(id=1, number=1920, name="Bank", version=1):
    return {"id": id, "version": version, "number": number, "name": name}


def make_department(id=100, name="General"):
    return {"id": id, "name": name}


def make_vat_types():
    """Standard Norwegian VAT types."""
    return [
        {"id": 3, "name": "Utgående mva, høy sats", "number": "3", "percentage": 25, "typeOfVat": "OUTGOING"},
        {"id": 31, "name": "Utgående mva, middels sats", "number": "31", "percentage": 15, "typeOfVat": "OUTGOING"},
        {"id": 5, "name": "Ingen utgående avgift", "number": "5", "percentage": 0, "typeOfVat": "OUTGOING"},
        {"id": 21, "name": "Inngående mva, høy sats", "number": "21", "percentage": 25, "typeOfVat": "INCOMING"},
        {"id": 6, "name": "Ingen avgiftsbehandling", "number": "0", "percentage": 0},
    ]


def make_salary_type(id=10, name="Fastlønn", number="111"):
    return {"id": id, "name": name, "number": number}


def make_employment(id=1, employee_id=1, start_date="2026-01-01"):
    return {
        "id": id,
        "employee": {"id": employee_id},
        "startDate": start_date,
    }


def make_payment_type(id=1, description="Bankoverføring"):
    return {"id": id, "description": description}
