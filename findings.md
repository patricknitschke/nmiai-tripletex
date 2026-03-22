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
- create_voucher now does strict pre-validation of all posting accounts via one batched GET /ledger/account, creates missing accounts with POST /ledger/account, then re-validates before POST /ledger/voucher.
- create_voucher now fails fast on postings that omit both amount and amountGross; it no longer silently treats missing amounts as 0.
- Chief + Senior prompts now enforce month-end sequence: account discovery -> missing-account creation -> one voucher per journal entry -> optional balance-sheet verification when requested.
- Month-end prompt guidance now explicitly requires linear depreciation crediting accumulated depreciation contra-account (e.g. 1209), not gross asset account 1200.