import logging

from .tripletex import TripletexClient
from .workflows.employee import create_employee
from .workflows.customer import create_customer
from .workflows.department import create_department
from .workflows.product import create_product
from .workflows.invoice import create_invoice, create_order
from .workflows.payment import register_payment
from .workflows.credit_note import create_credit_note
from .workflows.travel_expense import create_travel_expense, delete_travel_expense
from .workflows.project import create_project
from .fallback import run_fallback_agent

logger = logging.getLogger("agent.router")

# Map task_type → workflow function
# Each workflow takes (data: dict, client: TripletexClient) and returns a result dict
WORKFLOWS: dict[str, callable] = {
    # Tier 1
    "create_employee": create_employee,
    "create_customer": create_customer,
    "create_department": create_department,
    "create_product": create_product,
    "create_order": create_order,
    "create_invoice": create_invoice,
    # Tier 2
    "register_payment": register_payment,
    "create_credit_note": create_credit_note,
    "create_travel_expense": create_travel_expense,
    "delete_travel_expense": delete_travel_expense,
    "create_project": create_project,
}


async def route_task(
    task_type: str,
    data: dict,
    client: TripletexClient,
    original_prompt: str,
    files: list,
) -> dict:
    """Route a task to its workflow, or fall back to Claude agent loop."""
    if task_type in WORKFLOWS:
        workflow = WORKFLOWS[task_type]
        logger.info("ROUTING: '%s' → workflow '%s'", task_type, workflow.__name__)
        return await workflow(data, client)
    else:
        logger.warning(
            "FALLBACK: No workflow for task_type='%s'. Falling back to agent loop.",
            task_type,
        )
        logger.warning("FALLBACK REASON: task_type '%s' not in known workflows: %s", task_type, list(WORKFLOWS.keys()))
        return await run_fallback_agent(original_prompt, files, client)
