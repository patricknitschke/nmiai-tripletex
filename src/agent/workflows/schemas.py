"""
Schema registry for Tripletex API fields, derived from the OpenAPI spec.

Each task_type maps to a dict with:
- "fields": list of field definitions (name, type, required, description)
- "api_endpoint": the Tripletex endpoint used
- "notes": special instructions about how the workflow works

Used by the Chief to build its workflow catalog and by sub-agents
to get exact field specs for each workflow.
"""

TASK_SCHEMAS: dict[str, dict] = {
    "create_employee": {
        "api_endpoint": "POST /employee",
        "notes": (
            "department.id is required but will be auto-resolved by the workflow. "
            "userType defaults to STANDARD. "
            "If the prompt mentions admin/administrator role, set role to 'administrator'. "
            "IMPORTANT: If the prompt includes a START DATE (date de début/tiltredelse/fecha de inicio/Startdatum), "
            "use register_employment instead — it handles employee + employment record + start date in one call."
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
            {"name": "departmentName", "type": "string", "required": False, "description": "Department name (e.g. 'Regnskap', 'Drift'). Workflow resolves to ID or creates if needed."},
            {"name": "departmentId", "type": "integer", "required": False, "description": "Department ID if already known"},
            {"name": "role", "type": "string", "required": False, "description": "Role: 'administrator' if admin access is needed, otherwise omit"},
        ],
    },
    "create_customer": {
        "api_endpoint": "POST /customer",
        "notes": "name is the only required field. isCustomer defaults to true in the workflow. Also used for suppliers (leverandør) — set isSupplier to true.",
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
            {"name": "departmentManagerId", "type": "integer", "required": False, "description": "Employee ID of the department manager"},
        ],
    },
    "create_product": {
        "api_endpoint": "POST /product",
        "notes": "vatType is auto-resolved by the workflow if not specified. Department is optional.",
        "fields": [
            {"name": "name", "type": "string", "required": True, "description": "Product name"},
            {"name": "number", "type": "string", "required": False, "description": "Product number/SKU"},
            {"name": "costExcludingVatCurrency", "type": "number", "required": False, "description": "Cost/purchase price excluding VAT"},
            {"name": "priceExcludingVatCurrency", "type": "number", "required": False, "description": "Selling price excluding VAT"},
            {"name": "priceIncludingVatCurrency", "type": "number", "required": False, "description": "Selling price including VAT"},
            {"name": "departmentId", "type": "integer", "required": False, "description": "Department ID to link product to"},
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
            {"name": "reference", "type": "string", "required": False, "description": "Order reference text"},
            {"name": "receiverEmail", "type": "string", "required": False, "description": "Email to send order to"},
            {"name": "orderLines", "type": "array of objects", "required": True, "description": "Line items", "items": [
                {"name": "description", "type": "string", "description": "Line description (product name or service)"},
                {"name": "productNumber", "type": "string", "description": "Product number/SKU if specified in the prompt"},
                {"name": "count", "type": "number", "description": "Quantity"},
                {"name": "unitPriceExcludingVatCurrency", "type": "number", "description": "Unit price excluding VAT"},
                {"name": "vatRatePercent", "type": "number", "description": "VAT rate as percentage (e.g. 25, 15, 12, 0) if specified"},
            ]},
        ],
    },
    "create_invoice": {
        "api_endpoint": "POST /order → PUT /order/{id}/:invoice",
        "notes": (
            "Invoice creation is a 2-step process: create order, then invoice from order. "
            "Customer is resolved by the workflow. If the prompt says to create a customer, "
            "include customer details in a 'customer' object. "
            "The workflow auto-registers a bank account if missing."
        ),
        "fields": [
            {"name": "customerName", "type": "string", "required": False, "description": "Customer name (looked up or created)"},
            {"name": "customerId", "type": "integer", "required": False, "description": "Customer ID if known"},
            {"name": "customer", "type": "object {name, email, organizationNumber, phoneNumber, postalAddress, isSupplier, ...}", "required": False, "description": "Full customer details if creating new customer"},
            {"name": "invoiceDate", "type": "string (YYYY-MM-DD)", "required": True, "description": "Invoice date. Default to today."},
            {"name": "dueDate", "type": "string (YYYY-MM-DD)", "required": False, "description": "Due date for payment"},
            {"name": "invoiceComment", "type": "string", "required": False, "description": "Comment on the invoice"},
            {"name": "sendToCustomer", "type": "boolean", "required": False, "description": "Whether to send the invoice to the customer"},
            {"name": "orderLines", "type": "array of objects", "required": True, "description": "Invoice line items", "items": [
                {"name": "description", "type": "string", "description": "Line description (product/service name)"},
                {"name": "productNumber", "type": "string", "description": "Product number/SKU if specified in the prompt"},
                {"name": "count", "type": "number", "description": "Quantity (default 1)"},
                {"name": "unitPriceExcludingVatCurrency", "type": "number", "description": "Unit price excluding VAT"},
                {"name": "vatRatePercent", "type": "number", "description": "VAT rate as percentage (e.g. 25, 15, 12, 0) if specified"},
            ]},
        ],
    },
    "register_payment": {
        "api_endpoint": "PUT /invoice/{id}/:payment (query params)",
        "notes": (
            "SELF-CONTAINED: This workflow searches for the existing invoice by customer. "
            "If no invoice is found, it creates one automatically. "
            "For 'full payment' tasks, set fullPayment: true — the workflow will use the "
            "invoice's actual total amount (including VAT). Do NOT calculate the amount yourself."
        ),
        "fields": [
            {"name": "invoiceId", "type": "integer", "required": False, "description": "Invoice ID (if known)"},
            {"name": "invoiceNumber", "type": "integer", "required": False, "description": "Invoice number (if known)"},
            {"name": "customerName", "type": "string", "required": False, "description": "Customer name — workflow searches for their existing invoice"},
            {"name": "customerOrgNumber", "type": "string", "required": False, "description": "Customer org number — workflow searches for their existing invoice"},
            {"name": "fullPayment", "type": "boolean", "required": False, "description": "Set to true for 'full payment' — workflow uses invoice's actual total amount incl VAT"},
            {"name": "paidAmount", "type": "number", "required": False, "description": "Specific amount to pay (only if NOT full payment)"},
            {"name": "description", "type": "string", "required": False, "description": "Invoice line description — used if invoice must be created"},
            {"name": "amountExclVat", "type": "number", "required": False, "description": "Invoice amount excl VAT — used if invoice must be created"},
            {"name": "paymentDate", "type": "string (YYYY-MM-DD)", "required": False, "description": "Date of payment. Default to today."},
        ],
    },
    "create_credit_note": {
        "api_endpoint": "PUT /invoice/{id}/:createCreditNote (query params)",
        "notes": (
            "SELF-CONTAINED: This workflow searches for the existing invoice by customer. "
            "If no invoice is found, it creates one automatically. "
            "You only need ONE step: call create_credit_note with customer info. "
            "Do NOT create the invoice separately — the workflow handles everything."
        ),
        "fields": [
            {"name": "invoiceId", "type": "integer", "required": False, "description": "Invoice ID (if known)"},
            {"name": "invoiceNumber", "type": "integer", "required": False, "description": "Invoice number (if known)"},
            {"name": "customerName", "type": "string", "required": False, "description": "Customer name — workflow searches for their existing invoice"},
            {"name": "customerOrgNumber", "type": "string", "required": False, "description": "Customer org number — workflow searches for their existing invoice"},
            {"name": "description", "type": "string", "required": False, "description": "Invoice line description (e.g. 'Webdesign') — used if invoice must be created"},
            {"name": "amount", "type": "number", "required": False, "description": "Invoice amount excl VAT — used if invoice must be created"},
            {"name": "date", "type": "string (YYYY-MM-DD)", "required": True, "description": "Credit note date. Default to today."},
            {"name": "comment", "type": "string", "required": False, "description": "Comment on the credit note"},
            {"name": "sendToCustomer", "type": "boolean", "required": False, "description": "Whether to send credit note to customer"},
        ],
    },
    "create_travel_expense": {
        "api_endpoint": "POST /travelExpense + POST /travelExpense/cost per line",
        "notes": (
            "If the prompt names an employee (with name/email), include their details so the workflow can create them. "
            "Each cost line requires: date, amountCurrencyIncVat. Payment type is auto-resolved. "
            "Use 'comments' (not 'description') for cost line text. "
            "Per diem (dagpenger/Tagegeld/daily allowance) should be extracted separately from regular costs."
        ),
        "fields": [
            {"name": "title", "type": "string", "required": True, "description": "Travel expense title"},
            {"name": "date", "type": "string (YYYY-MM-DD)", "required": False, "description": "Travel date. Default to today."},
            {"name": "employeeFirstName", "type": "string", "required": False, "description": "Employee first name if specified in prompt"},
            {"name": "employeeLastName", "type": "string", "required": False, "description": "Employee last name if specified in prompt"},
            {"name": "employeeEmail", "type": "string", "required": False, "description": "Employee email if specified in prompt"},
            {"name": "projectId", "type": "integer", "required": False, "description": "Project ID if expense is linked to a project"},
            {"name": "departmentId", "type": "integer", "required": False, "description": "Department ID if specified"},
            {"name": "perDiem", "type": "object", "required": False, "description": "Per diem / daily allowance if mentioned", "items": [
                {"name": "days", "type": "number", "description": "Number of days"},
                {"name": "dailyRate", "type": "number", "description": "Daily rate in NOK"},
            ]},
            {"name": "costs", "type": "array of objects", "required": False, "description": "Cost/expense lines (NOT per diem)", "items": [
                {"name": "comments", "type": "string", "description": "What the expense was for (e.g. 'Taxi', 'Togbillett', 'Flugticket')"},
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
        "notes": (
            "projectManager is required — pass projectManagerEmail to set the correct person. "
            "The workflow resolves by email first, then name, then falls back to any employee. "
            "startDate defaults to today if not specified. Do not guess employee IDs."
        ),
        "fields": [
            {"name": "name", "type": "string", "required": True, "description": "Project name"},
            {"name": "projectManagerEmail", "type": "string", "required": False, "description": "Email of the project manager (PREFERRED — most reliable way to set PM)"},
            {"name": "projectManagerName", "type": "string", "required": False, "description": "Full name of PM e.g. 'Liv Haugen' (used if email not available)"},
            {"name": "projectManagerFirstName", "type": "string", "required": False, "description": "PM first name (used if email not available)"},
            {"name": "projectManagerLastName", "type": "string", "required": False, "description": "PM last name (used if email not available)"},
            {"name": "number", "type": "string", "required": False, "description": "Project number (auto-generated if omitted)"},
            {"name": "description", "type": "string", "required": False, "description": "Project description"},
            {"name": "startDate", "type": "string (YYYY-MM-DD)", "required": False, "description": "Start date"},
            {"name": "endDate", "type": "string (YYYY-MM-DD)", "required": False, "description": "End date"},
            {"name": "isInternal", "type": "boolean", "required": False, "description": "True if internal project"},
            {"name": "isFixedPrice", "type": "boolean", "required": False, "description": "True if fixed price, false if hourly"},
            {"name": "customerId", "type": "integer", "required": False, "description": "Customer ID to link this project to"},
            {"name": "departmentId", "type": "integer", "required": False, "description": "Department ID"},
            {"name": "mainProjectId", "type": "integer", "required": False, "description": "Parent project ID if this is a sub-project"},
            {"name": "customerName", "type": "string", "required": False, "description": "Customer name (resolved to ID by workflow)"},
            {"name": "customerOrgNumber", "type": "string", "required": False, "description": "Customer org number (resolved to ID by workflow)"},
        ],
    },
    "create_supplier_invoice": {
        "api_endpoint": "POST /ledger/voucher",
        "notes": (
            "Registers an incoming supplier invoice (leverandorfaktura / inngaende faktura) as a voucher. "
            "The workflow auto-creates the supplier if needed, resolves accounts, and builds correct debit/credit postings. "
            "Use this for ANY incoming invoice from a supplier/vendor. "
            "The amountInclVat is the total the supplier is charging (including VAT). "
            "The workflow calculates the VAT split automatically."
        ),
        "fields": [
            {"name": "supplierName", "type": "string", "required": True, "description": "Supplier/vendor name"},
            {"name": "supplierOrgNumber", "type": "string", "required": False, "description": "Supplier organization number"},
            {"name": "invoiceNumber", "type": "string", "required": False, "description": "Supplier's invoice reference (e.g. INV-2026-2076)"},
            {"name": "amountInclVat", "type": "number", "required": False, "description": "Total amount INCLUDING VAT (inklusiv MVA)"},
            {"name": "amountExclVat", "type": "number", "required": False, "description": "Amount EXCLUDING VAT (if given instead of incl)"},
            {"name": "vatRate", "type": "number", "required": False, "description": "VAT rate in percent (default 25). Use 0 for exempt."},
            {"name": "expenseAccount", "type": "number", "required": True, "description": "Expense account number (e.g. 7300 for office services, 6300 for consulting)"},
            {"name": "description", "type": "string", "required": False, "description": "What the invoice is for"},
            {"name": "date", "type": "string (YYYY-MM-DD)", "required": False, "description": "Invoice date (defaults to today)"},
        ],
    },
    "create_voucher": {
        "api_endpoint": "POST /ledger/voucher",
        "notes": (
            "Creates a manual journal entry / voucher with custom postings. "
            "Use this for general ledger entries, corrections, or accounting entries "
            "that don't fit other workflow patterns. Each posting is a debit (positive amount) "
            "or credit (negative amount) on an account."
        ),
        "fields": [
            {"name": "description", "type": "string", "required": True, "description": "Voucher description"},
            {"name": "date", "type": "string (YYYY-MM-DD)", "required": False, "description": "Voucher date (defaults to today)"},
            {"name": "postings", "type": "array of objects", "required": True, "description": "List of posting lines", "items": [
                {"name": "account", "type": "number", "required": True, "description": "Account number (e.g. 6300, 2400)"},
                {"name": "amount", "type": "number", "required": True, "description": "Amount: positive = debit, negative = credit"},
                {"name": "description", "type": "string", "required": False, "description": "Posting description"},
                {"name": "dimensionId", "type": "integer", "required": False, "description": "Accounting dimension value ID to link this posting to (from /ledger/accountingDimensionValue)"},
                {"name": "dimensionIndex", "type": "integer", "required": False, "description": "Which dimension slot (1, 2, or 3) — defaults to 1"},
            ]},
        ],
    },
    "register_time": {
        "api_endpoint": "POST /timesheet/entry",
        "notes": (
            "Registers timesheet hours for an employee on a project activity. "
            "The workflow resolves employee, project, and activity by name/email. "
            "Only one entry per employee/date/activity/project combination is allowed. "
            "The employee and project must exist first (use create_employee and create_project workflows)."
        ),
        "fields": [
            {"name": "employeeEmail", "type": "string", "required": False, "description": "Employee email to identify them"},
            {"name": "employeeFirstName", "type": "string", "required": False, "description": "Employee first name"},
            {"name": "employeeLastName", "type": "string", "required": False, "description": "Employee last name"},
            {"name": "projectName", "type": "string", "required": False, "description": "Project name to register hours on"},
            {"name": "projectId", "type": "integer", "required": False, "description": "Project ID (if known)"},
            {"name": "activityName", "type": "string", "required": False, "description": "Activity name (e.g. 'Utvikling', 'Konsultering')"},
            {"name": "hours", "type": "number", "required": True, "description": "Number of hours to register"},
            {"name": "date", "type": "string (YYYY-MM-DD)", "required": False, "description": "Date for the entry (defaults to today)"},
            {"name": "chargeableHours", "type": "number", "required": False, "description": "Billable hours (defaults to same as hours)"},
        ],
    },
    "register_employment": {
        "api_endpoint": "POST /employee/employment + POST /employee/employment/details + POST /employee/standardTime",
        "notes": (
            "Registers a full employment contract: creates employee, employment record, employment details "
            "(STYRK/occupation code, salary, percentage), and standard working hours. Self-contained — "
            "handles department creation, employee creation, everything in one call. "
            "Use this for employment contracts (arbeidskontrakt/tilbudsbrev) from PDFs."
        ),
        "fields": [
            {"name": "firstName", "type": "string", "required": True, "description": "Employee first name"},
            {"name": "lastName", "type": "string", "required": True, "description": "Employee last name"},
            {"name": "email", "type": "string", "required": False, "description": "Employee email"},
            {"name": "dateOfBirth", "type": "string (YYYY-MM-DD)", "required": False, "description": "Date of birth"},
            {"name": "nationalIdentityNumber", "type": "string", "required": False, "description": "National identity number (fødselsnummer)"},
            {"name": "bankAccountNumber", "type": "string", "required": False, "description": "Bank account number"},
            {"name": "departmentName", "type": "string", "required": False, "description": "Department name (e.g. 'Regnskap', 'Drift')"},
            {"name": "startDate", "type": "string (YYYY-MM-DD)", "required": True, "description": "Employment start date (tiltredelse)"},
            {"name": "occupationCode", "type": "string", "required": False, "description": "STYRK occupation code (e.g. '2411')"},
            {"name": "percentageOfFullTimeEquivalent", "type": "number", "required": False, "description": "Employment percentage (e.g. 100 for full-time)"},
            {"name": "annualSalary", "type": "number", "required": False, "description": "Annual salary in NOK"},
            {"name": "hoursPerDay", "type": "number", "required": False, "description": "Standard working hours per day (e.g. 7.5)"},
            {"name": "employmentType", "type": "string", "required": False, "description": "ORDINARY (default), MARITIME, FREELANCE"},
            {"name": "employmentForm", "type": "string", "required": False, "description": "PERMANENT (default), TEMPORARY"},
            {"name": "remunerationType", "type": "string", "required": False, "description": "MONTHLY_WAGE (default), HOURLY_WAGE, FEE"},
        ],
    },
    "reconcile_bank_statement": {
        "api_endpoint": "GET /invoice + PUT /invoice/{id}/:payment (bulk)",
        "notes": (
            "Reconciles a bank statement CSV against open invoices in Tripletex. "
            "Pass the ENTIRE CSV content as csvContent — the workflow parses it internally, "
            "matches incoming payments to customer invoices by amount, and registers payments. "
            "Handles partial payments correctly. Use this for ANY bank reconciliation task (bankavsteming)."
        ),
        "fields": [
            {"name": "csvContent", "type": "string", "required": True, "description": "The full CSV text from the bank statement file. Pass the entire file content."},
        ],
    },
    "register_expense": {
        "api_endpoint": "POST /ledger/voucher",
        "notes": (
            "Registers an expense from a receipt/kvittering as a voucher with correct VAT and department allocation. "
            "Creates debit posting on expense account (with input VAT) and credit posting on bank account (1920). "
            "Use this for receipt-based expense registration (kvittering/recibo/Quittung/reçu)."
        ),
        "fields": [
            {"name": "description", "type": "string", "required": True, "description": "What the expense is for (e.g. 'Oppbevaringsboks')"},
            {"name": "amountInclVat", "type": "number", "required": True, "description": "Total amount INCLUDING VAT from the receipt"},
            {"name": "vatRate", "type": "number", "required": False, "description": "VAT rate in percent (default 25)"},
            {"name": "expenseAccount", "type": "number", "required": True, "description": "Expense account number (e.g. 6540 inventar, 7140 reise)"},
            {"name": "departmentName", "type": "string", "required": False, "description": "Department to allocate expense to"},
            {"name": "date", "type": "string (YYYY-MM-DD)", "required": False, "description": "Receipt/expense date"},
            {"name": "supplierName", "type": "string", "required": False, "description": "Who issued the receipt (e.g. 'Biltema')"},
        ],
    },
    "analyze_ledger": {
        "api_endpoint": "GET /ledger/posting",
        "notes": (
            "Analyzes ledger postings for a date range and detects accounting errors: imbalanced vouchers, "
            "duplicate postings, orphaned VAT entries. Returns structured error list with voucher summaries. "
            "Use this FIRST for ledger correction tasks, then use create_voucher to post corrective entries. "
            "For error correction (retting/correction/Korrektur/correction/correção): analyze_ledger → create_voucher."
        ),
        "fields": [
            {"name": "dateFrom", "type": "string (YYYY-MM-DD)", "required": False, "description": "Start date for analysis (default: Jan 1 current year)"},
            {"name": "dateTo", "type": "string (YYYY-MM-DD)", "required": False, "description": "End date for analysis (default: Feb 28 current year)"},
            {"name": "accountFrom", "type": "integer", "required": False, "description": "Optional: only analyze accounts from this number"},
            {"name": "accountTo", "type": "integer", "required": False, "description": "Optional: only analyze accounts up to this number"},
        ],
    },
}
