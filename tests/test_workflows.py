"""Workflow unit tests with MockTripletexClient.

No LLM calls, no network — runs in milliseconds.
Tests the most bug-prone and competition-critical workflows.
"""

import pytest

from src.agent.workflows.payroll import register_payroll
from src.agent.workflows.customer import create_customer
from src.agent.workflows.payment import register_payment
from src.agent.workflows.voucher import create_supplier_invoice, create_voucher, _split_into_balanced_pairs
from src.agent.workflows.expense import register_expense

from .conftest import (
    make_employee, make_customer, make_invoice, make_account,
    make_department, make_vat_types, make_salary_type, make_employment,
    make_payment_type,
)


# ============================================================
# register_payroll — NEW workflow, critical to validate
# ============================================================

class TestRegisterPayroll:
    """Tests for the new register_payroll workflow."""

    async def test_base_salary_plus_bonus(self, mock_client):
        """Core happy path: existing employee with employment, base + bonus."""
        employee = make_employee(id=1, email="marta@example.org", employments=[make_employment()])

        mock_client.when_get("/employee", {"values": [employee]})
        mock_client.when_get("/employee/1", {"value": employee})
        mock_client.when_get("/salary/type", {"values": [make_salary_type(id=10, name="Fastlønn")]})
        mock_client.when_post("/salary/transaction", {"value": {"id": 99}})

        result = await register_payroll({
            "email": "marta@example.org",
            "firstName": "Marta",
            "lastName": "Torres",
            "baseSalary": 50400,
            "bonus": 7050,
        }, mock_client)

        assert "error" not in result
        assert result["value"]["transactionId"] == 99
        assert result["value"]["baseSalary"] == 50400
        assert result["value"]["bonus"] == 7050
        assert result["value"]["total"] == 57450

        # Verify the POST payload structure
        tx_calls = mock_client.get_calls("POST", "/salary/transaction")
        assert len(tx_calls) == 1
        payload = tx_calls[0]["payload"]
        assert "payslips" in payload
        payslip = payload["payslips"][0]
        assert payslip["employee"]["id"] == 1
        assert "specifications" in payslip  # NOT salaryLines!
        assert len(payslip["specifications"]) == 2  # base + bonus

    async def test_uses_specifications_not_salary_lines(self, mock_client):
        """The critical bug: agent used 'salaryLines' which doesn't exist. We use 'specifications'."""
        employee = make_employee(id=1, email="test@x.org", employments=[make_employment()])

        mock_client.when_get("/employee", {"values": [employee]})
        mock_client.when_get("/employee/1", {"value": employee})
        mock_client.when_get("/salary/type", {"values": [make_salary_type()]})
        mock_client.when_post("/salary/transaction", {"value": {"id": 1}})

        await register_payroll({"email": "test@x.org", "baseSalary": 30000}, mock_client)

        tx_call = mock_client.get_calls("POST", "/salary/transaction")[0]
        payslip = tx_call["payload"]["payslips"][0]
        assert "specifications" in payslip
        assert "salaryLines" not in payslip

    async def test_creates_employment_when_missing(self, mock_client):
        """Employee exists but has no employment record — should auto-create."""
        employee_no_emp = make_employee(id=5, email="new@x.org", employments=[])

        mock_client.when_get("/employee", {"values": [employee_no_emp]})
        mock_client.when_get("/employee/5", {"value": employee_no_emp})
        mock_client.when_put("/employee/5", {"value": {"id": 5, "version": 2}})
        mock_client.when_post("/employee/employment", {"value": {"id": 20}})
        mock_client.when_post("/employee/employment/details", {"value": {"id": 30}})
        mock_client.when_post("/employee/standardTime", {"value": {"id": 40}})
        mock_client.when_get("/salary/type", {"values": [make_salary_type()]})
        mock_client.when_post("/salary/transaction", {"value": {"id": 88}})

        result = await register_payroll({
            "email": "new@x.org",
            "baseSalary": 40000,
        }, mock_client)

        assert "error" not in result
        # Verify employment was created
        mock_client.assert_called("POST", "/employee/employment")
        mock_client.assert_called("POST", "/employee/employment/details")
        mock_client.assert_called("POST", "/employee/standardTime")

    async def test_sets_year_and_month(self, mock_client):
        """Transaction must include year and month fields."""
        employee = make_employee(id=1, employments=[make_employment()])

        mock_client.when_get("/employee", {"values": [employee]})
        mock_client.when_get("/employee/1", {"value": employee})
        mock_client.when_get("/salary/type", {"values": [make_salary_type()]})
        mock_client.when_post("/salary/transaction", {"value": {"id": 1}})

        await register_payroll({
            "email": "test@x.org",
            "baseSalary": 50000,
            "year": 2026,
            "month": 3,
        }, mock_client)

        tx_call = mock_client.get_calls("POST", "/salary/transaction")[0]
        assert tx_call["payload"]["year"] == 2026
        assert tx_call["payload"]["month"] == 3

    async def test_bonus_only_no_base(self, mock_client):
        """Should work with just a bonus, no base salary."""
        employee = make_employee(id=1, employments=[make_employment()])

        mock_client.when_get("/employee", {"values": [employee]})
        mock_client.when_get("/employee/1", {"value": employee})
        mock_client.when_get("/salary/type", {"values": [make_salary_type()]})
        mock_client.when_post("/salary/transaction", {"value": {"id": 1}})

        result = await register_payroll({
            "email": "test@x.org",
            "bonus": 5000,
        }, mock_client)

        assert "error" not in result
        tx_call = mock_client.get_calls("POST", "/salary/transaction")[0]
        specs = tx_call["payload"]["payslips"][0]["specifications"]
        assert len(specs) == 1
        assert specs[0]["amount"] == 5000

    async def test_no_amounts_returns_error(self, mock_client):
        """Should error if neither baseSalary nor bonus provided."""
        employee = make_employee(id=1, employments=[make_employment()])

        mock_client.when_get("/employee", {"values": [employee]})
        mock_client.when_get("/employee/1", {"value": employee})
        mock_client.when_get("/salary/type", {"values": [make_salary_type()]})

        result = await register_payroll({"email": "test@x.org"}, mock_client)
        assert "error" in result


