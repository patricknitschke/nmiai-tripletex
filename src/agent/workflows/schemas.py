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
        "notes": "name is the only required field. isCustomer is readOnly (server defaults to true). Also used for suppliers (leverandør) — set isSupplier to true.",
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
            {"name": "departmentManagerId", "type": "integer", "required": False, "description": "Employee ID of the department manager (workflow maps to departmentManager: {id: X})"},
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
            {"name": "departmentId", "type": "integer", "required": False, "description": "Department ID to link product to (workflow maps to department: {id: X})"},
        ],
    },
    "create_order": {
        "api_endpoint": "POST /order",
        "notes": (
            "customer is resolved by the workflow (by name or ID). "
            "orderLines use workflow field names — the workflow resolves them to API objects. "
            "productNumber → product lookup → product: {id: X}. "
            "vatRatePercent → vatType lookup → vatType: {id: X}."
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
                {"name": "productNumber", "type": "string", "description": "Product number/SKU if specified (workflow resolves to product: {id: X})"},
                {"name": "count", "type": "number", "description": "Quantity"},
                {"name": "unitPriceExcludingVatCurrency", "type": "number", "description": "Unit price excluding VAT"},
                {"name": "vatRatePercent", "type": "number", "description": "VAT rate as percentage e.g. 25, 15, 12, 0 (workflow resolves to vatType: {id: X})"},
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
                {"name": "productNumber", "type": "string", "description": "Product number/SKU if specified (workflow resolves to product: {id: X})"},
                {"name": "count", "type": "number", "description": "Quantity (default 1)"},
                {"name": "unitPriceExcludingVatCurrency", "type": "number", "description": "Unit price excluding VAT"},
                {"name": "vatRatePercent", "type": "number", "description": "VAT rate as percentage e.g. 25, 15, 12, 0 (workflow resolves to vatType: {id: X})"},
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
            "SELF-CONTAINED: Finds the existing invoice by customer org/name + description + amount, "
            "then creates a credit note that fully reverses it. The invoice MUST already exist. "
            "Use this for: credit notes, payment reversals (returned by bank), invoice cancellations. "
            "NEVER use register_payment with negative amounts for reversals — always use this workflow."
        ),
        "fields": [
            {"name": "invoiceId", "type": "integer", "required": False, "description": "Invoice ID (if known)"},
            {"name": "invoiceNumber", "type": "integer", "required": False, "description": "Invoice number (if known)"},
            {"name": "customerName", "type": "string", "required": False, "description": "Customer name — used to search for the invoice"},
            {"name": "customerOrgNumber", "type": "string", "required": False, "description": "Customer org number — used to search for the invoice"},
            {"name": "description", "type": "string", "required": False, "description": "Invoice line description (e.g. 'Design web') — helps match the correct invoice"},
            {"name": "amountExclVat", "type": "number", "required": False, "description": "Invoice amount excl VAT — helps match the correct invoice"},
            {"name": "date", "type": "string (YYYY-MM-DD)", "required": True, "description": "Credit note date. Default to today."},
            {"name": "comment", "type": "string", "required": False, "description": "Comment on the credit note"},
            {"name": "sendToCustomer", "type": "boolean", "required": False, "description": "Whether to send credit note to customer. Default false for reversals."},
        ],
    },
    "create_travel_expense": {
        "api_endpoint": "POST /travelExpense + POST /travelExpense/perDiemCompensation + POST /travelExpense/cost per line",
        "notes": (
            "If the prompt names an employee (with name/email), include their details so the workflow can create them. "
            "Each cost line requires: date, amountCurrencyIncVat. Payment type is auto-resolved. "
            "Use 'comments' (not 'description') for cost line text. "
            "Per diem (dagpenger/Tagegeld/daily allowance) should be extracted separately from regular costs."
        ),
        "fields": [
            {"name": "title", "type": "string", "required": True, "description": "Travel expense title"},
            {"name": "date", "type": "string (YYYY-MM-DD)", "required": False, "description": "Travel date. Default to today. (readOnly on API — workflow maps to travelDetails dates)"},
            {"name": "employeeFirstName", "type": "string", "required": False, "description": "Employee first name if specified in prompt"},
            {"name": "employeeLastName", "type": "string", "required": False, "description": "Employee last name if specified in prompt"},
            {"name": "employeeEmail", "type": "string", "required": False, "description": "Employee email if specified in prompt"},
            {"name": "projectId", "type": "integer", "required": False, "description": "Project ID if expense is linked to a project"},
            {"name": "departmentId", "type": "integer", "required": False, "description": "Department ID if specified"},
            {"name": "departureDate", "type": "string (YYYY-MM-DD)", "required": False, "description": "Departure date. Defaults to travel date. (mapped to travelDetails.departureDate)"},
            {"name": "returnDate", "type": "string (YYYY-MM-DD)", "required": False, "description": "Return date. Auto-calc from departureDate + perDiem days. (mapped to travelDetails.returnDate)"},
            {"name": "perDiem", "type": "object", "required": False, "description": "Per diem / daily allowance if mentioned", "items": [
                {"name": "days", "type": "number", "description": "Number of days (workflow maps to API field 'count')"},
                {"name": "dailyRate", "type": "number", "description": "Daily rate in NOK (workflow maps to API field 'rate')"},
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
            "startDate defaults to today if not specified. Do not guess employee IDs. "
            "Supports activityName to embed a project activity at creation time (no separate call needed). "
            "For MULTIPLE projects, use create_projects_batch — it resolves PM once and loops POST /project."
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
            {"name": "activityName", "type": "string", "required": False, "description": "Name of a project activity to create with the project (created separately via POST /project/projectActivity)"},
            {"name": "projectActivities", "type": "array", "required": False, "description": "List of activities — strings or {name, activityType} dicts. Created separately after project."},
            {"name": "projectManagerId", "type": "integer", "required": False, "description": "PM employee ID if already known (skips resolution — use when caching from a previous call)"},
        ],
    },
    "create_projects_batch": {
        "api_endpoint": "POST /project (loop)",
        "notes": (
            "Creates MULTIPLE projects by looping POST /project. Resolves PM once and reuses for all projects. "
            "Creates activities separately via POST /project/projectActivity. More efficient than calling create_project in a loop "
            "because PM resolution happens only once. Use this when the task asks to create 2+ projects."
        ),
        "fields": [
            {"name": "projects", "type": "array", "required": True, "description": "List of project dicts. Each must have 'name', may have 'activityName', 'isInternal', etc.",
             "items": [
                 {"name": "name", "type": "string", "description": "Project name"},
                 {"name": "activityName", "type": "string", "description": "Activity to embed in this project"},
                 {"name": "isInternal", "type": "boolean", "description": "True if internal"},
                 {"name": "number", "type": "string", "description": "Project number"},
                 {"name": "startDate", "type": "string", "description": "Start date (YYYY-MM-DD)"},
             ]},
            {"name": "projectManagerEmail", "type": "string", "required": False, "description": "Shared PM email (resolved once for all projects)"},
            {"name": "projectManagerName", "type": "string", "required": False, "description": "Shared PM full name"},
            {"name": "projectManagerId", "type": "integer", "required": False, "description": "Shared PM ID if already known"},
            {"name": "startDate", "type": "string", "required": False, "description": "Shared start date for all projects (default: today)"},
            {"name": "isInternal", "type": "boolean", "required": False, "description": "Shared isInternal flag for all projects"},
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
            {"name": "projectId", "type": "integer", "required": False, "description": "Project ID to link the expense to (for project lifecycle tasks with supplier costs)"},
        ],
    },
    "create_dimension": {
        "api_endpoint": "POST /ledger/accountingDimensionName + POST /ledger/accountingDimensionValue",
        "notes": (
            "Creates a custom accounting dimension with values in one call. "
            "The dimension index (1-3) is auto-assigned by Tripletex. "
            "Returns value IDs that can be passed as dimensionId to create_voucher postings. "
            "Use this BEFORE create_voucher when the task asks to create a dimension AND post a voucher."
        ),
        "fields": [
            {"name": "dimensionName", "type": "string", "required": True, "description": "Name of the dimension (e.g. 'Region', 'Prosjekttype')"},
            {"name": "description", "type": "string", "required": False, "description": "Description of the dimension"},
            {"name": "values", "type": "array of strings", "required": True, "description": "List of dimension value names (e.g. ['Sør-Norge', 'Midt-Norge'])"},
        ],
    },
    "create_voucher": {
        "api_endpoint": "POST /ledger/voucher",
        "notes": (
            "Creates a manual journal entry / voucher with custom postings. "
            "Use this for general ledger entries, corrections, or accounting entries "
            "that don't fit other workflow patterns. Each posting is a debit (positive amount) "
            "or credit (negative amount) on an account. "
            "NOTE: Posting fields are workflow abstractions — 'account' (number) is resolved to account: {id: X}, "
            "'dimensionId' + 'dimensionIndex' are mapped to freeAccountingDimension{N}: {id: X}. "
            "IMPORTANT: For postings on AR accounts (1500-1599), you MUST provide customerName "
            "or customerId at the top level — Tripletex requires a customer reference on AR postings."
        ),
        "fields": [
            {"name": "description", "type": "string", "required": True, "description": "Voucher description"},
            {"name": "date", "type": "string (YYYY-MM-DD)", "required": False, "description": "Voucher date (defaults to today)"},
            {"name": "customerName", "type": "string", "required": False, "description": "Customer name — auto-attached to AR account postings (1500-1599). REQUIRED when posting to 1500."},
            {"name": "customerId", "type": "integer", "required": False, "description": "Customer ID — auto-attached to AR account postings (1500-1599). Use if ID is known."},
            {"name": "postings", "type": "array of objects", "required": True, "description": "List of posting lines", "items": [
                {"name": "account", "type": "number", "required": True, "description": "Account number e.g. 6300, 2400 (workflow resolves to account: {id: X})"},
                {"name": "amount", "type": "number", "required": True, "description": "Amount: positive = debit, negative = credit"},
                {"name": "description", "type": "string", "required": False, "description": "Posting description"},
                {"name": "dimensionId", "type": "integer", "required": False, "description": "Dimension value ID (workflow maps to freeAccountingDimension{N}: {id: X})"},
                {"name": "dimensionIndex", "type": "integer", "required": False, "description": "Which dimension slot (1, 2, or 3) — defaults to 1. Maps to freeAccountingDimension{N}"},
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
            {"name": "chargeableHours", "type": "number", "required": False, "description": "Billable hours (defaults to same as hours). NOTE: API field chargeableHours is readOnly — workflow passes it but Tripletex may ignore; the writable equivalent on the project level is managed separately."},
            {"name": "hourlyRate", "type": "number", "required": False, "description": "Hourly rate (NOK/h). NOT set on timesheet entry (readOnly there) — workflow sets it via POST /project/hourlyRates instead. Must be set BEFORE invoicing."},
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
            {"name": "employmentForm", "type": "string", "required": False, "description": "PERMANENT (default), TEMPORARY, PERMANENT_AND_HIRED_OUT, TEMPORARY_AND_HIRED_OUT, TEMPORARY_ON_CALL, NOT_CHOSEN"},
            {"name": "remunerationType", "type": "string", "required": False, "description": "MONTHLY_WAGE (default), HOURLY_WAGE, FEE, COMMISION_PERCENTAGE, PIECEWORK_WAGE, NOT_CHOSEN"},
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
    "register_payroll": {
        "api_endpoint": "POST /salary/transaction",
        "notes": (
            "Executes payroll (nómina/lønn/Gehaltsabrechnung/paie) for an employee. "
            "Self-contained: finds employee by email, ensures employment record exists, "
            "looks up salary types, and creates the salary transaction with specifications. "
            "Use baseSalary for monthly base pay and bonus for one-time additions."
        ),
        "fields": [
            {"name": "firstName", "type": "string", "required": False, "description": "Employee first name"},
            {"name": "lastName", "type": "string", "required": False, "description": "Employee last name"},
            {"name": "email", "type": "string", "required": False, "description": "Employee email (preferred for lookup)"},
            {"name": "baseSalary", "type": "number", "required": False, "description": "Monthly base salary amount in NOK"},
            {"name": "bonus", "type": "number", "required": False, "description": "One-time bonus amount in NOK (added on top of base salary)"},
            {"name": "year", "type": "integer", "required": False, "description": "Payroll year (defaults to current year)"},
            {"name": "month", "type": "integer", "required": False, "description": "Payroll month (defaults to current month)"},
            {"name": "date", "type": "string (YYYY-MM-DD)", "required": False, "description": "Transaction date (defaults to today)"},
        ],
    },
    "analyze_ledger": {
        "api_endpoint": "GET /ledger/posting",
        "notes": (
            "Analyzes ledger postings for a date range and detects accounting errors: imbalanced vouchers, "
            "duplicate postings, orphaned VAT entries. Returns structured error list with account numbers and voucher summaries. "
            "Use this FIRST for ledger correction tasks, then use create_voucher to post corrective entries. "
            "For error correction (retting/correction/Korrektur/correction/correção): analyze_ledger → create_voucher. "
            "IMPORTANT for missing-VAT corrections: do NOT post directly to account 2710 (system-managed). Instead: "
            "(1) create_voucher to reverse the original no-VAT posting, then (2) register_expense with amountInclVat "
            "(original amount × 1.25 for 25% VAT) — this auto-generates the 2710 VAT posting. "
            "NOTE: For EXPENSE COMPARISON across months (not error correction), use compare_expenses instead — it "
            "fetches actual postings from /ledger/posting and aggregates by account per month, returning top_increases."
        ),
        "fields": [
            {"name": "dateFrom", "type": "string (YYYY-MM-DD)", "required": False, "description": "Start date for analysis (default: Jan 1 current year)"},
            {"name": "dateTo", "type": "string (YYYY-MM-DD)", "required": False, "description": "End date for analysis EXCLUSIVE (default: first day of next month after current range)"},
            {"name": "accountFrom", "type": "integer", "required": False, "description": "Optional: only analyze accounts from this number"},
            {"name": "accountTo", "type": "integer", "required": False, "description": "Optional: only analyze accounts up to this number"},
        ],
    },
    "compare_expenses": {
        "api_endpoint": "GET /ledger/posting",
        "notes": (
            "Compares ACTUAL expenses across months using posted ledger data. Fetches all postings in "
            "the given date range and expense account range, aggregates by account per month, and "
            "automatically computes the largest month-over-month increase for each account. "
            "Returns top_increases (sorted by largest increase) and top_accounts (by absolute total). "
            "The workflow auto-detects which months are present — no need to specify month numbers. "
            "CRITICAL: dateTo is EXCLUSIVE — to include all of month N, use the 1st of month N+1. "
            "Example: Jan+Feb → dateFrom=2026-01-01 dateTo=2026-03-01. Mar+Apr → dateFrom=2026-03-01 dateTo=2026-05-01. "
            "Do NOT use /resultbudget/company — it returns budget data (0 entries if no budgets configured)."
        ),
        "fields": [
            {"name": "dateFrom", "type": "string (YYYY-MM-DD)", "required": True, "description": "Start date — first day of the earlier month"},
            {"name": "dateTo", "type": "string (YYYY-MM-DD)", "required": True, "description": "End date EXCLUSIVE — first day AFTER the later month"},
            {"name": "accountFrom", "type": "integer", "required": False, "description": "Expense account range start (default: 4000)"},
            {"name": "accountTo", "type": "integer", "required": False, "description": "Expense account range end (default: 8999)"},
            {"name": "topN", "type": "integer", "required": False, "description": "Number of top accounts to return (default: 10)"},
        ],
    },
    "verify_trial_balance": {
        "api_endpoint": "GET /balanceSheet",
        "notes": (
            "Validates that the trial balance is in equilibrium at a period-end snapshot. "
            "Use this for month-end/year-end checks instead of manually interpreting raw /balanceSheet responses. "
            "Returns balanced=true/false with parsed debit and credit totals. "
            "IMPORTANT: dateTo is EXCLUSIVE and this check uses snapshot semantics (dateTo only, no dateFrom)."
        ),
        "fields": [
            {"name": "dateTo", "type": "string (YYYY-MM-DD)", "required": True, "description": "Snapshot date EXCLUSIVE. Example: year-end 2025 => 2026-01-01"},
        ],
    },
    "register_fx_payment": {
        "api_endpoint": "PUT /invoice/{id}/:payment + POST /ledger/voucher",
        "notes": (
            "Registers payment on a foreign-currency invoice AND posts the exchange difference voucher "
            "(disagio = loss on 8060, agio = gain on 8160). Self-contained: finds the invoice, registers "
            "payment at the actual NOK received, calculates exchange difference, posts disagio/agio. "
            "Rates are OPTIONAL — if omitted, fetches official Norges Bank rates via Tripletex API for the given dates. "
            "Use this for ANY task mentioning exchange rate differences, 'disagio', 'agio', valutadifferanse, "
            "différence de change, diferença cambial, Wechselkursdifferenz."
        ),
        "fields": [
            {"name": "customerName", "type": "string", "required": False, "description": "Customer name to find the invoice"},
            {"name": "customerOrgNumber", "type": "string", "required": False, "description": "Customer org number"},
            {"name": "invoiceId", "type": "integer", "required": False, "description": "Invoice ID if known"},
            {"name": "invoiceNumber", "type": "integer", "required": False, "description": "Invoice number if known"},
            {"name": "invoiceAmountForeign", "type": "number", "required": True, "description": "Invoice amount in foreign currency (e.g. 19074 EUR)"},
            {"name": "invoiceRate", "type": "number", "required": False, "description": "Exchange rate at invoice time (e.g. 11.69 NOK/EUR). If omitted, fetched from API using invoiceDate."},
            {"name": "paymentRate", "type": "number", "required": False, "description": "Exchange rate at payment time (e.g. 11.28 NOK/EUR). If omitted, fetched from API using paymentDate."},
            {"name": "invoiceDate", "type": "string (YYYY-MM-DD)", "required": False, "description": "Date of original invoice (used for rate lookup when invoiceRate omitted)"},
            {"name": "currency", "type": "string", "required": False, "description": "Currency code (EUR, USD, GBP etc). Default EUR."},
            {"name": "paymentDate", "type": "string (YYYY-MM-DD)", "required": False, "description": "Date of payment"},
            {"name": "description", "type": "string", "required": False, "description": "Description for the exchange difference voucher"},
        ],
    },
    "create_project_invoice": {
        "api_endpoint": "POST /order + PUT /order/{id}/:invoice",
        "notes": (
            "Invoices a project's work to the linked customer. Two modes: "
            "(1) Fixed-price: invoice a percentage or specific amount of the project's fixed price. "
            "(2) Time-based: invoice from registered timesheet hours at their rates. "
            "Self-contained: finds the project, gathers hours, builds invoice lines, creates order, invoices. "
            "Use this for project invoicing tasks (prosjektfaktura/factura de proyecto/fatura de projeto/Projektrechnung). "
            "The project must already exist with a linked customer."
        ),
        "fields": [
            {"name": "projectId", "type": "integer", "required": False, "description": "Project ID if known"},
            {"name": "projectNumber", "type": "string", "required": False, "description": "Project number"},
            {"name": "projectName", "type": "string", "required": False, "description": "Project name to search for"},
            {"name": "invoicePercent", "type": "number", "required": False, "description": "Percentage of fixed price to invoice (e.g. 50 for 50%)"},
            {"name": "invoiceAmount", "type": "number", "required": False, "description": "Specific amount to invoice (overrides percent)"},
            {"name": "includeHours", "type": "boolean", "required": False, "description": "If True, invoice based on registered timesheet hours instead of fixed price"},
            {"name": "hourlyRate", "type": "number", "required": False, "description": "Override hourly rate for time-based invoicing"},
            {"name": "description", "type": "string", "required": False, "description": "Invoice description/comment"},
            {"name": "invoiceDate", "type": "string (YYYY-MM-DD)", "required": False, "description": "Invoice date (default today)"},
            {"name": "sendToCustomer", "type": "boolean", "required": False, "description": "Whether to send the invoice"},
        ],
    },
    "create_dimension_voucher": {
        "api_endpoint": "POST /ledger/accountingDimensionName + POST /ledger/accountingDimensionValue + POST /ledger/voucher",
        "notes": (
            "All-in-one: creates a custom accounting dimension with values, then posts a voucher "
            "linked to one of the values. Eliminates the risk of losing the dimension value ID "
            "between steps. Use this when the task asks to BOTH create a dimension AND post a voucher. "
            "If the task only creates a dimension (no voucher), use create_dimension instead."
        ),
        "fields": [
            {"name": "dimensionName", "type": "string", "required": True, "description": "Name of the dimension (e.g. 'Region', 'Kostsenter')"},
            {"name": "values", "type": "array of strings", "required": True, "description": "Dimension value names (e.g. ['Sør-Norge', 'Midt-Norge'])"},
            {"name": "linkValue", "type": "string", "required": False, "description": "Which dimension value to link the voucher to (e.g. 'Sør-Norge'). Defaults to first value."},
            {"name": "voucherAccount", "type": "number", "required": False, "description": "Account number for the voucher debit (e.g. 6540, 6300, 7300)"},
            {"name": "voucherAmount", "type": "number", "required": False, "description": "Amount for the voucher posting"},
            {"name": "voucherDescription", "type": "string", "required": False, "description": "Voucher description"},
            {"name": "voucherDate", "type": "string (YYYY-MM-DD)", "required": False, "description": "Voucher date"},
            {"name": "balancingAccount", "type": "number", "required": False, "description": "Credit/balancing account (default 2400)"},
        ],
    },
    "find_overdue_invoices": {
        "api_endpoint": "GET /invoice (filtered)",
        "notes": (
            "Finds overdue invoices (dueDate < today, amountOutstanding > 0). "
            "Returns the most overdue invoice with customer details. Use this as the FIRST step "
            "for any task mentioning overdue invoices, reminder fees (Mahngebühr), or late fees. "
            "The result includes customerName, customerId, invoiceId, and amountOutstanding — "
            "pass these to subsequent send_reminder and register_payment steps. "
            "NEVER fabricate a customer name — this workflow finds the REAL customer."
        ),
        "fields": [
            {"name": "customerName", "type": "string", "required": False, "description": "Optional: filter by customer name"},
            {"name": "customerOrgNumber", "type": "string", "required": False, "description": "Optional: filter by organization number"},
            {"name": "minAmount", "type": "number", "required": False, "description": "Optional: minimum outstanding amount"},
        ],
    },
    "send_reminder": {
        "api_endpoint": "PUT /invoice/{id}/:createReminder",
        "notes": (
            "Sends a reminder for an overdue invoice. Uses PUT /invoice/{id}/:createReminder with "
            "includeCharge=true to handle EVERYTHING in ONE write call: creates reminder, adds the "
            "charge (purregebyr), auto-generates accounting entries (debit 1500 AR, credit 3400 "
            "reminder income), and sends to the customer. MUCH more efficient than the old manual "
            "voucher + order + invoice path (1 write vs 3 writes). Norwegian reminder fees are "
            "VAT-exempt (0%). Falls back to POST /invoice with 0% VAT if createReminder fails. "
            "ALWAYS use after find_overdue_invoices — pass invoiceId from that result."
        ),
        "fields": [
            {"name": "invoiceId", "type": "integer", "required": True, "description": "The overdue invoice ID (from find_overdue_invoices)"},
            {"name": "reminderType", "type": "string", "required": False, "description": "SOFT_REMINDER | REMINDER | NOTICE_OF_DEBT_COLLECTION | DEBT_COLLECTION (default: REMINDER). Workflow maps to API param 'type'."},
            {"name": "includeCharge", "type": "boolean", "required": False, "description": "Include reminder fee / purregebyr (default: true — intentional override, API defaults to false)"},
            {"name": "includeInterest", "type": "boolean", "required": False, "description": "Include interest on overdue amount (default: false)"},
            {"name": "chargeAmount", "type": "number", "required": False, "description": "Reminder fee amount in NOK (default: 65, standard Norwegian purregebyr)"},
            {"name": "customerId", "type": "integer", "required": False, "description": "Customer ID (for fallback invoice path)"},
            {"name": "customerName", "type": "string", "required": False, "description": "Customer name (for fallback invoice path)"},
        ],
    },
}
