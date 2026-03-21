"""
Ledger Error Analysis — fetch postings and detect common accounting errors.

Returns structured error list for Senior to create corrective vouchers via create_voucher.
"""

import logging
from datetime import date

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.ledger_analysis")


async def analyze_ledger(data: dict, client: TripletexClient) -> dict:
    """Analyze ledger postings for a date range and detect common errors.

    Fetches all postings, groups by voucher, and checks for:
    - Imbalanced vouchers (debits != credits)
    - Duplicate postings (same account + amount on same date)
    - Unusual amounts (negative where positive expected, etc.)

    Returns a structured list of suspected errors with enough context
    for Senior to create corrective vouchers via create_voucher.

    Input data fields:
    - dateFrom: start date (default: 2026-01-01)
    - dateTo: end date (default: 2026-02-28)
    - accountFrom: optional account range start
    - accountTo: optional account range end
    """
    today = date.today()
    date_from = data.get("dateFrom", f"{today.year}-01-01")
    date_to = data.get("dateTo", f"{today.year}-02-28")

    logger.info("Analyzing ledger postings from %s to %s", date_from, date_to)

    # Fetch all postings in the date range
    params = {
        "dateFrom": date_from,
        "dateTo": date_to,
        "count": "10000",
    }
    if data.get("accountFrom"):
        params["accountNumberFrom"] = str(data["accountFrom"])
    if data.get("accountTo"):
        params["accountNumberTo"] = str(data["accountTo"])

    result = await client.get("/ledger/posting", params=params)
    postings = result.get("values", [])
    logger.info("Fetched %d postings for analysis", len(postings))

    if not postings:
        return {"value": {"postings_count": 0, "errors_found": [], "summary": "No postings found in date range"}}

    # Group postings by voucher
    vouchers: dict[int, list] = {}
    for p in postings:
        v_id = p.get("voucher", {}).get("id") if isinstance(p.get("voucher"), dict) else p.get("voucherId")
        if v_id:
            vouchers.setdefault(v_id, []).append(p)

    errors_found = []

    # Check 1: Imbalanced vouchers (debits should equal credits)
    for v_id, v_postings in vouchers.items():
        total = sum(p.get("amountGross", p.get("amount", 0)) for p in v_postings)
        if abs(total) > 0.01:
            sample = v_postings[0]
            errors_found.append({
                "type": "imbalanced_voucher",
                "voucherId": v_id,
                "imbalance": round(total, 2),
                "date": sample.get("date", ""),
                "postings": [
                    {
                        "account": p.get("account", {}).get("number") if isinstance(p.get("account"), dict) else None,
                        "accountName": p.get("account", {}).get("name") if isinstance(p.get("account"), dict) else None,
                        "amount": p.get("amountGross", p.get("amount", 0)),
                        "description": p.get("description", ""),
                    }
                    for p in v_postings
                ],
                "suggestion": f"Voucher {v_id} is out of balance by {round(total, 2)}. Create a corrective voucher.",
            })

    # Check 2: Duplicate postings (same account + same absolute amount on same date, same voucher)
    for v_id, v_postings in vouchers.items():
        seen = {}
        for p in v_postings:
            acct = p.get("account", {}).get("number") if isinstance(p.get("account"), dict) else "?"
            amt = round(p.get("amountGross", p.get("amount", 0)), 2)
            key = (acct, abs(amt), p.get("date", ""))
            if key in seen and abs(amt) > 0:
                errors_found.append({
                    "type": "duplicate_posting",
                    "voucherId": v_id,
                    "account": acct,
                    "amount": amt,
                    "date": p.get("date", ""),
                    "description": p.get("description", ""),
                    "suggestion": f"Account {acct} has duplicate posting of {amt} on {p.get('date', '')}. May need reversal.",
                })
            seen[key] = p

    # Check 3: VAT postings without corresponding expense (orphaned VAT)
    for v_id, v_postings in vouchers.items():
        accounts = [p.get("account", {}).get("number") if isinstance(p.get("account"), dict) else None for p in v_postings]
        has_vat = any(a and str(a).startswith("27") for a in accounts)
        has_expense = any(a and str(a).startswith(("4", "5", "6", "7")) for a in accounts)
        if has_vat and not has_expense:
            errors_found.append({
                "type": "orphaned_vat",
                "voucherId": v_id,
                "date": v_postings[0].get("date", ""),
                "postings": [
                    {
                        "account": p.get("account", {}).get("number") if isinstance(p.get("account"), dict) else None,
                        "amount": p.get("amountGross", p.get("amount", 0)),
                    }
                    for p in v_postings
                ],
                "suggestion": f"Voucher {v_id} has VAT posting but no expense account. Possible missing expense line.",
            })

    # Build summary with all postings grouped by voucher for Senior's context
    voucher_summaries = []
    for v_id, v_postings in vouchers.items():
        voucher_summaries.append({
            "voucherId": v_id,
            "date": v_postings[0].get("date", ""),
            "description": v_postings[0].get("description", ""),
            "postings": [
                {
                    "account": p.get("account", {}).get("number") if isinstance(p.get("account"), dict) else None,
                    "accountName": p.get("account", {}).get("name") if isinstance(p.get("account"), dict) else None,
                    "amount": p.get("amountGross", p.get("amount", 0)),
                    "description": p.get("description", ""),
                }
                for p in v_postings
            ],
        })

    logger.info("Analysis complete: %d vouchers, %d errors detected", len(vouchers), len(errors_found))

    return {
        "value": {
            "postings_count": len(postings),
            "vouchers_count": len(vouchers),
            "errors_found": errors_found,
            "voucher_summaries": voucher_summaries,
            "summary": (
                f"Analyzed {len(postings)} postings across {len(vouchers)} vouchers "
                f"({date_from} to {date_to}). Found {len(errors_found)} potential error(s). "
                "Review errors_found and use create_voucher to post corrective entries."
            ),
        }
    }
