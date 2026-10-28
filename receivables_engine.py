
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from typing import Optional, List, Dict, Any
from collections import defaultdict
import math


# ============================================================
# TalkingTotals Receivables Intelligence Engine v0.1
#
# Design principle:
#   Tally facts -> deterministic analysis -> financial impact
#   -> structured insight -> LLM beautification
#
# The LLM must NOT calculate financial impact.
# ============================================================


@dataclass
class CostCentreAllocation:
    """One cost-centre allocation for an invoice/receivable.

    allocated_pending_amount is the outstanding portion assigned to this
    cost centre, if the source can establish it. allocation_pct is optional.
    Never assume the full invoice amount belongs to every cost centre.
    """
    cost_centre_id: Optional[str] = None
    cost_centre_name: str = "Unallocated / Not specified"
    cost_centre_path: Optional[str] = None
    allocated_pending_amount: Optional[float] = None
    allocation_pct: Optional[float] = None


@dataclass
class ReceivableBill:
    company_id: str
    customer_id: str
    customer_name: str
    bill_no: str
    bill_date: date
    due_date: Optional[date]
    original_amount: float
    pending_amount: float

    # Company-defined Tally dimensions. Keep raw labels and hierarchy paths;
    # do not assume every company uses standard Tally names.
    voucher_type_id: Optional[str] = None
    voucher_type_name: Optional[str] = None
    customer_ledger_group_id: Optional[str] = None
    customer_ledger_group_name: Optional[str] = None
    customer_ledger_group_path: Optional[str] = None

    # Optional invoice-level product classifications. We intentionally do not
    # break receivables down to individual stock items because outstanding
    # balances cannot be reliably attributed to invoice lines without payment
    # allocation data. If an invoice spans multiple groups/categories, use
    # the literal classification "MULTIPLE" rather than duplicating its balance.
    stock_item_group_id: Optional[str] = None
    stock_item_group_name: Optional[str] = None
    stock_item_group_path: Optional[str] = None
    stock_item_category_id: Optional[str] = None
    stock_item_category_name: Optional[str] = None
    stock_item_category_path: Optional[str] = None

    # Optional additional dimensions
    area: Optional[str] = None
    salesperson: Optional[str] = None
    customer_group: Optional[str] = None
    cost_centres: Optional[List[CostCentreAllocation]] = None

    # Useful for richer analysis
    last_receipt_date: Optional[date] = None
    last_receipt_amount: Optional[float] = None


@dataclass
class FinanceContext:
    as_of_date: date

    # Funding information. Populate from Tally/bank configuration
    # or user/company settings where available.
    od_outstanding: float = 0.0
    od_interest_rate: Optional[float] = None  # annual %, e.g. 12.0

    cc_outstanding: float = 0.0
    cc_interest_rate: Optional[float] = None

    other_short_term_borrowing: float = 0.0
    other_short_term_rate: Optional[float] = None

    effective_tax_rate: Optional[float] = None  # %, optional

    # Conservative assumption for collections.
    # This is NOT an accounting fact. It is a scenario parameter.
    collection_recovery_rate: float = 0.50

    # Maximum amount of receivables assumed to be capable of reducing
    # short-term borrowing. Usually the current OD/CC utilization.
    max_debt_reduction_from_collection: Optional[float] = None


@dataclass
class BucketResult:
    bucket: str
    amount: float
    bill_count: int
    customer_count: int
    percentage_of_total: float


@dataclass
class CustomerResult:
    customer_id: str
    customer_name: str
    outstanding: float
    overdue: float
    very_old: float
    bill_count: int
    oldest_days: int
    last_receipt_date: Optional[str]
    no_receipt_days: Optional[int]


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def safe_pct(numerator: float, denominator: float) -> float:
    return (numerator / denominator * 100.0) if denominator else 0.0


def age_days(bill: ReceivableBill, as_of: date) -> int:
    """
    Preferred business ageing:
      due-date ageing if due_date exists.
    Otherwise bill-date ageing.

    This matches Tally's documented distinction between ageing by
    due date and ageing by bill date.
    """
    basis = bill.due_date or bill.bill_date
    return max(0, (as_of - basis).days)


