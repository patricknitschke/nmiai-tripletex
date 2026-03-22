"""
Orchestrator — routes tasks to the right execution mode.

Modes:
  - "senior": Single Senior Accountant agent (no planning, direct execution)
  - "hybrid": Chief plans (1 LLM call) → Senior executes with plan as context (default)

Set via AGENT_MODE env var. Defaults to "hybrid".
"""

import asyncio
import logging
import os

from .agents import chief_plan
from .agents.senior import run_senior_accountant
from .tripletex import TripletexClient

logger = logging.getLogger("agent.orchestrator")

AGENT_MODE = os.environ.get("AGENT_MODE", "hybrid").lower()

_SKIP_CHIEF_KEYWORDS = [
    "closing", "lukking", "avslutning", "årsoppgjør", "årsavslutning",
    "depreciation", "avskrivning", "avskriving",
    "periodisering", "accrual", "provision", "salary accrual",
    "monthly close", "cierre mensual", "clôture", "encerramento",
    "year-end", "yearend", "balance de saldos", "saldobalanse",
    "dimensjon", "dimension", "dimensão", "dimensión",
    "bank statement", "bankutskrift", "bankutskrifta", "extracto bancario",
    "extrato bancario", "kontoauszug", "compare expenses",
    "largest increase", "mayor aumento", "maior aumento",
]


def _normalize_prompt(prompt: str) -> str:
    return " ".join((prompt or "").lower().split())


def _contains_any(prompt: str, keywords: list[str]) -> bool:
    return any(keyword in prompt for keyword in keywords)


def _should_skip_chief(prompt: str) -> bool:
    return _contains_any(_normalize_prompt(prompt), _SKIP_CHIEF_KEYWORDS)


def _rule_based_plan_preamble(prompt: str) -> str | None:
    prompt_lower = _normalize_prompt(prompt)

    if _contains_any(prompt_lower, [
        "bank statement", "bankutskrift", "bankutskrifta", "extracto bancario",
        "extrato bancario", "kontoauszug", "concili", "reconcile", "avstem",
    ]):
        return "\n".join([
            "**Steps to execute (in order):**",
            "1. [reconcile_bank_statement] Reconcile the attached statement in one workflow call. Match customer and supplier invoices, then post fee/interest rows if present.",
            "",
            "Prefer the dedicated reconciliation workflow over manual payment loops or entity creation.",
        ])

    if _contains_any(prompt_lower, [
        "overdue invoice", "uberfällig", "überfällig", "mahngeb", "reminder fee",
        "late fee", "purregebyr", "forfalt", "overdue",
    ]):
        return "\n".join([
            "**Steps to execute (in order):**",
            "1. [find_overdue_invoices] Find the real overdue invoice and customer first.",
            "2. [send_reminder] Create the reminder/reminder fee from that invoice.",
            "3. [register_payment] Register any partial payment against the same invoice.",
            "",
            "Never invent a customer or create a fresh invoice for an overdue-payment task.",
        ])

    if _contains_any(prompt_lower, [
        "returned by the bank", "payment returned", "annulez le paiement", "retourné",
        "reversed by bank", "stornier", "annulé", "reverse payment",
    ]):
        return "\n".join([
            "**Steps to execute (in order):**",
            "1. [create_credit_note] Find the original invoice and reverse it with a credit note.",
            "",
            "Do not use register_payment with a negative amount for payment reversals.",
        ])

    if _contains_any(prompt_lower, [
        "disagio", "agio", "exchange difference", "écart de change", "cambial",
        "valutatap", "valutagevinst", "fx payment", "foreign currency",
    ]) or (
        _contains_any(prompt_lower, ["eur", "usd", "gbp"])
        and _contains_any(prompt_lower, ["rate", "kurs", "change", "cambio", "taxa"])
    ):
        return "\n".join([
            "**Steps to execute (in order):**",
            "1. [register_fx_payment] Find the existing foreign-currency invoice, register the payment with paidAmountCurrency, and post any agio/disagio voucher if needed.",
            "",
            "Do not convert the invoice to NOK up front and do not treat this as a normal register_payment task.",
        ])

    if _contains_any(prompt_lower, [
        "compare expenses", "largest increase", "mayor aumento", "maior aumento",
        "größten anstieg", "identify the three accounts", "identifique as três contas",
    ]):
        return "\n".join([
            "**Steps to execute (in order):**",
            "1. [compare_expenses] Compute the ranked expense-account increases for the requested period.",
            "2. [create_projects_batch] Create the internal projects and activities from the returned top_increases list.",
            "",
            "Use the dedicated comparison workflow, not analyze_ledger, for month-over-month expense ranking.",
        ])

    if _contains_any(prompt_lower, ["dimensjon", "dimension", "dimensão", "dimensión"]):
        return "\n".join([
            "**Steps to execute (in order):**",
            "1. [create_dimension_voucher] Create the free accounting dimension, values, and linked voucher in one workflow call when the prompt asks for both.",
            "",
            "Prefer the combined workflow so the dimension value ID is not lost between steps.",
        ])

    return None


