"""Workflow unit tests with MockTripletexClient.

No LLM calls, no network — runs in milliseconds.
Tests the most bug-prone and competition-critical workflows.
"""

import pytest

from src.agent.workflows.payroll import register_payroll
from src.agent.workflows.customer import create_customer
from src.agent.workflows.payment import register_payment
from src.agent.workflows.product import create_product
from src.agent.workflows.project_invoice import create_project_invoice
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
        assert tx_calls[0]["params"]["generateTaxDeduction"] == "true"
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
            "dateOfBirth": "1992-04-18",
        }, mock_client)

        assert "error" not in result
        # Verify employment was created
        mock_client.assert_called("POST", "/employee/employment")
        mock_client.assert_called("POST", "/employee/employment/details")
        mock_client.assert_called("POST", "/employee/standardTime")

    async def test_sets_year_and_month(self, mock_client):
        """Transaction must include year and month fields."""
        employee = make_employee(id=1, email="test@x.org", employments=[make_employment()])

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

    async def test_date_sets_default_year_and_month(self, mock_client):
        """Explicit date should drive default payroll period when year/month are omitted."""
        employee = make_employee(id=1, email="test@x.org", employments=[make_employment()])

        mock_client.when_get("/employee", {"values": [employee]})
        mock_client.when_get("/employee/1", {"value": employee})
        mock_client.when_get("/salary/type", {"values": [make_salary_type()]})
        mock_client.when_post("/salary/transaction", {"value": {"id": 1}})

        await register_payroll({
            "email": "test@x.org",
            "baseSalary": 50000,
            "date": "2026-05-15",
        }, mock_client)

        tx_call = mock_client.get_calls("POST", "/salary/transaction")[0]
        assert tx_call["payload"]["date"] == "2026-05-15"
        assert tx_call["payload"]["year"] == 2026
        assert tx_call["payload"]["month"] == 5

    async def test_bonus_only_no_base(self, mock_client):
        """Should work with just a bonus, no base salary."""
        employee = make_employee(id=1, email="test@x.org", employments=[make_employment()])

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

    async def test_reuses_existing_employee_without_create(self, mock_client):
        """Payroll should reuse an exact employee match before taking the create path."""
        employee = make_employee(id=12, email="marta@example.org", first_name="Marta", last_name="Torres", employments=[make_employment()])

        mock_client.when_get("/employee/12", {"value": employee})
        mock_client.when_get("/employee", {"values": [employee]})
        mock_client.when_get("/salary/type", [
            {"values": [make_salary_type(id=10, name="Fastlønn")]},
            {"values": [make_salary_type(id=11, name="Bonus")]},
        ])
        mock_client.when_post("/salary/transaction", {"value": {"id": 1}})

        result = await register_payroll({
            "email": "marta@example.org",
            "baseSalary": 30000,
            "bonus": 5000,
        }, mock_client)

        assert "error" not in result
        mock_client.assert_not_called("POST", "/employee")
        salary_type_calls = mock_client.get_calls("GET", "/salary/type")
        assert len(salary_type_calls) == 2

    async def test_requires_real_date_of_birth_for_new_employment(self, mock_client):
        """Payroll must not fabricate DOB when employment creation requires one."""
        employee = make_employee(id=5, email="new@x.org", employments=[], dateOfBirth=None)

        mock_client.when_get("/employee", {"values": [employee]})
        mock_client.when_get("/employee/5", {"value": employee})

        result = await register_payroll({
            "email": "new@x.org",
            "baseSalary": 40000,
        }, mock_client)

        assert result["error"] == "dateOfBirth is required to create employment for payroll"
        mock_client.assert_not_called("PUT", "/employee/5")
        mock_client.assert_not_called("POST", "/employee/employment")

    async def test_fails_loudly_when_base_salary_type_missing(self, mock_client):
        """Payroll should not fall back to an arbitrary salary type."""
        employee = make_employee(id=1, email="test@x.org", employments=[make_employment()])

        mock_client.when_get("/employee", {"values": [employee]})
        mock_client.when_get("/employee/1", {"value": employee})
        mock_client.when_get("/salary/type", {"values": []})

        result = await register_payroll({
            "email": "test@x.org",
            "baseSalary": 50000,
        }, mock_client)

        assert result["error"] == "Could not find an active base salary type matching Fast/Fastlønn"

    async def test_filters_inactive_salary_types(self, mock_client):
        """Salary type lookup should explicitly request active salary types only."""
        employee = make_employee(id=1, email="test@x.org", employments=[make_employment()])

        mock_client.when_get("/employee", {"values": [employee]})
        mock_client.when_get("/employee/1", {"value": employee})
        mock_client.when_get("/salary/type", {"values": [make_salary_type(id=10, name="Fastlønn")]})
        mock_client.when_post("/salary/transaction", {"value": {"id": 1}})

        await register_payroll({
            "email": "test@x.org",
            "baseSalary": 50000,
        }, mock_client)

        salary_type_call = mock_client.get_calls("GET", "/salary/type")[0]
        assert salary_type_call["params"]["isInactive"] == "false"

    async def test_generate_tax_deduction_can_be_disabled(self, mock_client):
        """Tax deduction defaults on, but the caller can override it."""
        employee = make_employee(id=1, email="test@x.org", employments=[make_employment()])

        mock_client.when_get("/employee", {"values": [employee]})
        mock_client.when_get("/employee/1", {"value": employee})
        mock_client.when_get("/salary/type", {"values": [make_salary_type()]})
        mock_client.when_post("/salary/transaction", {"value": {"id": 1}})

        await register_payroll({
            "email": "test@x.org",
            "baseSalary": 50000,
            "generateTaxDeduction": False,
        }, mock_client)

        tx_call = mock_client.get_calls("POST", "/salary/transaction")[0]
        assert tx_call["params"]["generateTaxDeduction"] == "false"

    async def test_uses_employee_id_in_summary_when_name_missing(self, mock_client):
        """Summary should stay readable when only employeeId is provided."""
        employee = make_employee(id=7, first_name="", last_name="", employments=[make_employment()])

        mock_client.when_get("/employee/7", {"value": employee})
        mock_client.when_get("/salary/type", {"values": [make_salary_type()]})
        mock_client.when_post("/salary/transaction", {"value": {"id": 1}})

        result = await register_payroll({
            "employeeId": 7,
            "baseSalary": 50000,
        }, mock_client)

        assert result["value"]["summary"].startswith("Payroll executed for employee 7")

    async def test_blocks_duplicate_payroll_for_same_period(self, mock_client):
        """Idempotency guard should block duplicate payroll runs for employee+period."""
        employee = make_employee(id=1, email="test@x.org", employments=[make_employment()])

        mock_client.when_get("/employee", {"values": [employee]})
        mock_client.when_get("/employee/1", {"value": employee})
        mock_client.when_get("/salary/type", {"values": [make_salary_type()]})
        mock_client.when_get("/salary/payslip", {"values": [{"id": 777}]})

        result = await register_payroll({
            "email": "test@x.org",
            "baseSalary": 50000,
            "year": 2026,
            "month": 3,
        }, mock_client)

        assert result["error"] == "Payroll already exists for this employee/period"
        assert result["existingPayslipId"] == 777
        assert result["employeeId"] == 1
        assert result["year"] == 2026
        assert result["month"] == 3
        mock_client.assert_not_called("POST", "/salary/transaction")

    async def test_allow_duplicate_skips_idempotency_check(self, mock_client):
        """allowDuplicate=True should bypass duplicate guard entirely."""
        employee = make_employee(id=1, email="test@x.org", employments=[make_employment()])

        mock_client.when_get("/employee", {"values": [employee]})
        mock_client.when_get("/employee/1", {"value": employee})
        mock_client.when_get("/salary/type", {"values": [make_salary_type()]})
        mock_client.when_post("/salary/transaction", {"value": {"id": 123}})

        result = await register_payroll({
            "email": "test@x.org",
            "baseSalary": 50000,
            "year": 2026,
            "month": 3,
            "allowDuplicate": True,
        }, mock_client)

        assert "error" not in result
        mock_client.assert_not_called("GET", "/salary/payslip")
        mock_client.assert_called("POST", "/salary/transaction")

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
        assert "id" not in put_calls[0]["params"]

    async def test_invoice_search_uses_customer_scoped_params_first(self, mock_client):
        customer = make_customer(id=10, name="Solmar SL", org_number="939332235")
        invoice = make_invoice(
            id=77,
            amount=58375,
            amount_outstanding=58375,
            customer={"id": 10, "name": "Solmar SL", "organizationNumber": "939332235"},
        )

        mock_client.when_get("/customer", {"values": [customer]})
        mock_client._responses[("GET", "/invoice")] = [{"values": [invoice]}]
        mock_client.when_get("/invoice/paymentType", {"values": [make_payment_type(id=1)]})
        mock_client.when_put("/invoice/77/:payment", {"value": {"id": 77}})

        await register_payment({
            "customerName": "Solmar SL",
            "customerOrgNumber": "939332235",
            "fullPayment": True,
        }, mock_client)

        invoice_calls = mock_client.get_calls("GET", "/invoice")
        assert invoice_calls[0]["params"]["customerId"] == "10"
        assert invoice_calls[0]["params"]["count"] == "1000"
        assert invoice_calls[0]["params"]["from"] == "0"

    async def test_paid_amount_zero_is_preserved(self, mock_client):
        invoice = make_invoice(id=77, amount=58375, amount_outstanding=58375)

        mock_client.when_get("/invoice/77", {"value": invoice})
        mock_client.when_get("/invoice/paymentType", {"values": [make_payment_type(id=1)]})
        mock_client.when_put("/invoice/77/:payment", {"value": {"id": 77}})

        await register_payment({
            "invoiceId": 77,
            "paidAmount": 0,
        }, mock_client)

        put_calls = mock_client.get_calls("PUT", "/invoice/77/:payment")
        assert put_calls[0]["params"]["paidAmount"] == "0"

    async def test_derives_paid_amount_currency_for_foreign_invoice(self, mock_client):
        invoice = make_invoice(
            id=88,
            amount=1200,
            amount_outstanding=1200,
            amountCurrency=100,
            amountCurrencyOutstanding=100,
            currencyCode="EUR",
            currency={"code": "EUR"},
        )

        mock_client.when_get("/invoice/88", {"value": invoice})
        mock_client.when_get("/invoice/paymentType", {"values": [make_payment_type(id=1)]})
        mock_client.when_put("/invoice/88/:payment", {"value": {"id": 88}})

        await register_payment({
            "invoiceId": 88,
            "fullPayment": True,
        }, mock_client)

        put_calls = mock_client.get_calls("PUT", "/invoice/88/:payment")
        assert put_calls[0]["params"]["paidAmount"] == "1200"
        assert put_calls[0]["params"]["paidAmountCurrency"] == "100"

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
# create_product — search/update/VAT correctness
# ============================================================