def age_bucket(days: int) -> str:
    if days <= 30:
        return "0-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    if days <= 180:
        return "91-180"
    if days <= 365:
        return "181-365"
    return ">365"


def normalize_bills(rows: List[Dict[str, Any]]) -> List[ReceivableBill]:
    """
    Convert SQL rows/dicts into the engine's canonical fact model.

    Required SQL columns:
      company_id, customer_id, customer_name, bill_no,
      bill_date, due_date, original_amount, pending_amount

    Optional:
      voucher_type_id, voucher_type_name,
      customer_ledger_group_id, customer_ledger_group_name,
      customer_ledger_group_path, stock_item_group_id/name/path,
      stock_item_category_id/name/path, cost_centres (optional list),
      area, salesperson, customer_group,
      last_receipt_date, last_receipt_amount
    """
    result = []

    for r in rows:
        def d(v):
            if v is None or v == "":
                return None
            if isinstance(v, date):
                return v
            return date.fromisoformat(str(v)[:10])

        result.append(
            ReceivableBill(
                company_id=str(r["company_id"]),
                customer_id=str(r["customer_id"]),
                customer_name=str(r["customer_name"]),
                bill_no=str(r["bill_no"]),
                bill_date=d(r["bill_date"]),
                due_date=d(r.get("due_date")),
                original_amount=float(r["original_amount"] or 0),
                pending_amount=float(r["pending_amount"] or 0),
                voucher_type_id=(str(r["voucher_type_id"]) if r.get("voucher_type_id") is not None else None),
                voucher_type_name=r.get("voucher_type_name") or r.get("voucher_type"),
                customer_ledger_group_id=(str(r["customer_ledger_group_id"]) if r.get("customer_ledger_group_id") is not None else None),
                customer_ledger_group_name=(r.get("customer_ledger_group_name") or r.get("ledger_group_name") or r.get("customer_group")),
                customer_ledger_group_path=(r.get("customer_ledger_group_path") or r.get("ledger_group_path")),
                stock_item_group_id=(str(r["stock_item_group_id"]) if r.get("stock_item_group_id") is not None else None),
                stock_item_group_name=r.get("stock_item_group_name") or "Not specified",
                stock_item_group_path=r.get("stock_item_group_path") or r.get("stock_item_group_name"),
                stock_item_category_id=(str(r["stock_item_category_id"]) if r.get("stock_item_category_id") is not None else None),
                stock_item_category_name=r.get("stock_item_category_name") or "Not specified",
                stock_item_category_path=r.get("stock_item_category_path") or r.get("stock_item_category_name"),
                area=r.get("area"),
                salesperson=r.get("salesperson"),
                customer_group=r.get("customer_group"),
                cost_centres=[
                    cc if isinstance(cc, CostCentreAllocation) else CostCentreAllocation(
                        cost_centre_id=str(cc["cost_centre_id"]) if cc.get("cost_centre_id") is not None else None,
                        cost_centre_name=cc.get("cost_centre_name") or cc.get("name") or "Unallocated / Not specified",
                        cost_centre_path=cc.get("cost_centre_path") or cc.get("path"),
                        allocated_pending_amount=float(cc.get("allocated_pending_amount", cc.get("allocated_amount"))) if cc.get("allocated_pending_amount", cc.get("allocated_amount")) is not None else None,
                        allocation_pct=float(cc["allocation_pct"]) if cc.get("allocation_pct") is not None else None,
                    ) for cc in (r.get("cost_centres") or [])
                ],
                last_receipt_date=d(r.get("last_receipt_date")),
                last_receipt_amount=(
                    float(r["last_receipt_amount"])
                    if r.get("last_receipt_amount") is not None else None
                ),
            )
        )

    return result


# ------------------------------------------------------------
# Core receivables analysis
# ------------------------------------------------------------