async def solve_task(
    prompt: str,
    files: list,
    client: TripletexClient,
    deadline: float | None = None,
) -> dict:
    """Route to the right execution mode."""
    logger.info("Agent mode: %s", AGENT_MODE)

    if AGENT_MODE == "hybrid":
        return await _run_hybrid_mode(prompt, files, client, deadline=deadline)
    else:
        return await run_senior_accountant(prompt, files, client, deadline=deadline)


async def _run_hybrid_mode(
    prompt: str,
    files: list,
    client: TripletexClient,
    deadline: float | None = None,
) -> dict:
    """Hybrid mode: Chief plans (1 LLM call), Senior executes with plan as context."""
    rule_preamble = _rule_based_plan_preamble(prompt)

    if _should_skip_chief(prompt):
        logger.info("Rule-based fast path detected — skipping Chief and running Senior directly")
        return await run_senior_accountant(
            prompt,
            files,
            client,
            deadline=deadline,
            plan_preamble=rule_preamble,
        )

    # Stage 1: Chief produces a strategic plan (50s max — PDF inputs can be slow)
    logger.info("=" * 40)
    logger.info("HYBRID MODE: Chief planning...")
    try:
        # Don't pass files (PDFs/CSVs) to Chief — it only needs the text prompt to plan.
        # Senior gets the full files for data extraction.
        thinking, steps = await asyncio.wait_for(chief_plan(prompt, []), timeout=30.0)
    except asyncio.TimeoutError:
        logger.warning("Chief planning timed out after 30s — skipping to Senior")
        return await run_senior_accountant(
            prompt,
            files,
            client,
            deadline=deadline,
            plan_preamble=rule_preamble,
        )

    if not steps:
        logger.warning("Chief produced empty plan, falling back to Senior")
        if thinking and rule_preamble:
            preamble = f"**Chief reasoning (plan failed):** {thinking}\n\n{rule_preamble}"
        elif thinking:
            preamble = f"**Chief reasoning (plan failed):** {thinking}"
        else:
            preamble = rule_preamble
        return await run_senior_accountant(prompt, files, client, deadline=deadline, plan_preamble=preamble)

    # Safety net: detect if Chief "gave up" instead of planning
    if len(steps) == 1 and steps[0].get("suggested_workflow") == "fallback":
        task_text = steps[0].get("task", "").lower()
        give_up_signals = ["missing", "can't proceed", "cannot proceed", "not available",
                           "please provide", "invoice number is required", "not possible"]
        if any(signal in task_text for signal in give_up_signals):
            logger.warning("Chief plan looks like giving up: '%s'. Ignoring plan, Senior will handle directly.",
                           steps[0].get("task", ""))
            return await run_senior_accountant(
                prompt,
                files,
                client,
                deadline=deadline,
                plan_preamble=rule_preamble,
            )

    # Log the plan
    for i, step in enumerate(steps, 1):
        logger.info("  PLAN STEP %d: [%s] %s", i, step.get("suggested_workflow", "?"), step.get("task", ""))
    if thinking:
        logger.info("Chief thinking: %s", thinking)

    # Stage 2: Build plan preamble and let Senior execute in one loop
    plan_lines = []
    if thinking:
        plan_lines.append(f"**Reasoning:** {thinking}")
        plan_lines.append("")
    plan_lines.append("**Steps to execute (in order):**")
    for i, step in enumerate(steps, 1):
        wf = step.get("suggested_workflow", "fallback")
        task = step.get("task", "")
        plan_lines.append(f"{i}. [{wf}] {task}")
    plan_lines.append("")
    plan_lines.append("Follow this plan. Pass IDs from earlier steps to later ones. "
                      "Do NOT repeat steps that were already completed by a previous workflow call.")
    preamble = "\n".join(plan_lines)

    logger.info("HYBRID MODE: Senior executing with plan...")
    return await run_senior_accountant(prompt, files, client, deadline=deadline, plan_preamble=preamble)


