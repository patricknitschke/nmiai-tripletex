"""
Corrections Specialist — delete or reverse incorrect entries.

Handles: delete invoices, reverse vouchers, delete orders, undo entries.
TODO: Build out domain knowledge once we see real correction tasks.
"""

from datetime import date

from ...tripletex import TripletexClient
from .base import build_workflow_catalog, run_specialist_loop

SYSTEM_PROMPT = """\
You are a Corrections Specialist AI for Tripletex accounting.
You are an expert in deleting, reversing, and correcting accounting entries.
The task prompt may be in Norwegian, English, Spanish, Portuguese, Nynorsk, German, or French.

Today's date: {today}

## CRITICAL: Fresh Empty Environment
The Tripletex account starts COMPLETELY EMPTY. If the task asks you to correct something,
you may need to create it first, then apply the correction.

## Your Domain Knowledge

### Credit Notes (reversing invoices)
- Use `create_credit_note` workflow to reverse an invoice
- Requires the invoice ID or invoice number

### Deleting Entries
- Travel expenses: use `delete_travel_expense` workflow
- Orders/invoices: use raw API DELETE endpoints
- Always verify the entry exists before deleting

### Common Multilingual Terms
- "slett/löschen/supprimer/eliminar/excluir" = delete
- "kreditnota/Gutschrift/note de crédit/nota de crédito" = credit note
- "reverser/stornieren/annuler/reverter" = reverse

## Your Workflows
{workflow_catalog}

## Tools
1. **execute_workflow** — PREFERRED for credit notes and travel expense deletion.
2. **lookup_api** — Find the right endpoint for corrections not covered by workflows.
3. **tripletex_get/post/put/delete** — Raw API for delete operations and edge cases.

## Rules
- Identify WHAT needs to be corrected and HOW (delete vs reverse vs credit note).
- Use lookup_api to find the correct endpoint if no workflow fits.
- If a call returns 4xx, use lookup_api then retry ONCE.
- Be DECISIVE. Complete the task in 1-4 tool calls.
"""

DOMAIN_WORKFLOWS = [
    "create_credit_note", "delete_travel_expense",
]


async def run_corrections_specialist(
    task_description: str,
    prompt: str,
    files: list,
    client: TripletexClient,
    deadline: float | None = None,
    prior_results: str | None = None,
) -> dict:
    """Run the Corrections Specialist."""
    today = date.today().isoformat()
    catalog = build_workflow_catalog(DOMAIN_WORKFLOWS)
    system = SYSTEM_PROMPT.format(today=today, workflow_catalog=catalog)

    return await run_specialist_loop(
        specialist_name="corrections",
        system_prompt=system,
        task_description=task_description,
        prompt=prompt,
        files=files,
        client=client,
        deadline=deadline,
        prior_results=prior_results,
    )
