"""
Ledger Error Analysis — fetch postings and detect common accounting errors.

Returns structured error list for Senior to create corrective vouchers via create_voucher.
"""

import logging
from datetime import date, timedelta

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.ledger_analysis")


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iter_dict_nodes(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dict_nodes(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dict_nodes(item)


def _extract_debit_credit_totals(balance_sheet_result: dict) -> tuple[float | None, float | None, str]:
    """Best-effort totals parser for /balanceSheet response variants."""
    root = balance_sheet_result.get("value") if isinstance(balance_sheet_result.get("value"), dict) else balance_sheet_result

    # Variant A: explicit debit/credit totals are present.
    debit_keys = ("totalDebit", "debitTotal", "sumDebit", "debit")
    credit_keys = ("totalCredit", "creditTotal", "sumCredit", "credit")
    for d_key in debit_keys:
        for c_key in credit_keys:
            debit = _as_float(root.get(d_key)) if isinstance(root, dict) else None
            credit = _as_float(root.get(c_key)) if isinstance(root, dict) else None
            if debit is not None and credit is not None:
                return debit, credit, f"explicit:{d_key}/{c_key}"

    # Variant B: tree/list of lines with signed balances only.
    candidate_amount_keys = ("amount", "balance", "sum", "closingBalance", "amountTotal")
    signed_values: list[float] = []
    for node in _iter_dict_nodes(root):
        if not isinstance(node, dict):
            continue
        for key in candidate_amount_keys:
            value = _as_float(node.get(key))
            if value is not None:
                signed_values.append(value)
                break

    if not signed_values:
        return None, None, "unparsed"

    debit_total = sum(v for v in signed_values if v > 0)
    credit_total = -sum(v for v in signed_values if v < 0)
    return debit_total, credit_total, "derived:signed-lines"


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
    # dateTo is EXCLUSIVE in Tripletex API ("to and excl.") — use Mar 1 to include all of Feb
    date_to = data.get("dateTo", f"{today.year}-03-01")

    logger.info("Analyzing ledger postings from %s to %s (exclusive)", date_from, date_to)

    # Fetch all postings in the date range
    # Request expanded account fields so we get number+name (not just id+url)
    params = {
        "dateFrom": date_from,
        "dateTo": date_to,
        "count": "10000",
        "fields": "id,voucher,date,description,account(*),amountGross,amount,amountCurrency,amountGrossCurrency,systemGenerated",
    }
    if data.get("accountFrom"):
        params["accountNumberFrom"] = str(data["accountFrom"])
    if data.get("accountTo"):
        params["accountNumberTo"] = str(data["accountTo"])

    # Paginate to avoid silent data loss
    all_postings = []
    offset = 0
    page_size = 10000
    while True:
        params["from"] = str(offset)
        params["count"] = str(page_size)
        result = await client.get("/ledger/posting", params=params)
        page = result.get("values", [])
        all_postings.extend(page)
        full_size = result.get("fullResultSize", len(page))
        if offset + len(page) >= full_size or not page:
            break
        offset += len(page)
    postings = all_postings
    logger.info("Fetched %d postings for analysis (fullResultSize=%s)", len(postings), result.get("fullResultSize"))

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
    # Use 'amount' consistently — amountGross includes VAT which sits on separate posting lines
    for v_id, v_postings in vouchers.items():
        total = sum(p.get("amount", 0) for p in v_postings)
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
                        "amount": p.get("amount", 0),
                        "description": p.get("description", ""),
                    }
                    for p in v_postings
                ],
                "suggestion": f"Voucher {v_id} is out of balance by {round(total, 2)}. Create a corrective voucher.",
            })

    # Check 2: Duplicate postings (same account + same signed amount + same description, same voucher)
    # No abs() — debit+credit to same account is a normal reversal, not a duplicate
    for v_id, v_postings in vouchers.items():
        seen = {}
        for p in v_postings:
            acct = p.get("account", {}).get("number") if isinstance(p.get("account"), dict) else "?"
            amt = round(p.get("amount", 0), 2)
            desc = (p.get("description") or "").strip()
            key = (acct, amt, p.get("date", ""), desc)
            if key in seen and amt != 0:
                errors_found.append({
                    "type": "duplicate_posting",
                    "voucherId": v_id,
                    "account": acct,
                    "amount": amt,
                    "date": p.get("date", ""),
                    "description": desc,
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
                        "amount": p.get("amount", 0),
                    }
                    for p in v_postings
                ],
                "suggestion": f"Voucher {v_id} has VAT posting but no expense account. Possible missing expense line.",
            })

    # Build summaries only for flagged vouchers — avoids bloating response on busy ledgers
    flagged_voucher_ids = {e["voucherId"] for e in errors_found}
    voucher_summaries = []
    for v_id in flagged_voucher_ids:
        v_postings = vouchers[v_id]
        voucher_summaries.append({
            "voucherId": v_id,
            "date": v_postings[0].get("date", ""),
            "description": v_postings[0].get("description", ""),
            "postings": [
                {
                    "account": p.get("account", {}).get("number") if isinstance(p.get("account"), dict) else None,
                    "accountName": p.get("account", {}).get("name") if isinstance(p.get("account"), dict) else None,
                    "amount": p.get("amount", 0),
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


async def compare_expenses(data: dict, client: TripletexClient) -> dict:
    """Compare actual expenses across months using ledger postings.

    Fetches all posted expense transactions and aggregates by account per month.
    Automatically detects which months are present and computes increases between
    consecutive months. The caller (LLM) sets dateFrom/dateTo to cover the
    relevant period — the workflow handles the rest.

    Uses GET /ledger/posting (actual data) — NOT /resultbudget/company (budget data).
    IMPORTANT: dateTo is EXCLUSIVE ("to and excl.").

    Input data fields:
    - dateFrom: start date (yyyy-MM-dd), e.g. "2026-01-01"
    - dateTo: end date (yyyy-MM-dd, EXCLUSIVE), e.g. "2026-03-01"
    - accountFrom: optional account range start (default: 4000)
    - accountTo: optional account range end (default: 8999)
    - topN: number of top accounts to return (default: 10)
    """
    today = date.today()
    date_from = data.get("dateFrom", f"{today.year}-01-01")
    date_to = data.get("dateTo", f"{today.year}-{today.month + 1:02d}-01" if today.month < 12 else f"{today.year + 1}-01-01")
    account_from = str(data.get("accountFrom", 4000))
    account_to = str(data.get("accountTo", 8999))
    top_n = data.get("topN", 10)

    logger.info(
        "Comparing actual expenses %s to %s (excl), accounts %s-%s via /ledger/posting",
        date_from, date_to, account_from, account_to,
    )

    # Fetch all postings with account range filter (server-side)
    all_postings = []
    offset = 0
    page_size = 10000
    while True:
        params = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "accountNumberFrom": account_from,
            "accountNumberTo": account_to,
            "from": str(offset),
            "count": str(page_size),
            "fields": "id,date,account(*),amount,description",
        }
        result = await client.get("/ledger/posting", params=params)
        page = result.get("values", [])
        all_postings.extend(page)
        full_size = result.get("fullResultSize", len(page))
        if offset + len(page) >= full_size or not page:
            break
        offset += len(page)

    logger.info("Fetched %d expense postings", len(all_postings))

    if not all_postings:
        return {
            "value": {
                "postings_count": 0,
                "monthly_totals": {},
                "top_increases": [],
                "summary": "No expense postings found in date range",
            }
        }

    # Aggregate amount per account per month
    account_months: dict[str, dict] = {}
    for p in all_postings:
        acct = p.get("account", {})
        acct_num = str(acct.get("number", "?")) if isinstance(acct, dict) else "?"
        acct_name = acct.get("name", "") if isinstance(acct, dict) else ""
        amount = p.get("amount", 0)
        posting_date = p.get("date", "")
        if posting_date and len(posting_date) >= 7:
            month_num = int(posting_date[5:7])
        else:
            continue

        if acct_num not in account_months:
            account_months[acct_num] = {"name": acct_name, "months": {}}
        account_months[acct_num]["months"][month_num] = (
            account_months[acct_num]["months"].get(month_num, 0) + amount
        )

    # Detect the months present and compute max increase between consecutive months
    all_months_present = sorted({m for info in account_months.values() for m in info["months"]})

    increases = []
    for acct_num, info in account_months.items():
        max_increase = 0.0
        increase_from = None
        increase_to = None
        for i in range(len(all_months_present) - 1):
            m1, m2 = all_months_present[i], all_months_present[i + 1]
            delta = info["months"].get(m2, 0) - info["months"].get(m1, 0)
            if delta > max_increase:
                max_increase = delta
                increase_from = m1
                increase_to = m2
        if max_increase > 0:
            increases.append({
                "account": acct_num,
                "name": info["name"],
                "max_increase": round(max_increase, 2),
                "increase_from_month": increase_from,
                "increase_to_month": increase_to,
                "months": {k: round(v, 2) for k, v in sorted(info["months"].items())},
            })

    # Canonical ranking: highest increase first, deterministic tie-breaker on account number.
    def _rank_key(item: dict) -> tuple[float, int]:
        try:
            account_num = int(item.get("account"))
        except (TypeError, ValueError):
            account_num = 10**9
        return (-item["max_increase"], account_num)

    increases.sort(key=_rank_key)
    top_increases = increases[:top_n]

    # Monthly grand totals
    monthly_totals: dict[int, float] = {}
    for info in account_months.values():
        for month, amount in info["months"].items():
            monthly_totals[month] = monthly_totals.get(month, 0) + amount
    monthly_totals = {k: round(v, 2) for k, v in sorted(monthly_totals.items())}

    months_str = " → ".join(str(m) for m in all_months_present)
    logger.info(
        "Expense comparison complete: %d accounts, months [%s], top %d increases returned",
        len(account_months), months_str, len(top_increases),
    )

    return {
        "value": {
            "postings_count": len(all_postings),
            "accounts_count": len(account_months),
            "months_found": all_months_present,
            "monthly_totals": monthly_totals,
            "top_increases": top_increases,
            "summary": (
                f"Analyzed {len(all_postings)} actual expense postings across {len(account_months)} accounts "
                f"({date_from} to {date_to} excl). Months found: {months_str}. "
                f"Top {len(top_increases)} accounts by largest month-over-month increase (positive deltas only). "
                f"Use top_increases[].name for project/account names."
            ),
        }
    }


async def verify_trial_balance(data: dict, client: TripletexClient) -> dict:
    """Verify whether the trial balance is in equilibrium at a given snapshot date.

    Uses GET /balanceSheet and parses the response into debit/credit totals.
    This workflow is intended for month-end/year-end control steps.

    Input data fields:
    - dateTo: snapshot date (YYYY-MM-DD). Tripletex dateTo is EXCLUSIVE.
    """
    date_to = data.get("dateTo")
    if not date_to:
        tomorrow = date.today() + timedelta(days=1)
        date_to = tomorrow.isoformat()

    params = {"dateTo": date_to}
    if data.get("dateFrom"):
        logger.warning("verify_trial_balance ignores dateFrom to enforce snapshot semantics")

    result = await client.get("/balanceSheet", params=params)
    if result.get("error"):
        return {
            "error": "Failed to fetch balance sheet",
            "details": result,
            "dateTo": date_to,
        }

    debit_total, credit_total, parse_source = _extract_debit_credit_totals(result)
    if debit_total is None or credit_total is None:
        return {
            "error": "Could not parse debit/credit totals from /balanceSheet response",
            "dateTo": date_to,
            "parseSource": parse_source,
            "details": result,
        }

    difference = round(debit_total - credit_total, 2)
    balanced = abs(difference) <= 0.01

    return {
        "value": {
            "dateTo": date_to,
            "balanced": balanced,
            "debitTotal": round(debit_total, 2),
            "creditTotal": round(credit_total, 2),
            "difference": difference,
            "parseSource": parse_source,
            "summary": (
                f"Trial balance {'is balanced' if balanced else 'is NOT balanced'} "
                f"at {date_to}: debit={round(debit_total, 2)}, credit={round(credit_total, 2)}, diff={difference}."
            ),
        }
    }
