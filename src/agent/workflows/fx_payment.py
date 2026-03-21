"""Foreign currency payment + exchange difference (disagio/agio) workflow.

Handles: find/create invoice in foreign currency → register payment with
paidAmount (NOK) + paidAmountCurrency (foreign) → post exchange
difference voucher.

Accounts:
- 8060 Valutadifferanse (exchange loss = disagio, debit)
- 8060 reversed for gain (agio, credit)
- 1500 Kundefordringer (accounts receivable — balancing entry)
"""

import logging
from datetime import date

from ..tripletex import TripletexClient
from .customer import create_customer
from .invoice import _ensure_bank_account, _ensure_customer, _lookup_vat_type_by_rate
from .payment import _find_invoice, _find_payment_type_id
from .voucher import create_voucher

logger = logging.getLogger("agent.workflows.fx_payment")


async def _lookup_currency_id(code: str, client: TripletexClient) -> int | None:
    """Resolve currency code (e.g. 'EUR') to Tripletex currency ID."""
    result = await client.get("/currency", params={"code": code, "count": "1"})
    currencies = result.get("values", [])
    for c in currencies:
        if c.get("code", "").upper() == code.upper():
            logger.info("Resolved currency %s → id=%d", code, c["id"])
            return c["id"]
    # Fallback: search without code filter (some API versions don't support code param)
    if not currencies:
        result = await client.get("/currency", params={"count": "100"})
        for c in result.get("values", []):
            if c.get("code", "").upper() == code.upper():
                logger.info("Resolved currency %s → id=%d (full scan)", code, c["id"])
                return c["id"]
    logger.error("Could not resolve currency code: %s", code)
    return None


