"""
Schema registry for Tripletex API fields, derived from the OpenAPI spec.

Each task_type maps to a dict with:
- "fields": list of field definitions for LLM extraction
- "api_endpoint": the Tripletex endpoint used
- "notes": any special instructions for the LLM

Field definitions have:
- "name": exact API field name
- "type": the JSON type to produce
- "required": whether the field is required
- "description": human-readable description for the LLM
- "aliases": alternative names the prompt might use (helps LLM map)
"""

TASK_SCHEMAS: dict[str, dict] = {
    "create_employee": {
        "api_endpoint": "POST /employee",
        "notes": (
            "department.id is required but will be auto-resolved by the workflow. "
            "userType defaults to STANDARD. "
            "If the prompt mentions admin/administrator role, set role to 'administrator'."
        ),
        "fields": [
            {"name": "firstName", "type": "string", "required": True, "description": "First name"},
            {"name": "lastName", "type": "string", "required": True, "description": "Last name"},
            {"name": "email", "type": "string", "required": False, "description": "Email address"},
            {"name": "phoneNumberMobile", "type": "string", "required": False, "description": "Mobile phone number"},
            {"name": "dateOfBirth", "type": "string (YYYY-MM-DD)", "required": False, "description": "Date of birth"},
            {"name": "employeeNumber", "type": "string", "required": False, "description": "Employee number/ID"},
            {"name": "bankAccountNumber", "type": "string", "required": False, "description": "Bank account number"},
            {"name": "nationalIdentityNumber", "type": "string", "required": False, "description": "National identity number (fødselsnummer/personnummer)"},
            {"name": "address", "type": "object {addressLine1, postalCode, city}", "required": False, "description": "Home address"},
            {"name": "role", "type": "string", "required": False, "description": "Role: 'administrator' if admin access is needed, otherwise omit"},
        ],
    },
    "create_customer": {
        "api_endpoint": "POST /customer",
        "notes": "name is the only required field. isCustomer defaults to true in the workflow.",
        "fields": [
            {"name": "name", "type": "string", "required": True, "description": "Company or person name"},
            {"name": "email", "type": "string", "required": False, "description": "General email"},
            {"name": "invoiceEmail", "type": "string", "required": False, "description": "Email for invoices (faktura e-post)"},
            {"name": "phoneNumber", "type": "string", "required": False, "description": "Phone number"},
            {"name": "organizationNumber", "type": "string", "required": False, "description": "Organization number (org.nr)"},
            {"name": "isPrivateIndividual", "type": "boolean", "required": False, "description": "True if person (not company)"},
            {"name": "language", "type": "string enum: NO, EN", "required": False, "description": "Language for invoices"},
            {"name": "invoiceSendMethod", "type": "string enum: EMAIL, EHF, EFAKTURA, AVTALEGIRO, VIPPS, PAPER, MANUAL", "required": False, "description": "How to send invoices"},
            {"name": "invoicesDueIn", "type": "integer", "required": False, "description": "Number of days/months until invoice is due"},
            {"name": "invoicesDueInType", "type": "string enum: DAYS, MONTHS, RECURRING_DAY_OF_MONTH", "required": False, "description": "Unit for invoicesDueIn"},
            {"name": "postalAddress", "type": "object {addressLine1, postalCode, city}", "required": False, "description": "Postal address"},
            {"name": "physicalAddress", "type": "object {addressLine1, postalCode, city}", "required": False, "description": "Physical/visiting address"},
            {"name": "isSupplier", "type": "boolean", "required": False, "description": "True if also a supplier"},
        ],
    },
    "create_department": {
        "api_endpoint": "POST /department",
        "notes": "Both name and departmentNumber are required.",
        "fields": [
            {"name": "name", "type": "string", "required": True, "description": "Department name"},
            {"name": "departmentNumber", "type": "string", "required": True, "description": "Department number (unique identifier). Default to '1' if not specified."},
        ],
    },
    "create_product": {
        "api_endpoint": "POST /product",
        "notes": "vatType is an object reference {id: int}. The workflow will look up the correct VAT type ID.",
        "fields": [
            {"name": "name", "type": "string", "required": True, "description": "Product name"},
            {"name": "number", "type": "string", "required": False, "description": "Product number/SKU"},
            {"name": "description", "type": "string", "required": False, "description": "Product description"},
            {"name": "costExcludingVatCurrency", "type": "number", "required": False, "description": "Cost/purchase price excluding VAT"},
            {"name": "priceExcludingVatCurrency", "type": "number", "required": False, "description": "Selling price excluding VAT"},
            {"name": "priceIncludingVatCurrency", "type": "number", "required": False, "description": "Selling price including VAT"},
        ],
    },
    "create_order": {
        "api_endpoint": "POST /order",
        "notes": (
            "customer is resolved by the workflow (by name or ID). "
            "orderLines use exact API field names. product should be an object {id: int} if known, "
            "otherwise omit and use description instead."
        ),
        "fields": [
            {"name": "customerName", "type": "string", "required": False, "description": "Customer name (used to look up or create customer)"},
            {"name": "customerId", "type": "integer", "required": False, "description": "Customer ID if known"},
            {"name": "orderDate", "type": "string (YYYY-MM-DD)", "required": True, "description": "Order date. Default to today."},
            {"name": "deliveryDate", "type": "string (YYYY-MM-DD)", "required": True, "description": "Delivery date. Default to order date."},
            {"name": "invoiceComment", "type": "string", "required": False, "description": "Comment to appear on invoice"},
            {"name": "receiverEmail", "type": "string", "required": False, "description": "Email to send order to"},
            {"name": "orderLines", "type": "array of objects", "required": True, "description": "Line items", "items": [
                {"name": "description", "type": "string", "description": "Line description (product name or service)"},
                {"name": "count", "type": "number", "description": "Quantity"},
                {"name": "unitPriceExcludingVatCurrency", "type": "number", "description": "Unit price excluding VAT"},
            ]},
        ],
    },
    "create_invoice": {
        "api_endpoint": "POST /order → PUT /order/{id}/:invoice",
        "notes": (
            "Invoice creation is a 2-step process: create order, then invoice from order. "
            "Customer is resolved by the workflow. If the prompt says to create a customer, "
            "include customer details in a 'customer' object."
        ),
        "fields": [
            {"name": "customerName", "type": "string", "required": False, "description": "Customer name (looked up or created)"},
            {"name": "customerId", "type": "integer", "required": False, "description": "Customer ID if known"},
            {"name": "customer", "type": "object {name, email, organizationNumber, ...}", "required": False, "description": "Full customer details if creating new customer"},
            {"name": "invoiceDate", "type": "string (YYYY-MM-DD)", "required": True, "description": "Invoice date. Default to today."},
            {"name": "dueDate", "type": "string (YYYY-MM-DD)", "required": False, "description": "Due date for payment"},
            {"name": "invoiceComment", "type": "string", "required": False, "description": "Comment on the invoice"},
            {"name": "orderLines", "type": "array of objects", "required": True, "description": "Invoice line items", "items": [
                {"name": "description", "type": "string", "description": "Line description (product/service name)"},
                {"name": "count", "type": "number", "description": "Quantity"},
                {"name": "unitPriceExcludingVatCurrency", "type": "number", "description": "Unit price excluding VAT"},
            ]},
        ],
    },
    "register_payment": {
        "api_endpoint": "PUT /invoice/{id}/:payment (query params)",
        "notes": (
            "All parameters are query params, not body. "
            "paymentTypeId is required — the workflow looks it up via GET /invoice/paymentType. "
            "The workflow resolves invoiceId from invoiceNumber if needed."
        ),
        "fields": [
            {"name": "invoiceId", "type": "integer", "required": False, "description": "Invoice ID"},
            {"name": "invoiceNumber", "type": "integer", "required": False, "description": "Invoice number (if ID not known)"},
            {"name": "paymentDate", "type": "string (YYYY-MM-DD)", "required": True, "description": "Date of payment. Default to today."},
            {"name": "paidAmount", "type": "number", "required": True, "description": "Amount paid"},
        ],
    },
    "create_credit_note": {
        "api_endpoint": "PUT /invoice/{id}/:createCreditNote (query params)",
        "notes": "All parameters are query params. The workflow resolves invoiceId from invoiceNumber if needed.",
        "fields": [
            {"name": "invoiceId", "type": "integer", "required": False, "description": "Invoice ID"},
            {"name": "invoiceNumber", "type": "integer", "required": False, "description": "Invoice number (if ID not known)"},
            {"name": "date", "type": "string (YYYY-MM-DD)", "required": True, "description": "Credit note date. Default to today."},
            {"name": "comment", "type": "string", "required": False, "description": "Comment on the credit note"},
        ],
    },
    "create_travel_expense": {
        "api_endpoint": "POST /travelExpense + POST /travelExpense/cost per line",
        "notes": (
            "Employee is auto-resolved if not specified. "
            "Each cost line requires: date, amountCurrencyIncVat, paymentType (object ref — workflow handles lookup). "
            "Use 'comments' (not 'description') for cost line text."
        ),
        "fields": [
            {"name": "title", "type": "string", "required": True, "description": "Travel expense title"},
            {"name": "date", "type": "string (YYYY-MM-DD)", "required": False, "description": "Travel date. Default to today."},
            {"name": "employeeId", "type": "integer", "required": False, "description": "Employee ID (auto-resolved if omitted)"},
            {"name": "costs", "type": "array of objects", "required": False, "description": "Cost/expense lines", "items": [
                {"name": "comments", "type": "string", "description": "What the expense was for (e.g. 'Taxi', 'Togbillett')"},
                {"name": "amountCurrencyIncVat", "type": "number", "description": "Amount including VAT"},
                {"name": "date", "type": "string (YYYY-MM-DD)", "description": "Date of this expense. Defaults to travel date."},
            ]},
        ],
    },
    "delete_travel_expense": {
        "api_endpoint": "DELETE /travelExpense/{id}",
        "notes": "Simple deletion by ID.",
        "fields": [
            {"name": "travelExpenseId", "type": "integer", "required": True, "description": "ID of the travel expense to delete"},
        ],
    },
    "create_project": {
        "api_endpoint": "POST /project",
        "notes": "projectManager is required but auto-resolved by the workflow. Do not guess employee IDs.",
        "fields": [
            {"name": "name", "type": "string", "required": True, "description": "Project name"},
            {"name": "description", "type": "string", "required": False, "description": "Project description"},
            {"name": "startDate", "type": "string (YYYY-MM-DD)", "required": False, "description": "Start date"},
            {"name": "endDate", "type": "string (YYYY-MM-DD)", "required": False, "description": "End date"},
            {"name": "isInternal", "type": "boolean", "required": False, "description": "True if internal project"},
            {"name": "isFixedPrice", "type": "boolean", "required": False, "description": "True if fixed price, false if hourly"},
        ],
    },
}