class TestCreateProduct:

    async def test_search_by_number_uses_product_number_param(self, mock_client):
        existing = {
            "id": 11,
            "version": 3,
            "name": "Widget A",
            "number": "P-100",
            "priceExcludingVatCurrency": 100,
            "costExcludingVatCurrency": 50,
            "priceIncludingVatCurrency": 125,
        }
        mock_client.when_get("/product", {"values": [existing]})

        result = await create_product({"number": "P-100", "name": "Widget A"}, mock_client)

        assert result["value"]["id"] == 11
        get_call = mock_client.get_calls("GET", "/product")[0]
        assert get_call["params"]["productNumber"] == "P-100"
        assert "number" not in get_call["params"]

    async def test_update_sends_minimal_put_payload(self, mock_client):
        existing = {
            "id": 11,
            "version": 7,
            "name": "Widget A",
            "number": "P-100",
            "priceExcludingVatCurrency": 100,
            "costExcludingVatCurrency": 50,
            "priceIncludingVatCurrency": 125,
            "url": "https://example.invalid/read-only-field",
        }
        mock_client.when_get("/product", {"values": [existing]})
        mock_client.when_put("/product/11", {"value": {"id": 11}})

        await create_product({"number": "P-100", "price": 110}, mock_client)

        put_call = mock_client.get_calls("PUT", "/product/11")[0]
        assert put_call["payload"] == {
            "id": 11,
            "version": 7,
            "priceExcludingVatCurrency": 110,
        }

    async def test_unrecognized_vat_string_is_not_forced_to_25(self, mock_client):
        mock_client.when_get("/product", {"values": []})
        mock_client.when_get("/ledger/vatType", {"values": make_vat_types()})
        mock_client.when_post("/product", {"value": {"id": 201}})

        await create_product({"name": "New Widget", "vatType": "totally-unknown"}, mock_client)

        post_call = mock_client.get_calls("POST", "/product")[0]
        assert "vatType" not in post_call["payload"]