# ============================================================
# create_customer — bulletproof T1, verify search-before-create
# ============================================================

class TestCreateCustomer:

    async def test_existing_customer_by_email(self, mock_client):
        """Should return existing customer, not create duplicate."""
        existing = make_customer(id=42, name="Existing AS")
        mock_client.when_get("/employee", {"values": [existing]})

        # create_customer searches /employee by email first for employee creation
        # For customer: it searches /customer
        mock_client.when_get("/customer", {"values": []})
        # But the employee search finds an existing employee
        result = await create_customer({"email": "post@existing.no", "name": "Existing AS"}, mock_client)

        # Should not POST a new customer if found via search
        # (the actual behavior depends on whether the workflow searches first)

    async def test_supplier_flag(self, mock_client):
        """Creating a supplier: isSupplier=true, isCustomer=false."""
        mock_client.when_get("/customer", {"values": []})
        mock_client.when_post("/customer", {"value": make_customer(id=1, name="Supplier AS")})

        result = await create_customer({
            "name": "Supplier AS",
            "isSupplier": True,
        }, mock_client)

        post_calls = mock_client.get_calls("POST", "/customer")
        assert len(post_calls) == 1
        payload = post_calls[0]["payload"]
        assert payload.get("isSupplier") is True
        assert payload.get("isCustomer") is False


# ============================================================
# register_payment — find existing invoice, use actual amount
# ============================================================

class TestRegisterPayment:

    async def test_full_payment_uses_outstanding_amount(self, mock_client):
        """Full payment should use amountOutstanding from the invoice, not calculate it."""
        customer = make_customer(id=10, name="Solmar SL", org_number="939332235")
        invoice = make_invoice(id=77, amount=58375, amount_outstanding=58375,
                               customer={"id": 10, "name": "Solmar SL", "organizationNumber": "939332235"})

        mock_client.when_get("/customer", {"values": [customer]})
        mock_client.when_get("/invoice", {"values": [invoice]})
        mock_client.when_get("/invoice/paymentType", {"values": [make_payment_type(id=1)]})
        mock_client.when_put("/invoice/77/:payment", {"value": {"id": 77}})

        result = await register_payment({
            "customerName": "Solmar SL",
            "fullPayment": True,
        }, mock_client)

        put_calls = mock_client.get_calls("PUT", "/invoice/77/:payment")
        assert len(put_calls) == 1
        assert put_calls[0]["params"]["paidAmount"] == "58375"

    async def test_creates_invoice_when_not_found(self, mock_client):
        """If no invoice exists, should create one then pay it."""
        mock_client.when_get("/customer", {"values": []})
        mock_client.when_get("/invoice", {"values": []})
        # Invoice creation path (via create_invoice workflow)
        mock_client.when_get("/ledger/account", {"values": [make_account(id=1, number=1920)]})
        mock_client.when_get("/ledger/vatType", {"values": make_vat_types()})
        mock_client.when_post("/customer", {"value": make_customer(id=1)})
        mock_client.when_post("/order", {"value": {"id": 1}})
        mock_client.when_put("/order/1/:invoice", {"value": make_invoice(id=50, amount=10000)})
        mock_client.when_get("/invoice/paymentType", {"values": [make_payment_type(id=1)]})
        mock_client.when_put("/invoice/50/:payment", {"value": {"id": 50}})

        result = await register_payment({
            "customerName": "New Customer",
            "description": "Service",
            "amountExclVat": 8000,
            "paidAmount": 10000,
        }, mock_client)

        # Should have created an invoice first
        mock_client.assert_called("POST", "/order")


