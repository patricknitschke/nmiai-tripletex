import asyncio

import pytest

from src.agent import orchestrator

from .conftest import MockTripletexClient


@pytest.mark.asyncio
async def test_skips_chief_for_rule_based_fast_path(monkeypatch):
    calls = {"chief": 0, "senior": 0}

    async def fake_chief_plan(prompt, files):
        calls["chief"] += 1
        return "", []

    async def fake_run_senior(prompt, files, client, deadline=None, plan_preamble=None):
        calls["senior"] += 1
        return {"plan_preamble": plan_preamble}

    monkeypatch.setattr(orchestrator, "chief_plan", fake_chief_plan)
    monkeypatch.setattr(orchestrator, "run_senior_accountant", fake_run_senior)

    result = await orchestrator._run_hybrid_mode(
        "Reconcilie o extrato bancario (CSV anexo) com as faturas em aberto.",
        [],
        MockTripletexClient(),
    )

    assert calls["chief"] == 0
    assert calls["senior"] == 1
    assert "reconcile_bank_statement" in result["plan_preamble"]


@pytest.mark.asyncio
async def test_uses_rule_based_preamble_when_chief_times_out(monkeypatch):
    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    async def fake_run_senior(prompt, files, client, deadline=None, plan_preamble=None):
        return {"plan_preamble": plan_preamble}

    monkeypatch.setattr(orchestrator.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(orchestrator, "run_senior_accountant", fake_run_senior)

    result = await orchestrator._run_hybrid_mode(
        "Nous avons envoyé une facture de 6893 EUR quand le taux a changé. Enregistrez paiement et comptabilisez disagio.",
        [],
        MockTripletexClient(),
    )

    assert "register_fx_payment" in result["plan_preamble"]


@pytest.mark.asyncio
async def test_uses_rule_based_preamble_when_chief_returns_empty(monkeypatch):
    async def fake_chief_plan(prompt, files):
        return "", []

    async def fake_run_senior(prompt, files, client, deadline=None, plan_preamble=None):
        return {"plan_preamble": plan_preamble}

    monkeypatch.setattr(orchestrator, "chief_plan", fake_chief_plan)
    monkeypatch.setattr(orchestrator, "run_senior_accountant", fake_run_senior)

    result = await orchestrator._run_hybrid_mode(
        "One of your customers has an overdue invoice. Find the overdue invoice and post a reminder fee of 65 NOK.",
        [],
        MockTripletexClient(),
    )

    assert "find_overdue_invoices" in result["plan_preamble"]
    assert "send_reminder" in result["plan_preamble"]
    assert "register_payment" in result["plan_preamble"]