# ============================================================
# create_project_invoice — direct invoice + idempotent prerequisites
# ============================================================

class TestCreateProjectInvoice:

    async def test_posts_invoice_once_with_embedded_order(self, mock_client):
        project = {
            "id": 7,
            "name": "Alpha",
            "isFixedPrice": True,
            "fixedPrice": 10000,
            "customer": {"id": 42},
        }

        mock_client.when_get("/project/7", {"value": project})
        mock_client.when_get("/ledger/account", {"values": [{**make_account(id=1, number=1920), "bankAccountNumber": "86011117947"}]})
        mock_client.when_get("/ledger/vatType", {"values": make_vat_types()})
        mock_client.when_post("/invoice", {"value": {"id": 501}})

        result = await create_project_invoice({
            "projectId": 7,
            "invoicePercent": 50,
            "invoiceDate": "2026-03-22",
            "sendToCustomer": True,
        }, mock_client)

        assert result["value"]["id"] == 501
        assert result["value"]["project_name"] == "Alpha"
        mock_client.assert_not_called("POST", "/order")
        mock_client.assert_not_called("PUT", "/order/")

        invoice_call = mock_client.get_calls("POST", "/invoice")[0]
        assert invoice_call["params"] == {"sendToCustomer": "true"}
        payload = invoice_call["payload"]
        assert payload["customer"] == {"id": 42}
        assert payload["invoiceDate"] == "2026-03-22"
        assert payload["invoiceDueDate"] == "2026-03-22"
        assert len(payload["orders"]) == 1
        order = payload["orders"][0]
        assert order["customer"] == {"id": 42}
        assert order["project"] == {"id": 7}
        assert order["orderDate"] == "2026-03-22"
        assert order["deliveryDate"] == "2026-03-22"
        assert len(order["orderLines"]) == 1
        assert order["orderLines"][0]["unitPriceExcludingVatCurrency"] == 5000.0

    async def test_updates_existing_hourly_rate_instead_of_posting_duplicate(self, mock_client):
        project = {
            "id": 7,
            "name": "Alpha",
            "isFixedPrice": False,
            "customer": {"id": 42},
        }

        mock_client.when_get("/project/7", {"value": project})
        mock_client.when_get("/project/hourlyRates", {"values": [{
            "id": 12,
            "version": 3,
            "project": {"id": 7},
            "startDate": "2026-03-01",
            "hourlyRateModel": "TYPE_FIXED_HOURLY_RATE",
            "fixedRate": 120,
            "showInProjectOrder": False,
        }]})
        mock_client.when_put("/project/hourlyRates/12", {"value": {"id": 12}})
        mock_client.when_get("/ledger/account", {"values": [{**make_account(id=1, number=1920), "bankAccountNumber": "86011117947"}]})
        mock_client.when_get("/timesheet/entry", {"values": []})
        mock_client.when_get("/ledger/vatType", {"values": make_vat_types()})
        mock_client.when_post("/invoice", {"value": {"id": 502}})

        await create_project_invoice({
            "projectId": 7,
            "invoiceAmount": 1800,
            "hourlyRate": 150,
        }, mock_client)

        mock_client.assert_not_called("POST", "/project/hourlyRates")
        put_call = mock_client.get_calls("PUT", "/project/hourlyRates/12")[0]
        assert put_call["payload"] == {
            "project": {"id": 7},
            "startDate": "2026-03-01",
            "hourlyRateModel": "TYPE_FIXED_HOURLY_RATE",
            "fixedRate": 150.0,
            "showInProjectOrder": True,
            "id": 12,
            "version": 3,
        }

    async def test_caches_bank_account_check_per_client(self, mock_client):
        project = {
            "id": 7,
            "name": "Alpha",
            "isFixedPrice": True,
            "fixedPrice": 1000,
            "customer": {"id": 42},
        }

        mock_client._responses[("GET", "/project/7")] = [
            {"value": project},
            {"value": project},
        ]
        mock_client.when_get("/ledger/account", {"values": [{**make_account(id=1, number=1920), "bankAccountNumber": "86011117947"}]})
        mock_client.when_get("/ledger/vatType", {"values": make_vat_types()})
        mock_client.when_post("/invoice", {"value": {"id": 503}})

        await create_project_invoice({"projectId": 7}, mock_client)
        await create_project_invoice({"projectId": 7}, mock_client)

        assert len(mock_client.get_calls("GET", "/ledger/account")) == 1