# Task types list for the classifier
KNOWN_TASK_TYPES = list(TASK_SCHEMAS.keys())


def get_extraction_prompt(task_type: str) -> str:
    """Build an extraction prompt with the exact field schema for a task type."""
    schema = TASK_SCHEMAS.get(task_type)
    if not schema:
        return ""

    lines = [
        f"Extract the following fields for task '{task_type}'.",
        f"API endpoint: {schema['api_endpoint']}",
        f"Notes: {schema['notes']}",
        "",
        "Fields to extract (use these exact field names in your JSON output):",
    ]

    for field in schema["fields"]:
        req = "REQUIRED" if field.get("required") else "optional"
        lines.append(f"  - {field['name']} ({field['type']}, {req}): {field['description']}")
        if "items" in field:
            lines.append(f"    Each item has:")
            for item in field["items"]:
                lines.append(f"      - {item['name']} ({item['type']}): {item['description']}")

    lines.extend([
        "",
        "IMPORTANT:",
        "- Use the EXACT field names listed above.",
        "- For dates, use YYYY-MM-DD format.",
        "- Only include fields that are mentioned or clearly implied in the prompt.",
        "- Do NOT guess IDs for entities (employees, customers, etc.) — the workflow will look them up.",
        "- Return ONLY valid JSON: {\"data\": { ... }}",
    ])

    return "\n".join(lines)
