"""Foreign currency payment + exchange difference (disagio/agio) workflow.

Handles: find existing foreign-currency invoice -> register payment with
paidAmount (NOK) + paidAmountCurrency (foreign) -> optionally post exchange
difference voucher.

Accounts:
- 8060 Valutatap (exchange loss = disagio, debit)
- 8160 Valutagevinst (exchange gain = agio, credit)
- 1500 Kundefordringer (accounts receivable — balancing entry)
"""

import logging
from datetime import date

from ..tripletex import TripletexClient
from .payment import _find_invoice, _find_payment_type_id
from .voucher import create_voucher

logger = logging.getLogger("agent.workflows.fx_payment")


async def _lookup_currency_id(code: str, client: TripletexClient) -> int | None:
    """Resolve currency code (e.g. 'EUR') to Tripletex currency ID."""
    cache = getattr(client, "_currency_id_cache", None)
    if cache is None:
        cache = {}
        setattr(client, "_currency_id_cache", cache)

    cache_key = code.upper()
    if cache_key in cache:
        return cache[cache_key]

    result = await client.get("/currency", params={"code": code, "count": "1"})
    for c in result.get("values", []):
        if c.get("code", "").upper() == code.upper():
            logger.info("Resolved currency %s → id=%d", code, c["id"])
            cache[cache_key] = c["id"]
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
    """Register payment on an existing foreign-currency invoice.

    Expected data fields:
    - customerName / customerOrgNumber: to find the invoice
    - invoiceId / invoiceNumber: direct invoice reference
    - invoiceAmountForeign: invoice amount in foreign currency (optional if inferable)
    - paymentRate: exchange rate at payment time (optional if paidAmount is provided)
    - paymentAmountForeign: amount paid in foreign currency (defaults to invoice amount)
    - paidAmount: explicit payment amount in NOK
    - currency: currency code (e.g. EUR, USD, GBP)
    - paymentDate: date of payment
    - forceManualFxVoucher: force manual voucher posting even when invoice is fully settled
    - description: optional description
    """
    today = date.today().isoformat()

    invoice = await _find_invoice(data, client, allow_broad_fallback=False)
    if not invoice:
        return {
            "error": (
                "Could not find existing invoice for FX payment. "
                "Provide invoiceId/invoiceNumber or customerOrgNumber/customerName."
            )
        }

    invoice_id = invoice["id"]
    customer_id = (invoice.get("customer") or {}).get("id")

    request_currency = (data.get("currency") or "").upper()
    invoice_currency = (
        (invoice.get("currencyCode") or "")
        or ((invoice.get("currency") or {}).get("code") or "")
    ).upper()
    if invoice_currency and request_currency and invoice_currency != request_currency:
        return {
            "error": (
                f"Currency mismatch: invoice is {invoice_currency}, "
                f"but request specified {request_currency}."
            )
        }

    currency = invoice_currency or request_currency or "EUR"

    def _first_not_none(*vals):
        for v in vals:
            if v is not None:
                return v
        return None

    invoice_amount_fx = _first_not_none(
        data.get("invoiceAmountForeign"),
        data.get("amountForeign"),
        data.get("amountCurrency"),
        invoice.get("amountCurrencyOutstanding"),
        invoice.get("amountCurrencyOutstandingTotal"),
        invoice.get("amountCurrency"),
    )
    if invoice_amount_fx is None:
        return {"error": "Need invoiceAmountForeign or an invoice with amountCurrencyOutstanding"}

    payment_amount_fx = data.get("paymentAmountForeign") or invoice_amount_fx
    payment_rate = data.get("paymentRate") or data.get("paidRate") or data.get("newRate")
    paid_amount_nok = _first_not_none(data.get("paidAmount"), data.get("paymentAmount"))
    payment_date = data.get("paymentDate") or data.get("date") or today
    description = data.get("description", "")

    invoice_amount_fx = float(invoice_amount_fx)
    payment_amount_fx = float(payment_amount_fx)

    invoice_nok_basis = _first_not_none(
        invoice.get("amountOutstanding"),
        invoice.get("amountOutstandingTotal"),
        invoice.get("amount"),
    )
    if invoice_nok_basis is None:
        return {"error": f"Invoice {invoice_id} has no NOK basis fields (amountOutstanding/amount)"}
    invoice_nok_basis = round(float(invoice_nok_basis), 2)

    currency_id = await _lookup_currency_id(currency, client)
    if not currency_id:
        return {"error": f"Could not resolve currency: {currency}"}

    if paid_amount_nok is not None:
        payment_nok = round(float(paid_amount_nok), 2)
    elif payment_rate:
        payment_nok = round(payment_amount_fx * float(payment_rate), 2)
    else:
        payment_nok = await _get_exchange_rate_nok(currency_id, payment_amount_fx, payment_date, client)
        if payment_nok is None:
            return {"error": "Could not fetch payment NOK from API. Provide paidAmount or paymentRate manually."}

    fx_diff = round(payment_nok - invoice_nok_basis, 2)  # positive = gain (agio)
    logger.info(
        "FX payment: invoice basis %.2f NOK, payment %.2f NOK, fx_diff=%.2f",
        invoice_nok_basis,
        payment_nok,
        fx_diff,
    )

    payment_type_id = data.get("paymentTypeId") or await _find_payment_type_id(client)
    if not payment_type_id:
        return {"error": "No payment type available"}

    params = {
        "paymentDate": payment_date,
        "paymentTypeId": str(payment_type_id),
        "paidAmount": str(payment_nok),
        "paidAmountCurrency": str(payment_amount_fx),
    }

    logger.info("Registering FX payment on invoice %d: %.2f NOK + %.2f %s", invoice_id, payment_nok, payment_amount_fx, currency)
    payment_result = await client.put(f"/invoice/{invoice_id}/:payment", params=params)

    payment_ok = payment_result.get("value", {}).get("id")
    if not payment_ok:
        logger.error("Payment registration failed: %s", payment_result)
        return {"error": f"Payment failed: {payment_result}", "invoice_id": invoice_id}

    # Use the PUT response directly — it already returns the updated invoice
    invoice_after = payment_result.get("value", {})
    outstanding_after = round(float(invoice_after.get("amountOutstanding", 0) or 0), 2)
    outstanding_currency_after = round(float(invoice_after.get("amountCurrencyOutstanding", 0) or 0), 2)

    result = {
        "value": payment_result.get("value"),
        "invoice_id": invoice_id,
        "payment_nok": payment_nok,
        "invoice_nok_basis": invoice_nok_basis,
        "fx_difference": fx_diff,
        "amountOutstandingAfterPayment": outstanding_after,
        "amountCurrencyOutstandingAfterPayment": outstanding_currency_after,
    }

    if abs(fx_diff) < 0.01:
        logger.info("No exchange difference to post")
        result["message"] = "Payment registered, no exchange difference"
        return result

    force_manual_voucher = bool(data.get("forceManualFxVoucher"))
    if not force_manual_voucher and abs(outstanding_after) < 0.01 and abs(outstanding_currency_after) < 0.01:
        # Invoice fully settled — but we still need the FX difference voucher.
        # Tripletex settles the invoice when paidAmountCurrency matches the full
        # foreign amount, but does NOT auto-post the exchange difference journal entry.
        # If we skip here, the disagio/agio is silently lost.
        logger.info(
            "Invoice settled (outstanding=0) but fx_diff=%.2f — posting FX voucher anyway",
            fx_diff,
        )

    if fx_diff > 0:
        # Gain (agio): debit 1500 AR, credit 8160 Valutagevinst
        label = f"Agio {currency} {description}".strip()
        postings = [
            {"account": 1500, "amountGross": abs(fx_diff), "description": label, "customerId": customer_id},
            {"account": 8160, "amountGross": -abs(fx_diff), "description": label},
        ]
        logger.info("Posting agio (gain): %.2f NOK — debit 1500, credit 8160", abs(fx_diff))
    else:
        # Loss (disagio): debit 8060 Valutatap, credit 1500 AR
        label = f"Disagio {currency} {description}".strip()
        postings = [
            {"account": 8060, "amountGross": abs(fx_diff), "description": label},
            {"account": 1500, "amountGross": -abs(fx_diff), "description": label, "customerId": customer_id},
        ]
        logger.info("Posting disagio (loss): %.2f NOK — debit 8060, credit 1500", abs(fx_diff))

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
        result["message"] = f"Payment + {'agio' if fx_diff > 0 else 'disagio'} voucher posted"
    else:
        logger.error("Exchange diff voucher failed: %s", voucher_result)
        result["voucher_error"] = voucher_result
        result["_needs_repair"] = (
            "Payment registered but exchange difference voucher failed. "
            f"Post manually: {'1500 debit / 8160 credit' if fx_diff > 0 else '8060 debit / 1500 credit'} "
            f"for {abs(fx_diff)}"
        )

    return result