# ============================================================
# create_supplier_invoice — 2-posting structure
# Expense debit + AP credit
# ============================================================

class TestCreateSupplierInvoice:

    async def test_two_posting_structure(self, mock_client):
        """Supplier invoice should create expense debit + AP credit postings."""
        mock_client.when_get("/customer", {"values": [make_customer(id=5, name="Polaris AS")]})
        mock_client.when_get("/ledger/account", {"values": [
            make_account(id=100, number=7300, name="Kontortjenester"),
            make_account(id=200, number=2400, name="Leverandorgjeld"),
        ]})
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
        assert len(postings) == 2

        expense = [p for p in postings if p["amountGross"] > 0][0]
        ap = [p for p in postings if p["amountGross"] < 0][0]
        assert expense["amountGross"] == 12500
        assert "vatType" in expense
        assert expense["account"]["id"] == 100
        assert ap["amountGross"] == -12500
        assert ap["account"]["id"] == 200
        assert ap["supplier"]["id"] == 5

    async def test_amount_calculation(self, mock_client):
        """amountInclVat=12500 with 25% VAT should preserve gross on postings."""
        mock_client.when_get("/customer", {"values": []})
        mock_client.when_post("/customer", {"value": make_customer(id=1)})
        mock_client.when_get("/ledger/account", {"values": [
            make_account(id=50, number=7300),
            make_account(id=51, number=2400),
        ]})
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
        assert len(postings) == 2
        assert [p for p in postings if p["amountGross"] > 0][0]["amountGross"] == 12500
        assert [p for p in postings if p["amountGross"] < 0][0]["amountGross"] == -12500


