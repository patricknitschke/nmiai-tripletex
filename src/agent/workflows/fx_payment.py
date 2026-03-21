"""Foreign currency payment + exchange difference (disagio/agio) workflow.

Handles: find/create invoice in foreign currency → register payment with
paidAmount (NOK) + paidAmountCurrency (foreign) → post exchange
difference voucher.

Accounts:
- 8060 Valutatap (exchange loss = disagio, debit)
- 8160 Valutagevinst (exchange gain = agio, credit)
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
    for c in result.get("values", []):
        if c.get("code", "").upper() == code.upper():
            logger.info("Resolved currency %s → id=%d", code, c["id"])
            return c["id"]
    logger.error("Could not resolve currency code: %s", code)
    return None


async def _get_exchange_rate_nok(currency_id: int, amount: float, rate_date: str, client: TripletexClient) -> float | None:
    """Get NOK equivalent of a foreign currency amount using Tripletex official rates."""
    result = await client.get(
        f"/currency/{currency_id}/exchangeRate",
        params={"amount": str(amount), "date": rate_date},
    )
    nok_amount = result.get("value")
    if nok_amount is not None:
        logger.info("Exchange rate API: %.2f foreign @ %s = %.2f NOK", amount, rate_date, float(nok_amount))
        return round(float(nok_amount), 2)
    logger.error("Exchange rate API failed for currency_id=%d, date=%s: %s", currency_id, rate_date, result)
    return None


async def register_fx_payment(data: dict, client: TripletexClient) -> dict:
    """Register payment on a foreign-currency invoice and post exchange difference.

    Expected data fields:
    - customerName / customerOrgNumber: to find the invoice
    - invoiceId / invoiceNumber: direct invoice reference
    - invoiceAmountForeign: original invoice amount in foreign currency
    - invoiceRate: exchange rate at invoice time (optional — API lookup if omitted)
    - paymentRate: exchange rate at payment time (optional — API lookup if omitted)
    - invoiceDate: date of original invoice (for rate lookup when rates omitted)
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
    invoice_date = data.get("invoiceDate") or today
    description = data.get("description", "")

    if not invoice_amount_fx:
        return {"error": "Need invoiceAmountForeign"}

    invoice_amount_fx = float(invoice_amount_fx)
    payment_amount_fx = float(payment_amount_fx)

    # Step 0: Resolve foreign currency ID
    currency_id = await _lookup_currency_id(currency, client)
    if not currency_id:
        return {"error": f"Could not resolve currency: {currency}"}

    # Calculate NOK amounts — from provided rates or via Tripletex exchange rate API
    if invoice_rate and payment_rate:
        invoice_rate = float(invoice_rate)
        payment_rate = float(payment_rate)
        invoice_nok = round(invoice_amount_fx * invoice_rate, 2)
        payment_nok = round(payment_amount_fx * payment_rate, 2)
        logger.info(
            "FX payment (manual rates): %.2f %s × %.4f = %.2f NOK (invoice) vs × %.4f = %.2f NOK (payment)",
            invoice_amount_fx, currency, invoice_rate, invoice_nok, payment_rate, payment_nok,
        )
    else:
        # Use Tripletex official exchange rates
        logger.info("No rates provided — fetching from Tripletex exchange rate API for %s", currency)
        invoice_nok = await _get_exchange_rate_nok(currency_id, invoice_amount_fx, invoice_date, client)
        payment_nok = await _get_exchange_rate_nok(currency_id, payment_amount_fx, payment_date, client)
        if invoice_nok is None or payment_nok is None:
            return {"error": "Could not fetch exchange rates from API. Provide invoiceRate and paymentRate manually."}

    exchange_diff = round(invoice_nok - payment_nok, 2)  # positive = loss (disagio)
    logger.info("FX payment: invoice %.2f NOK, payment %.2f NOK, diff = %.2f", invoice_nok, payment_nok, exchange_diff)

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
        # Loss (disagio): debit 8060 Valutatap, credit 1500 AR
        label = f"Disagio {currency} {description}".strip()
        postings = [
            {"account": 8060, "amountGross": exchange_diff, "description": label},
            {"account": 1500, "amountGross": -exchange_diff, "description": label, "customerId": customer_id},
        ]
        logger.info("Posting disagio (loss): %.2f NOK — debit 8060, credit 1500", exchange_diff)
    else:
        # Gain (agio): debit 1500 AR, credit 8160 Valutagevinst
        gain = abs(exchange_diff)
        label = f"Agio {currency} {description}".strip()
        postings = [
            {"account": 1500, "amountGross": gain, "description": label, "customerId": customer_id},
            {"account": 8160, "amountGross": -gain, "description": label},
        ]
        logger.info("Posting agio (gain): %.2f NOK — debit 1500, credit 8160", gain)

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
            f"Post manually: {'8060' if exchange_diff > 0 else '8160'} {'debit' if exchange_diff > 0 else 'credit'} {abs(exchange_diff)}, "
            f"1500 {'credit' if exchange_diff > 0 else 'debit'} {abs(exchange_diff)}"
        )

    return result