def analyze_ageing(
    bills: List[ReceivableBill],
    ctx: FinanceContext,
) -> Dict[str, Any]:

    total = sum(max(0.0, b.pending_amount) for b in bills)

    buckets = {
        "0-30": [],
        "31-60": [],
        "61-90": [],
        "91-180": [],
        "181-365": [],
        ">365": [],
    }

    for b in bills:
        if b.pending_amount <= 0:
            continue
        buckets[age_bucket(age_days(b, ctx.as_of_date))].append(b)

    bucket_results = []

    for name, items in buckets.items():
        amount = sum(b.pending_amount for b in items)
        bucket_results.append(
            BucketResult(
                bucket=name,
                amount=amount,
                bill_count=len(items),
                customer_count=len({b.customer_id for b in items}),
                percentage_of_total=safe_pct(amount, total),
            )
        )

    overdue = sum(
        b.pending_amount
        for b in bills
        if age_days(b, ctx.as_of_date) > 0
    )

    old_180 = sum(
        b.pending_amount
        for b in bills
        if age_days(b, ctx.as_of_date) > 180
    )

    old_365 = sum(
        b.pending_amount
        for b in bills
        if age_days(b, ctx.as_of_date) > 365
    )

    return {
        "total_receivables": total,
        "overdue_receivables": overdue,
        "overdue_percentage": safe_pct(overdue, total),
        "over_180": old_180,
        "over_180_percentage": safe_pct(old_180, total),
        "over_365": old_365,
        "over_365_percentage": safe_pct(old_365, total),
        "buckets": [asdict(x) for x in bucket_results],
    }


def analyze_customers(
    bills: List[ReceivableBill],
    ctx: FinanceContext,
) -> List[CustomerResult]:

    grouped = defaultdict(list)

    for b in bills:
        if b.pending_amount > 0:
            grouped[b.customer_id].append(b)

    output = []

    for customer_id, items in grouped.items():
        outstanding = sum(b.pending_amount for b in items)

        overdue = sum(
            b.pending_amount
            for b in items
            if age_days(b, ctx.as_of_date) > 0
        )

        very_old = sum(
            b.pending_amount
            for b in items
            if age_days(b, ctx.as_of_date) > 180
        )

        oldest = max(age_days(b, ctx.as_of_date) for b in items)

        receipt_dates = [
            b.last_receipt_date for b in items
            if b.last_receipt_date is not None
        ]

        last_receipt = max(receipt_dates) if receipt_dates else None

        no_receipt_days = (
            (ctx.as_of_date - last_receipt).days
            if last_receipt else None
        )

        output.append(
            CustomerResult(
                customer_id=customer_id,
                customer_name=items[0].customer_name,
                outstanding=outstanding,
                overdue=overdue,
                very_old=very_old,
                bill_count=len(items),
                oldest_days=oldest,
                last_receipt_date=(
                    last_receipt.isoformat() if last_receipt else None
                ),
                no_receipt_days=no_receipt_days,
            )
        )

    return sorted(
        output,
        key=lambda x: x.outstanding,
        reverse=True,
    )


# ------------------------------------------------------------
# Company-defined dimension analysis
# ------------------------------------------------------------


def _dimension_value(bill: ReceivableBill, dimension: str) -> str:
    """Resolve a supported invoice-level dimension to a stable display key."""
    mapping = {
        "voucher_type": bill.voucher_type_name or "Not specified",
        "customer_ledger_group": bill.customer_ledger_group_name or "Not specified",
        "customer_ledger_group_path": bill.customer_ledger_group_path or bill.customer_ledger_group_name or "Not specified",
        "stock_item_group": bill.stock_item_group_path or bill.stock_item_group_name or "Not specified",
        "stock_item_category": bill.stock_item_category_path or bill.stock_item_category_name or "Not specified",
        "customer": bill.customer_name or "Not specified",
        "area": bill.area or "Not specified",
        "salesperson": bill.salesperson or "Not specified",
    }
    if dimension not in mapping:
        raise ValueError(f"Unsupported invoice-level dimension: {dimension}")
    return str(mapping[dimension])


