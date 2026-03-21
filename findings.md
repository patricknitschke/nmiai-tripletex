# Findings

- Current payroll workflow auto-creates an employee when employeeId is missing, even if a matching employee already exists.
- Current payroll workflow fabricates dateOfBirth as 1990-01-01 when employment creation needs a DOB.
- Current payroll workflow defaults year and month from today even when an explicit payroll date is provided.
- Current payroll workflow uses a dangerous fallback to the first salary type returned by the API.
- Payroll now derives default year and month from the explicit transaction date when provided.
- Payroll now reuses an exact employee match by email or first/last name before taking any create path.
- Payroll now requires a real dateOfBirth before creating employment and no longer fabricates 1990-01-01.
- Payroll salary-type lookup now filters isInactive=false, uses one search for base salary and one for bonus, and fails loudly if no active base salary type is found.
- Payroll posts /salary/transaction with generateTaxDeduction=true by default and allows callers to override it.