async def register_fx_payment(data: dict, client: TripletexClient) -> dict:
    """Register payment on a foreign-currency invoice and post exchange difference.

    Expected data fields:
    - customerName / customerOrgNumber: to find the invoice
    - invoiceId / invoiceNumber: direct invoice reference
    - invoiceAmountForeign: original invoice amount in foreign currency
    - invoiceRate: exchange rate at invoice time (e.g. 11.69 NOK/EUR)
    - paymentRate: exchange rate at payment time (e.g. 11.28 NOK/EUR)
    - paymentAmountForeign: amount paid in foreign currency (defaults to invoiceAmountForeign)
    - currency: currency code (e.g. EUR, USD, GBP)
    - paymentDate: date of payment
    - description: optional description
    """
    today = date.today().isoformat()

    # Extract FX data
    invoice_amount_fx = data.get("invoiceAmountForeign") or data.get("amountForeign") or data.get("amountCurrency")
    invoice_rate = data.get("invoiceRate") or data.get("originalRate") or data.get("rate")
    payment_rate = data.get("paymentRate") or data.get("paidRate") or data.get("newRate")
    payment_amount_fx = data.get("paymentAmountForeign") or invoice_amount_fx
    currency = data.get("currency", "EUR")
    payment_date = data.get("paymentDate") or data.get("date") or today
    description = data.get("description", "")

    if not invoice_amount_fx or not invoice_rate or not payment_rate:
        return {"error": "Need invoiceAmountForeign, invoiceRate, and paymentRate"}

    invoice_amount_fx = float(invoice_amount_fx)
    payment_amount_fx = float(payment_amount_fx)
    invoice_rate = float(invoice_rate)
    payment_rate = float(payment_rate)

    # Calculate amounts
    invoice_nok = round(invoice_amount_fx * invoice_rate, 2)
    payment_nok = round(payment_amount_fx * payment_rate, 2)
    exchange_diff = round(invoice_nok - payment_nok, 2)  # positive = loss (disagio)

    logger.info(
        "FX payment: %.2f %s × %.4f = %.2f NOK (invoice) vs × %.4f = %.2f NOK (payment), diff = %.2f",
        invoice_amount_fx, currency, invoice_rate, invoice_nok,
        payment_rate, payment_nok, exchange_diff,
    )

    # Step 0: Resolve foreign currency ID (needed for invoice creation)
    currency_id = await _lookup_currency_id(currency, client)
    if not currency_id:
        return {"error": f"Could not resolve currency: {currency}"}

    # Step 1: Find the invoice
    invoice = await _find_invoice(data, client)

    # Step 1b: If no invoice found, create one IN THE FOREIGN CURRENCY
    if not invoice:
        logger.info("No existing invoice found — creating FX invoice in %s", currency)
        await _ensure_bank_account(client)

        customer_id = await _ensure_customer(data, client)
        if not customer_id:
            return {"error": "Could not resolve customer for FX invoice"}

        # Resolve 0% VAT for FX invoices (foreign trade typically VAT-exempt)
        vat_id = await _lookup_vat_type_by_rate(0, client)
        order_lines = [{
            "description": description or f"Invoice {currency}",
            "count": 1,
            "unitPriceExcludingVatCurrency": invoice_amount_fx,  # Foreign currency amount!
        }]
        if vat_id:
            order_lines[0]["vatType"] = {"id": vat_id}

        order_payload = {
            "customer": {"id": customer_id},
            "orderDate": today,
            "deliveryDate": today,
            "currency": {"id": currency_id},  # Set currency on the order!
            "orderLines": order_lines,
        }
        logger.info("Creating FX order in %s (currency_id=%d, amount=%.2f %s)", currency, currency_id, invoice_amount_fx, currency)
        order_result = await client.post("/order", order_payload)
        order_id = order_result.get("value", {}).get("id")
        if not order_id:
            return {"error": f"Failed to create FX order: {order_result}"}

        inv_result = await client.put(f"/order/{order_id}/:invoice", params={"id": str(order_id), "invoiceDate": today})
        invoice = inv_result.get("value")
        if not invoice:
            return {"error": f"Failed to create FX invoice: {inv_result}"}
        logger.info("Created FX invoice %d in %s", invoice["id"], currency)

    invoice_id = invoice["id"]
    customer_id = (invoice.get("customer") or {}).get("id")
    logger.info("Found invoice %d for FX payment (customer=%s)", invoice_id, customer_id)

    # Step 2: Register payment with BOTH paidAmount (NOK) and paidAmountCurrency (foreign)
    # This fully settles the invoice in both currencies
    payment_type_id = data.get("paymentTypeId") or await _find_payment_type_id(client)
    if not payment_type_id:
        return {"error": "No payment type available"}

    params = {
        "id": str(invoice_id),
        "paymentDate": payment_date,
        "paymentTypeId": str(payment_type_id),
        "paidAmount": str(payment_nok),
        "paidAmountCurrency": str(payment_amount_fx),  # Settle full foreign currency amount
    }

    logger.info("Registering FX payment on invoice %d: %.2f NOK + %.2f %s", invoice_id, payment_nok, payment_amount_fx, currency)
    payment_result = await client.put(f"/invoice/{invoice_id}/:payment", params=params)

    payment_ok = payment_result.get("value", {}).get("id")
    if not payment_ok:
        logger.error("Payment registration failed: %s", payment_result)
        return {"error": f"Payment failed: {payment_result}", "invoice_id": invoice_id}

    result = {
        "value": payment_result.get("value"),
        "invoice_id": invoice_id,
        "payment_nok": payment_nok,
        "exchange_difference": exchange_diff,
    }

    # Step 3: Post exchange difference voucher (if non-zero)
    if abs(exchange_diff) < 0.01:
        logger.info("No exchange difference to post")
        result["message"] = "Payment registered, no exchange difference"
        return result

    if exchange_diff > 0:
        # Loss (disagio): debit 8060 (expense), credit 1500 (AR)
        label = f"Disagio {currency} {description}".strip()
        postings = [
            {"account": 8060, "amountGross": exchange_diff, "description": label},
            {"account": 1500, "amountGross": -exchange_diff, "description": label, "customerId": customer_id},
        ]
        logger.info("Posting disagio (loss): %.2f NOK to 8060", exchange_diff)
    else:
        # Gain (agio): debit 1500 (AR), credit 8060 (income)
        gain = abs(exchange_diff)
        label = f"Agio {currency} {description}".strip()
        postings = [
            {"account": 1500, "amountGross": gain, "description": label, "customerId": customer_id},
            {"account": 8060, "amountGross": -gain, "description": label},
        ]
        logger.info("Posting agio (gain): %.2f NOK to 8060", gain)

    voucher_data = {
        "description": label,
        "date": payment_date,
        "postings": postings,
    }
    voucher_result = await create_voucher(voucher_data, client)
    voucher_id = voucher_result.get("value", {}).get("id")

    if voucher_id:
        logger.info("Exchange difference voucher created: id=%d", voucher_id)
        result["voucher_id"] = voucher_id
        result["message"] = f"Payment + {'disagio' if exchange_diff > 0 else 'agio'} voucher posted"
    else:
        logger.error("Exchange diff voucher failed: %s", voucher_result)
        result["voucher_error"] = voucher_result
        result["_needs_repair"] = (
            f"Payment registered but exchange difference voucher failed. "
            f"Post manually: 8060 {'debit' if exchange_diff > 0 else 'credit'} {abs(exchange_diff)}, "
            f"1500 {'credit' if exchange_diff > 0 else 'debit'} {abs(exchange_diff)}"
        )

    return result