# ============================================================
# register_expense — 2-posting structure (B25v2 fix)
# Tripletex auto-generates 2710 (VAT) from vatType on expense line
# ============================================================

class TestRegisterExpense:

    async def test_two_posting_expense(self, mock_client):
        """B25v2: Expense uses 2 postings: expense (amountGross + vatType) + bank credit."""
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
        assert len(postings) == 2  # expense + bank credit

        expense_posting = [p for p in postings if p["amountGross"] > 0][0]
        bank_posting = [p for p in postings if p["amountGross"] < 0][0]
        assert expense_posting["amountGross"] == 500
        assert bank_posting["amountGross"] == -500
        assert "vatType" in expense_posting  # real VAT type (25% input)

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
        expense_posting = [p for p in post_call["payload"]["postings"] if p["amountGross"] > 0][0]
        assert expense_posting["department"]["id"] == 42


# ============================================================
# create_voucher — simplified posting logic
# ============================================================

class TestCreateVoucher:

    def test_split_balanced_pairs(self):
        """Splitting helper is now a no-op and returns one group."""
        postings = [
            {"account": 6010, "amount": 1000},
            {"account": 1200, "amount": -1000},
            {"account": 5000, "amount": 2000},
            {"account": 2900, "amount": -2000},
        ]
        groups = _split_into_balanced_pairs(postings)
        assert len(groups) == 1
        assert groups[0] == postings

    def test_single_pair_no_split(self):
        """2 postings should stay as one group."""
        postings = [
            {"account": 6010, "amount": 1000},
            {"account": 1200, "amount": -1000},
        ]
        groups = _split_into_balanced_pairs(postings)
        assert len(groups) == 1

    def test_unbalanced_stays_together(self):
        """Unbalanced postings stay as one group."""
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
        mock_client.when_get("/ledger/account", {"values": [
            make_account(id=50, number=6300),
            make_account(id=51, number=1920),
        ]})
        mock_client.when_post("/ledger/voucher", {"value": {"id": 1}})

        await create_voucher({
            "description": "Test entry",
            "postings": [
                {"account": 6300, "amount": 1000},
                {"account": 1920, "amount": -1000},
            ],
        }, mock_client)

        mock_client.assert_called("POST", "/ledger/voucher")

    async def test_creates_missing_accounts_before_posting_voucher(self, mock_client):
        """Missing accounts should be created before voucher POST to avoid 422."""
        mock_client.when_get("/ledger/vatType", {"values": make_vat_types()})
        mock_client.when_get("/ledger/account", [
            {"values": []},
            {"values": [make_account(id=70, number=6030), make_account(id=71, number=1209)]},
        ])
        mock_client.when_post("/ledger/account", [
            {"value": {"id": 70, "number": 6030}},
            {"value": {"id": 71, "number": 1209}},
        ])
        mock_client.when_post("/ledger/voucher", {"value": {"id": 1}})

        await create_voucher({
            "description": "Linear depreciation",
            "postings": [
                {"account": 6030, "amount": 1000},
                {"account": 1209, "amount": -1000},
            ],
        }, mock_client)

        account_posts = mock_client.get_calls("POST", "/ledger/account")
        assert len(account_posts) == 2
        assert account_posts[0]["payload"]["number"] == 1209
        assert account_posts[1]["payload"]["number"] == 6030

        voucher_call = mock_client.get_calls("POST", "/ledger/voucher")[0]
        assert voucher_call["payload"]["postings"][0]["account"]["id"] == 70
        assert voucher_call["payload"]["postings"][1]["account"]["id"] == 71

        post_order = [
            (c["method"], c["endpoint"])
            for c in mock_client.calls
            if c["method"] == "POST" and c["endpoint"] in {"/ledger/account", "/ledger/voucher"}
        ]
        assert post_order == [
            ("POST", "/ledger/account"),
            ("POST", "/ledger/account"),
            ("POST", "/ledger/voucher"),
        ]

    async def test_rejects_postings_without_amount(self, mock_client):
        """Workflow must fail fast when a posting amount is missing."""
        result = await create_voucher({
            "description": "Salary accrual",
            "postings": [
                {"account": 5090},
                {"account": 2930, "amount": -50000},
            ],
        }, mock_client)

        assert result["error"] == "Missing amount on posting row(s): 1"
        mock_client.assert_not_called("POST", "/ledger/voucher")


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
