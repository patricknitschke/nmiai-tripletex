"""Pre-built Tripletex workflows and their registry."""

from .employee import create_employee
from .customer import create_customer
from .department import create_department
from .product import create_product
from .invoice import create_invoice, create_order
from .payment import register_payment
from .credit_note import create_credit_note
from .travel_expense import create_travel_expense, delete_travel_expense
from .project import create_project
from .voucher import create_supplier_invoice, create_voucher
from .timesheet import register_time

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
    # Tier 3
    "create_supplier_invoice": create_supplier_invoice,
    "create_voucher": create_voucher,
    "register_time": register_time,
}