def analyze_dimension(
    bills: List[ReceivableBill],
    ctx: FinanceContext,
    dimension: str,
) -> List[Dict[str, Any]]:
    """Aggregate outstanding by a company-defined dimension.

    For cost centres, use explicit allocation amounts or allocation percentages
    if available. If an invoice has multiple cost centres but no allocation
    weights/amounts, report it under 'Multiple cost centres - allocation unknown'
    instead of duplicating the full invoice into every cost centre.
    """
    totals = defaultdict(lambda: {"outstanding": 0.0, "over_90": 0.0,
                                 "over_180": 0.0, "bill_keys": set(),
                                 "customers": set(), "allocation_complete": True})

    for b in bills:
        amount = max(0.0, b.pending_amount)
        if amount <= 0:
            continue
        days = age_days(b, ctx.as_of_date)
        bill_key = (b.company_id, b.customer_id, b.bill_no)

        if dimension == "cost_centre":
            ccs = b.cost_centres or []
            if not ccs:
                allocations = [("Not specified", amount, True)]
            elif len(ccs) == 1 and ccs[0].allocated_pending_amount is None and ccs[0].allocation_pct is None:
                allocations = [(ccs[0].cost_centre_path or ccs[0].cost_centre_name, amount, True)]
            else:
                explicit = [cc.allocated_pending_amount for cc in ccs]
                pcts = [cc.allocation_pct for cc in ccs]
                if all(v is not None for v in explicit):
                    allocations = [(cc.cost_centre_path or cc.cost_centre_name, max(0.0, float(cc.allocated_pending_amount)), True) for cc in ccs]
                elif all(v is not None for v in pcts) and sum(float(v) for v in pcts) > 0:
                    allocations = [(cc.cost_centre_path or cc.cost_centre_name, amount * float(cc.allocation_pct) / 100.0, True) for cc in ccs]
                elif len(ccs) == 1:
                    allocations = [(ccs[0].cost_centre_path or ccs[0].cost_centre_name, amount, True)]
                else:
                    allocations = [("Multiple cost centres - allocation unknown", amount, False)]
        else:
            allocations = [(_dimension_value(b, dimension), amount, True)]

        for key, allocated, allocation_known in allocations:
            row = totals[key]
            row["outstanding"] += allocated
            if days > 90:
                row["over_90"] += allocated
            if days > 180:
                row["over_180"] += allocated
            row["bill_keys"].add(bill_key)
            row["customers"].add(b.customer_id)
            row["allocation_complete"] = row["allocation_complete"] and allocation_known

    result = []
    for name, row in totals.items():
        result.append({
            "dimension": dimension,
            "value": name,
            "outstanding": round(row["outstanding"], 2),
            "over_90": round(row["over_90"], 2),
            "over_180": round(row["over_180"], 2),
            "bill_count": len(row["bill_keys"]),
            "customer_count": len(row["customers"]),
            "allocation_known": row["allocation_complete"],
        })
    return sorted(result, key=lambda x: x["outstanding"], reverse=True)


def analyze_all_dimensions(bills: List[ReceivableBill], ctx: FinanceContext) -> Dict[str, Any]:
    """Produce only useful breakdowns; optional dimensions are omitted if absent."""
    dimensions = {
        "by_voucher_type": analyze_dimension(bills, ctx, "voucher_type"),
        "by_customer_ledger_group": analyze_dimension(bills, ctx, "customer_ledger_group"),
        "by_customer_ledger_group_path": analyze_dimension(bills, ctx, "customer_ledger_group_path"),
    }

    # Optional dimensions: include only when at least one bill has a real value.
    # This avoids generating meaningless all-'Not specified' reports.
    if any(b.stock_item_group_name and b.stock_item_group_name != "Not specified" for b in bills):
        dimensions["by_stock_item_group"] = analyze_dimension(bills, ctx, "stock_item_group")
    if any(b.stock_item_category_name and b.stock_item_category_name != "Not specified" for b in bills):
        dimensions["by_stock_item_category"] = analyze_dimension(bills, ctx, "stock_item_category")
    if any(b.cost_centres for b in bills):
        dimensions["by_cost_centre"] = analyze_dimension(bills, ctx, "cost_centre")
    return dimensions


# ------------------------------------------------------------
# Financial impact engine
# ------------------------------------------------------------

def effective_short_term_funding_rate(ctx: FinanceContext) -> Optional[float]:
    """
    Returns a weighted/priority rate for short-term borrowing.

    v0.1 intentionally uses the OD rate first, then CC, then other
    short-term borrowing. Later versions can calculate a true weighted
    marginal funding cost from all borrowings.
    """
    candidates = [
        (ctx.od_outstanding, ctx.od_interest_rate),
        (ctx.cc_outstanding, ctx.cc_interest_rate),
        (ctx.other_short_term_borrowing, ctx.other_short_term_rate),
    ]

    usable = [(amount, rate) for amount, rate in candidates
              if amount > 0 and rate is not None]

    if not usable:
        return None

    total_amount = sum(x[0] for x in usable)
    return sum(amount * rate for amount, rate in usable) / total_amount


