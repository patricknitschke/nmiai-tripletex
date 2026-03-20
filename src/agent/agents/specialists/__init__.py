"""
Specialist domain agents — each has deep knowledge of their API patterns.

Routing maps suggested_workflow from the Chief's plan to the right specialist.
"""

from .ap import run_ap_specialist
from .corrections import run_corrections_specialist
from .employee import run_employee_specialist
from .general import run_general_specialist
from .invoice import run_invoice_specialist
from .travel import run_travel_specialist

# Map workflow name → specialist runner
SPECIALIST_ROUTING: dict[str, callable] = {
    # Invoice domain
    "create_invoice": run_invoice_specialist,
    "create_order": run_invoice_specialist,
    "register_payment": run_invoice_specialist,
    # Employee domain
    "create_employee": run_employee_specialist,
    "create_department": run_employee_specialist,
    # Travel domain
    "create_travel_expense": run_travel_specialist,
    "delete_travel_expense": run_travel_specialist,
    # Corrections domain
    "create_credit_note": run_corrections_specialist,
    "delete_entry": run_corrections_specialist,
    "reverse_entry": run_corrections_specialist,
    # Accounts Payable domain
    "supplier_invoice": run_ap_specialist,
    "create_voucher": run_ap_specialist,
    # General domain (customer, product, project, fallback)
    "create_customer": run_general_specialist,
    "create_product": run_general_specialist,
    "create_project": run_general_specialist,
    "fallback": run_general_specialist,
}


def get_specialist(suggested_workflow: str):
    """Return the specialist runner for a workflow, defaulting to general."""
    return SPECIALIST_ROUTING.get(suggested_workflow, run_general_specialist)