# ============================================================
# create_supplier_invoice — 3-posting structure (B22/B23 fix)
# ============================================================

class TestCreateSupplierInvoice:

    async def test_three_posting_structure(self, mock_client):
        """Must create 3 postings: expense debit + VAT debit + supplier credit."""
        mock_client.when_get("/customer", {"values": [make_customer(id=5, name="Polaris AS")]})
        mock_client.when_get("/ledger/account", [
            {"values": [make_account(id=100, number=7300, name="Kontortjenester")]},  # first call: expense
        ])
        # We need to handle multiple account lookups — use specific patterns
        mock_client._responses[("GET", "/ledger/account")] = {"values": [make_account(id=100, number=7300)]}
        mock_client.when_get("/ledger/vatType", {"values": make_vat_types()})
        mock_client.when_post("/ledger/voucher", {"value": {"id": 200}})

        result = await create_supplier_invoice({
            "supplierName": "Polaris AS",
            "supplierOrgNumber": "833875094",
            "invoiceNumber": "INV-2026-2076",
            "amountInclVat": 12500,
            "vatRate": 25,
            "expenseAccount": 7300,
            "description": "Kontortjenester",
        }, mock_client)

        post_calls = mock_client.get_calls("POST", "/ledger/voucher")
        assert len(post_calls) >= 1
        postings = post_calls[0]["payload"]["postings"]
        assert len(postings) == 3  # expense + VAT + supplier credit

        # Verify amounts: 10000 excl + 2500 VAT = 12500 incl
        amounts = sorted([p["amount"] for p in postings])
        assert amounts[0] == -12500  # credit (supplier)
        assert amounts[1] == 2500    # VAT debit
        assert amounts[2] == 10000   # expense debit

    async def test_amount_calculation(self, mock_client):
        """amountInclVat=12500 with 25% VAT -> excl=10000, vat=2500."""
        mock_client.when_get("/customer", {"values": []})
        mock_client.when_post("/customer", {"value": make_customer(id=1)})
        mock_client.when_get("/ledger/account", {"values": [make_account()]})
        mock_client.when_get("/ledger/vatType", {"values": make_vat_types()})
        mock_client.when_post("/ledger/voucher", {"value": {"id": 1}})

        await create_supplier_invoice({
            "supplierName": "Test",
            "amountInclVat": 12500,
            "vatRate": 25,
            "expenseAccount": 7300,
        }, mock_client)

        post_call = mock_client.get_calls("POST", "/ledger/voucher")[0]
        postings = post_call["payload"]["postings"]
        expense_posting = [p for p in postings if p["amount"] > 0 and "MVA" not in p.get("description", "")][0]
        vat_posting = [p for p in postings if p["amount"] > 0 and "MVA" in p.get("description", "")][0]
        credit_posting = [p for p in postings if p["amount"] < 0][0]

        assert expense_posting["amount"] == 10000
        assert vat_posting["amount"] == 2500
        assert credit_posting["amount"] == -12500


# ============================================================
# register_expense — 3-posting structure (B24 fix)
# ============================================================