def calculate_impact(
    recoverable_receivables: float,
    ctx: FinanceContext,
) -> Dict[str, Any]:

    recovery = max(0.0, recoverable_receivables)

    # Only the portion actually capable of reducing borrowing creates
    # an immediate interest-saving scenario.
    debt_capacity = ctx.max_debt_reduction_from_collection

    if debt_capacity is None:
        debt_capacity = (
            ctx.od_outstanding
            + ctx.cc_outstanding
            + ctx.other_short_term_borrowing
        )

    debt_reduction = min(recovery, max(0.0, debt_capacity))

    rate = effective_short_term_funding_rate(ctx)

    annual_interest_saving = (
        debt_reduction * rate / 100.0
        if rate is not None else None
    )

    monthly_interest_saving = (
        annual_interest_saving / 12.0
        if annual_interest_saving is not None else None
    )

    post_tax_profit_impact = None

    if annual_interest_saving is not None and ctx.effective_tax_rate is not None:
        post_tax_profit_impact = (
            annual_interest_saving
            * (1.0 - ctx.effective_tax_rate / 100.0)
        )

    return {
        "cash_released": recovery,
        "potential_debt_reduction": debt_reduction,
        "funding_rate_used": rate,
        "annual_interest_saving": annual_interest_saving,
        "monthly_interest_saving": monthly_interest_saving,
        "pre_tax_profit_impact": annual_interest_saving,
        "post_tax_profit_impact": post_tax_profit_impact,
        "assumptions": {
            "recovery_rate": ctx.collection_recovery_rate,
            "debt_reduction_capped_at_short_term_borrowing": True,
        },
    }


# ------------------------------------------------------------
# Insight generation
# ------------------------------------------------------------

