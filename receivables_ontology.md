# TalkingTotals Receivables Insight Ontology v0.2

## Design principle

Tally is configurable. Preserve company-defined names and hierarchy paths as data. Do not assume a voucher type, ledger group, or cost centre has a universal business meaning across companies.

## Canonical receivable fact: `ReceivableBill`

Required:
- `company_id`
- `customer_id`
- `customer_name`
- `bill_no`
- `bill_date`
- `due_date` (nullable)
- `original_amount`
- `pending_amount`

Recommended Tally dimensions:
- `voucher_type_id`, `voucher_type_name`: e.g. Sales, Sales Spares, Sales Service, Sales Machines
- `customer_ledger_group_id`, `customer_ledger_group_name`, `customer_ledger_group_path`: e.g. Sundry Debtors > Machine Group
- `cost_centres`: zero, one, or many allocations linked to the invoice/bill

Optional additional dimensions:
- area, salesperson, last receipt date/amount, and other company-defined dimensions

## Dimension semantics

- **Voucher type** classifies the voucher/transaction. Names are company-defined.
- **Customer ledger group** classifies the customer ledger within the company's chart-of-accounts hierarchy. Preserve both leaf name and full path where possible.
- **Cost centre** is a one-to-many allocation dimension. An invoice may have multiple cost centres.

These dimensions are independent. For example, a company might use one `Sales` voucher type and distinguish Machines vs Spares through customer ledger groups; another company might use separate voucher types. The engine must support both without changing code.

## Multiple cost centres: no double counting

Store cost centres in a child/bridge table, not as a single string in the bill row.

Each allocation can contain:
- cost centre ID/name/path
- `allocated_pending_amount`, if the outstanding receivable can be reliably allocated to that cost centre
- `allocation_pct`, if a verified allocation percentage exists
- allocation status / provenance

Important: a cost centre on the original invoice does **not automatically prove** that the remaining outstanding can be allocated in the same proportion. Payment allocations, credit notes, or other adjustments may change the split.

Rules:
1. If verified pending amounts exist for each cost centre, use those amounts.
2. Else, if verified allocation percentages exist and sum appropriately to 100%, apply them to the pending amount.
3. If there is one cost centre and no split is needed, the full bill may be assigned to it if that mapping is verified.
4. If there are multiple cost centres but no defensible allocation, report `Multiple cost centres - allocation unknown`; do not assign the full invoice to every centre.
5. Report any allocation reconciliation difference; do not silently force allocations to equal the bill balance.

## Ageing rule

If `due_date` exists, ageing is based on due date. Otherwise, the engine currently falls back to bill date and should label this as invoice age, not overdue days. For credit-policy/overdue analysis, obtain due date or agreed credit days.

Buckets:
- 0-30
- 31-60
- 61-90
- 91-180
- 181-365
- >365

## Dimension breakdowns

The engine exposes breakdowns by:
- voucher type
- customer ledger group
- customer ledger group path
- cost centre (allocation-aware)

The same generic aggregation pattern can later support company-defined tags, territory, sales team, product line, project, or branch, without hardcoding their values into the engine.

## Insight categories

- `AR.AGE`
- `AR.CUSTOMER`
- `AR.CONCENTRATION`
- `AR.PAYMENT_BEHAVIOUR`
- `AR.NO_PAYMENT`
- `AR.CREDIT_POLICY`
- `AR.VOUCHER_TYPE`
- `AR.CUSTOMER_LEDGER_GROUP`
- `AR.COST_CENTRE`
- `AR.RECOVERY`
- `AR.FINANCIAL_IMPACT`

## Financial impact vocabulary

- `cash_released`: existing customer money collected.
- `potential_debt_reduction`: portion of recovered cash that could reduce short-term borrowing.
- `annual_interest_saving`: potential debt reduction multiplied by the applicable annual borrowing rate.
- `pre_tax_profit_impact`: interest saving, assuming borrowing would otherwise remain outstanding.
- `post_tax_profit_impact`: pre-tax impact multiplied by (1 - effective tax rate).

Collection of an existing receivable improves cash flow / working capital. It is a direct P&L benefit only where recovered cash reduces interest-bearing borrowing or avoids a borrowing cost. Collection itself is not new sales or operating profit.

## Confidence classes

- `FACT`: direct source fact
- `CALCULATED`: deterministic result from facts
- `SCENARIO`: relies on an explicit assumption, such as recovery rate
- `ESTIMATE`: depends on incomplete or external data
- `NEEDS_CONFIRMATION`: requires owner/accountant validation

Never present scenario or estimate values as facts.


## v0.3: Product classification for receivables

The standard receivables engine does **not** report by individual stock item.
It may report by the invoice-level `stock_item_group` and `stock_item_category`
when the source can establish one effective classification per invoice. These
are independent dimensions; do not assume stock groups and categories form one
shared hierarchy.

Classification rules:
- One distinct value on invoice lines: use that value.
- Multiple distinct values: use `MULTIPLE` unless a documented, deterministic
  company rule provides one classification.
- Missing/incomplete linkage: use `Not specified`.
- Never allocate the outstanding balance across stock items or categories based
  only on the original invoice lines. Partial receipts may not settle invoice
  lines proportionately.
- Cost-centre allocations remain a separate one-to-many dimension and must be
  aggregated separately to avoid multiplying invoice balances.