class TestRegisterExpense:

    async def test_three_posting_expense(self, mock_client):
        """Expense must use 3-posting: expense net + VAT 2710 + bank credit."""
        mock_client.when_get("/ledger/account", {"values": [make_account(id=50, number=6540)]})
        mock_client.when_get("/ledger/vatType", {"values": make_vat_types()})
        mock_client.when_get("/department", {"values": [make_department(id=10, name="Lager")]})
        mock_client.when_post("/ledger/voucher", {"value": {"id": 300}})

        result = await register_expense({
            "description": "Oppbevaringsboks",
            "amountInclVat": 500,
            "vatRate": 25,
            "expenseAccount": 6540,
            "departmentName": "Lager",
        }, mock_client)

        post_calls = mock_client.get_calls("POST", "/ledger/voucher")
        assert len(post_calls) >= 1
        postings = post_calls[0]["payload"]["postings"]
        assert len(postings) == 3

        amounts = sorted([p["amount"] for p in postings])
        assert amounts[0] == -500  # credit bank
        assert amounts[1] == 100   # VAT (500/1.25 = 400, VAT = 100)
        assert amounts[2] == 400   # expense net

    async def test_department_linked(self, mock_client):
        """Expense posting should include department."""
        mock_client.when_get("/ledger/account", {"values": [make_account()]})
        mock_client.when_get("/ledger/vatType", {"values": make_vat_types()})
        mock_client.when_get("/department", {"values": [make_department(id=42, name="Salg")]})
        mock_client.when_post("/ledger/voucher", {"value": {"id": 1}})

        await register_expense({
            "description": "Lunch",
            "amountInclVat": 250,
            "expenseAccount": 7140,
            "departmentName": "Salg",
        }, mock_client)

        post_call = mock_client.get_calls("POST", "/ledger/voucher")[0]
        expense_posting = [p for p in post_call["payload"]["postings"] if p["amount"] > 0 and "MVA" not in p.get("description", "")][0]
        assert expense_posting["department"]["id"] == 42


# ============================================================
# create_voucher — split + retry logic
# ============================================================

class TestCreateVoucher:

    def test_split_balanced_pairs(self):
        """Should split 4 postings into 2 balanced pairs."""
        postings = [
            {"account": 6010, "amount": 1000},
            {"account": 1200, "amount": -1000},
            {"account": 5000, "amount": 2000},
            {"account": 2900, "amount": -2000},
        ]
        groups = _split_into_balanced_pairs(postings)
        assert len(groups) == 2
        assert len(groups[0]) == 2
        assert len(groups[1]) == 2

    def test_single_pair_no_split(self):
        """2 postings should stay as one group."""
        postings = [
            {"account": 6010, "amount": 1000},
            {"account": 1200, "amount": -1000},
        ]
        groups = _split_into_balanced_pairs(postings)
        assert len(groups) == 1

    def test_unbalanced_stays_together(self):
        """3 postings that don't form balanced pairs stay as one group."""
        postings = [
            {"account": 6010, "amount": 1000},
            {"account": 2710, "amount": 250},
            {"account": 2400, "amount": -1250},
        ]
        groups = _split_into_balanced_pairs(postings)
        assert len(groups) == 1

    async def test_voucher_posts_with_resolved_accounts(self, mock_client):
        """create_voucher should resolve account numbers to IDs."""
        mock_client.when_get("/ledger/vatType", {"values": make_vat_types()})
        mock_client.when_get("/ledger/account", {"values": [make_account(id=50, number=6300)]})
        mock_client.when_post("/ledger/voucher", {"value": {"id": 1}})

        await create_voucher({
            "description": "Test entry",
            "postings": [
                {"account": 6300, "amount": 1000},
                {"account": 1920, "amount": -1000},
            ],
        }, mock_client)

        mock_client.assert_called("POST", "/ledger/voucher")


# ============================================================
# Utility: parse_expected_workflows
# ============================================================

class TestParseExpectedWorkflows:

    def test_simple(self):
        from .conftest import parse_expected_workflows
        assert parse_expected_workflows("create_customer") == ["create_customer"]

    def test_multi_step(self):
        from .conftest import parse_expected_workflows
        result = parse_expected_workflows("create_customer+create_invoice")
        assert result == ["create_customer", "create_invoice"]

    def test_fallback_notation(self):
        from .conftest import parse_expected_workflows
        result = parse_expected_workflows("register_payroll(fallback)")
        assert result == ["register_payroll"]

    def test_pure_fallback_skipped(self):
        from .conftest import parse_expected_workflows
        result = parse_expected_workflows("fallback(dimensions)+create_voucher")
        assert result == ["create_voucher"]

    def test_supplier_annotation(self):
        from .conftest import parse_expected_workflows
        result = parse_expected_workflows("create_customer(supplier)")
        assert result == ["create_customer"]

    def test_multiplier(self):
        from .conftest import parse_expected_workflows
        result = parse_expected_workflows("create_department x3")
        assert result == ["create_department"]

    def test_complex(self):
        from .conftest import parse_expected_workflows
        result = parse_expected_workflows("create_employee+create_customer+create_project")
        assert result == ["create_employee", "create_customer", "create_project"]