def build_receivable_insights(
    bills: List[ReceivableBill],
    ctx: FinanceContext,
) -> Dict[str, Any]:

    ageing = analyze_ageing(bills, ctx)
    customers = analyze_customers(bills, ctx)

    insights = []

    total = ageing["total_receivables"]

    # ---- Insight 1: old receivables ----

    if ageing["over_180"] > 0:
        recovery_opportunity = (
            ageing["over_180"] * ctx.collection_recovery_rate
        )

        impact = calculate_impact(recovery_opportunity, ctx)

        insights.append({
            "insight_id": "AR.AGE.180",
            "category": "AGEING",
            "priority": "HIGH" if ageing["over_180_percentage"] >= 20 else "MEDIUM",

            "facts": {
                "receivables_total": total,
                "receivables_over_180": ageing["over_180"],
                "percentage_over_180": ageing["over_180_percentage"],
            },

            "what_happened": (
                f"{ageing['over_180_percentage']:.1f}% of total receivables "
                f"({ageing['over_180']:,.2f}) are more than 180 days old."
            ),

            "why_it_matters": [
                "Cash belonging to the business is tied up in old customer dues.",
                "Older receivables generally require more active collection effort.",
                "If the company is using short-term borrowing, delayed collections can increase financing requirements.",
            ],

            "recommended_actions": [
                "Prioritise customers contributing the largest >180-day balances.",
                "Review the oldest bills individually.",
                "Obtain a specific payment commitment rather than an open-ended promise.",
                "Consider temporarily tightening further credit where commercially appropriate.",
            ],

            "financial_impact": impact,

            "impact_explanation": (
                "This is a scenario, not a guaranteed saving. "
                "The engine assumes a portion of old receivables can be recovered "
                "and that recovered cash is used to reduce short-term borrowing."
            ),
        })

    # ---- Insight 2: >365 days ----

    if ageing["over_365"] > 0:
        recovery_opportunity = (
            ageing["over_365"] * ctx.collection_recovery_rate
        )

        impact = calculate_impact(recovery_opportunity, ctx)

        insights.append({
            "insight_id": "AR.AGE.365",
            "category": "AGEING",
            "priority": "CRITICAL" if ageing["over_365_percentage"] >= 10 else "HIGH",

            "facts": {
                "receivables_over_365": ageing["over_365"],
                "percentage_over_365": ageing["over_365_percentage"],
            },

            "what_happened": (
                f"{ageing['over_365_percentage']:.1f}% of receivables "
                f"({ageing['over_365']:,.2f}) are more than one year old."
            ),

            "why_it_matters": [
                "These balances have remained unpaid for an extended period.",
                "The probability of collection should be assessed customer by customer.",
                "The balance may require management review for dispute, recoverability, or possible bad-debt treatment.",
            ],

            "recommended_actions": [
                "Create a customer-by-customer recovery list.",
                "Separate genuine disputes from simple payment delays.",
                "Escalate high-value accounts to senior management.",
                "Review whether continued credit is commercially justified.",
            ],

            "financial_impact": impact,

            "impact_explanation": (
                "Potential recovery is calculated as a scenario using the configured "
                "recovery rate; it is not treated as guaranteed cash."
            ),
        })

    # ---- Insight 3: concentration ----

    if customers:
        top_10 = customers[:10]
        top_10_amount = sum(x.outstanding for x in top_10)
        concentration = safe_pct(top_10_amount, total)

        insights.append({
            "insight_id": "AR.CONCENTRATION.TOP10",
            "category": "CUSTOMER_CONCENTRATION",
            "priority": "HIGH" if concentration >= 50 else "MEDIUM",

            "facts": {
                "top_10_outstanding": top_10_amount,
                "top_10_percentage": concentration,
                "customer_count": len(customers),
            },

            "what_happened": (
                f"The 10 largest customers account for "
                f"{concentration:.1f}% of total receivables."
            ),

            "why_it_matters": [
                "Collection performance depends materially on a relatively small number of customers.",
                "A delay from one large customer can have a disproportionate cash-flow effect.",
            ],

            "recommended_actions": [
                "Review the largest overdue balances individually.",
                "Track promised payment dates for major customers.",
                "Monitor exposure limits for customers with persistent overdue balances.",
            ],

            "financial_impact": {
                "cash_released": None,
                "potential_debt_reduction": None,
                "annual_interest_saving": None,
                "note": "Customer concentration itself is not a saving; it identifies where collection attention may have the greatest cash impact."
            },
        })

    return {
        "as_of_date": ctx.as_of_date.isoformat(),
        "summary": ageing,
        "customers": [asdict(x) for x in customers],
        "dimensions": analyze_all_dimensions(bills, ctx),
        "insights": insights,
    }


# ------------------------------------------------------------
# LLM handoff
# ------------------------------------------------------------

def llm_payload(engine_output: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send THIS structured object to the LLM.

    Do not send raw Tally transactions unless needed for drill-down.
    The LLM's job is to explain the already-calculated facts.
    """
    return {
        "instruction": (
            "Convert the supplied financial insight into simple English for a "
            "business owner. Do not change, recalculate, invent, or rank numbers. "
            "Clearly distinguish actual facts from scenario estimates and assumptions."
        ),
        "data": engine_output,
    }


# ------------------------------------------------------------
# Example
# ------------------------------------------------------------

if __name__ == "__main__":
    bills = [
        ReceivableBill(
            company_id="C1",
            customer_id="CUS001",
            customer_name="ABC Traders",
            bill_no="INV-1001",
            bill_date=date(2025, 1, 10),
            due_date=date(2025, 2, 9),
            original_amount=1000000,
            pending_amount=800000,
            area="Bengaluru",
            last_receipt_date=date(2025, 3, 15),
        ),
        ReceivableBill(
            company_id="C1",
            customer_id="CUS002",
            customer_name="XYZ Industries",
            bill_no="INV-2001",
            bill_date=date(2024, 3, 10),
            due_date=date(2024, 4, 9),
            original_amount=2000000,
            pending_amount=1200000,
            area="Mysuru",
            last_receipt_date=None,
        ),
    ]

    ctx = FinanceContext(
        as_of_date=date(2026, 9, 28),
        od_outstanding=4000000,
        od_interest_rate=12.0,
        effective_tax_rate=25.0,
        collection_recovery_rate=0.50,
    )

    result = build_receivable_insights(bills, ctx)

    import json
    print(json.dumps(result, indent=2, default=str))
