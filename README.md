# TalkingTotals Receivables Intelligence Engine v0.2

This version adds company-configurable Tally dimensions to the receivables model.

## Files

- `receivables_engine.py` — deterministic ageing, customer analysis, financial-impact calculations, and dimension breakdowns.
- `receivables_sql.sql` — canonical SQL structure plus a one-to-many cost-centre bridge table.
- `receivables_ontology.md` — dimension semantics and safeguards.

## New dimensions

1. **Voucher type** — preserve exact company-defined voucher type name and ID.
2. **Customer ledger group** — preserve group name, ID, and full hierarchy path where available.
3. **Cost centres** — zero, one, or multiple per invoice, stored as child allocations.

No business logic should assume that every company uses `Sales` or `Sundry Debtors` in the same way. A company's own voucher types and ledger groups are data, not code branches.

## Multiple cost centres and money correctness

An invoice with three cost centres must not cause its outstanding amount to be counted three times. Monetary cost-centre reporting requires either a verified outstanding allocation amount per centre or a verified allocation percentage. If the source only tells us which cost centres are tagged but cannot allocate the remaining receivable, the engine groups the amount under `Multiple cost centres - allocation unknown` rather than inventing a split.

A cost-centre allocation on the original invoice may not equal the allocation of its remaining balance after receipts, credit notes, and adjustments. Prefer allocation data reconciled to the current outstanding balance.

## Architecture

Tally extraction → canonical bill-level facts + cost-centre bridge → deterministic calculations → structured insight JSON → LLM owner-facing explanation.

The LLM should explain calculated results, not calculate them. Keep company-specific labels and hierarchy paths in the payload so the model can describe them in business language without assuming standard names.

## Important data requirement

The current uploaded Excel workbook has customer, invoice number/date, age, invoice value and outstanding amount. It does not contain voucher type, ledger group, cost centre, due date, or receipt allocations. Those dimensions must come from the Tally connector / normalized database before these breakdowns will be populated. Missing dimensions should remain `Not specified`, not be guessed.

## Run a quick smoke test

```python
from datetime import date
from receivables_engine import (
    ReceivableBill, FinanceContext, CostCentreAllocation,
    analyze_all_dimensions,
)

bills = [
    ReceivableBill(
        company_id="C1", customer_id="D1", customer_name="Customer A",
        bill_no="INV-1", bill_date=date(2026, 1, 1), due_date=None,
        original_amount=1000, pending_amount=600,
        voucher_type_name="Sales Machines",
        customer_ledger_group_name="Machine Group",
        customer_ledger_group_path="Sundry Debtors > Machine Group",
        cost_centres=[
            CostCentreAllocation(cost_centre_name="Project North", allocation_pct=60),
            CostCentreAllocation(cost_centre_name="Project South", allocation_pct=40),
        ],
    )
]
ctx = FinanceContext(as_of_date=date(2026, 10, 27))
print(analyze_all_dimensions(bills, ctx))
```

The percentages in this example are illustrative. In production, only use allocation percentages when they are verified and applicable to the current pending amount.


## v0.3 update: product dimensions

The receivables engine supports invoice-level breakdowns by stock item group and
stock item category, when available. Individual stock-item analysis is excluded
from the standard engine because invoice outstanding cannot reliably be assigned
to product lines without payment allocation evidence. If an invoice has multiple
distinct groups/categories, classify it as `MULTIPLE` unless a documented company
rule determines a single invoice-level class. Missing dimensions are reported as
`Not specified`; unavailable dimensions can be omitted by the calling layer.
