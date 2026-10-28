
-- ============================================================
-- TalkingTotals Receivables extraction - v0.1
--
-- IMPORTANT:
-- This is a CANONICAL target shape, not a claim that the current
-- vc_voucher_entries view already contains every field below.
--
-- For true bill-wise ageing, the source needs:
--   1. receivable bill/reference
--   2. original amount
--   3. receipt allocations against the bill
--   4. due date / credit period
--
-- Tally supports both bill-wise and On Account receivables.
-- Keep On Account balances separate rather than fabricating bills.
-- ============================================================


-- TARGET DATASET:
--
-- receivable_bills
--
-- company_id
-- customer_id
-- customer_name
-- bill_no
-- bill_date
-- due_date
-- original_amount
-- pending_amount
-- area
-- salesperson
-- customer_group
-- last_receipt_date
-- last_receipt_amount


-- ------------------------------------------------------------
-- STEP 1: Identify customer/party ledgers
-- ------------------------------------------------------------

SELECT DISTINCT
    party_ledger_id AS customer_id,
    party_ledger_name AS customer_name,
    system_group,
    ledger_group_path
FROM vc_voucher_entries
WHERE party_ledger_id IS NOT NULL
  AND LOWER(system_group) IN (
      'sundry debtors',
      'accounts receivable'
  );


-- ------------------------------------------------------------
-- STEP 2: Candidate sales/credit-note transactions
--
-- DO NOT use this alone to calculate outstanding bills.
-- It identifies the originating business transactions.
-- ------------------------------------------------------------

SELECT
    company_id,
    party_ledger_id AS customer_id,
    party_ledger_name AS customer_name,
    voucher_number AS bill_no,
    voucher_date AS bill_date,
    reference_number,
    reference_date,
    gross_amount,
    net_amount,
    tax_amount,
    root_voucher_type
FROM vc_voucher_entries
WHERE party_ledger_id = ledger_id
  AND party_ledger_id IS NOT NULL
  AND root_voucher_type IN (
      'Sales',
      'Credit Note',
      'Debit Note'
  );


-- ------------------------------------------------------------
-- STEP 3: Receipts
--
-- This identifies receipt transactions but DOES NOT establish
-- bill allocation unless the underlying Tally bill-reference /
-- allocation data is also imported.
-- ------------------------------------------------------------

SELECT
    company_id,
    party_ledger_id AS customer_id,
    party_ledger_name AS customer_name,
    voucher_number,
    voucher_date,
    reference_number,
    reference_date,
    entry_amount,
    narration,
    root_voucher_type
FROM vc_voucher_entries
WHERE party_ledger_id IS NOT NULL
  AND LOWER(root_voucher_type) = 'receipt';


-- ------------------------------------------------------------
-- STEP 4: Preferred normalized table
--
-- Populate this from the Tally bill-wise/allocation import.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tt_receivable_bills (
    company_id           VARCHAR(100) NOT NULL,
    customer_id          VARCHAR(100) NOT NULL,
    customer_name        VARCHAR(255) NOT NULL,
    bill_no              VARCHAR(255) NOT NULL,
    bill_date            DATE NOT NULL,
    due_date             DATE NULL,
    original_amount      DECIMAL(18,2) NOT NULL,
    pending_amount       DECIMAL(18,2) NOT NULL,

    area                 VARCHAR(255) NULL,
    salesperson          VARCHAR(255) NULL,
    customer_group       VARCHAR(255) NULL,

    last_receipt_date    DATE NULL,
    last_receipt_amount  DECIMAL(18,2) NULL,

    PRIMARY KEY (company_id, customer_id, bill_no)
);


-- ------------------------------------------------------------
-- STEP 5: Optional company finance context
--
-- These values should preferably come from verified company/
-- bank data rather than user assumptions.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tt_finance_context (
    company_id                    VARCHAR(100) PRIMARY KEY,
    od_outstanding                DECIMAL(18,2) NULL,
    od_interest_rate              DECIMAL(8,4) NULL,
    cc_outstanding                DECIMAL(18,2) NULL,
    cc_interest_rate              DECIMAL(8,4) NULL,
    other_short_term_borrowing    DECIMAL(18,2) NULL,
    other_short_term_rate         DECIMAL(8,4) NULL,
    effective_tax_rate            DECIMAL(8,4) NULL,
    updated_at                    DATETIME NOT NULL
);

-- ============================================================
-- v0.2: COMPANY-DEFINED DIMENSIONS
-- ============================================================
-- Do not hardcode business meanings based on voucher type names or
-- customer ledger group names. Preserve the exact Tally names and paths.
-- A customer may have a custom ledger group hierarchy and voucher types.
--
-- Cost centres are a one-to-many relationship. Do NOT put a comma-separated
-- cost-centre list into the bill table, and do NOT join multiple cost-centre
-- rows into invoice totals without allocation logic (that duplicates amounts).

ALTER TABLE tt_receivable_bills
    ADD COLUMN IF NOT EXISTS voucher_type_id VARCHAR(100) NULL,
    ADD COLUMN IF NOT EXISTS voucher_type_name VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS customer_ledger_group_id VARCHAR(100) NULL,
    ADD COLUMN IF NOT EXISTS customer_ledger_group_name VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS customer_ledger_group_path VARCHAR(1000) NULL;

CREATE TABLE IF NOT EXISTS tt_receivable_bill_cost_centres (
    company_id                    VARCHAR(100) NOT NULL,
    customer_id                   VARCHAR(100) NOT NULL,
    bill_no                        VARCHAR(255) NOT NULL,
    cost_centre_id                 VARCHAR(100) NULL,
    cost_centre_name               VARCHAR(255) NOT NULL,
    cost_centre_path               VARCHAR(1000) NULL,
    allocated_pending_amount       DECIMAL(18,2) NULL,
    allocation_pct                 DECIMAL(9,4) NULL,
    allocation_status              VARCHAR(40) NOT NULL DEFAULT 'UNKNOWN',
    source_voucher_number          VARCHAR(255) NULL,
    source_line_id                 VARCHAR(100) NULL,
    PRIMARY KEY (company_id, customer_id, bill_no, cost_centre_name),
    CONSTRAINT fk_tt_ar_cc_bill
      FOREIGN KEY (company_id, customer_id, bill_no)
      REFERENCES tt_receivable_bills (company_id, customer_id, bill_no)
);

-- Suggested meaning of allocation_status:
-- VERIFIED_AMOUNT: allocated_pending_amount is a verified portion of pending AR.
-- VERIFIED_PERCENT: allocation_pct is a verified share; shares should sum to 100.
-- TAG_ONLY: cost centre is known, but no defensible outstanding split exists.
-- UNKNOWN: no cost-centre allocation available.
--
-- Only VERIFIED_AMOUNT / VERIFIED_PERCENT should be used for monetary
-- cost-centre breakdowns. TAG_ONLY must be shown as allocation unknown rather
-- than assigning the entire invoice amount to each cost centre.

-- Suggested extraction shape (adapt source column names to your schema):
--
-- SELECT
--   company_id,
--   party_ledger_id AS customer_id,
--   party_ledger_name AS customer_name,
--   voucher_number AS bill_no,
--   root_voucher_type AS voucher_type_name,
--   ledger_group_name AS customer_ledger_group_name,
--   ledger_group_path AS customer_ledger_group_path,
--   ...
-- FROM vc_voucher_entries
-- WHERE party_ledger_id = ledger_id;
--
-- The current vc_voucher_entries schema may use different field names for
-- voucher type / group / cost-centre path. Map actual source columns explicitly;
-- do not assume the illustrative names above exist.


-- ============================================================
-- v0.3: Invoice-level product classification (NO stock-item analysis)
-- ============================================================
-- Receivables are held at invoice/bill grain. Do not allocate pending AR
-- to individual stock items without defensible payment allocation data.
-- For default owner-facing product breakdowns, retain one effective stock
-- item group and one effective stock item category per invoice.
-- If source invoice lines contain multiple distinct groups/categories and
-- no approved attribution method exists, store 'MULTIPLE' for that dimension.
-- Do not join invoice-level balances directly to invoice-line rows.

ALTER TABLE tt_receivable_bills
    ADD COLUMN IF NOT EXISTS stock_item_group_id VARCHAR(100) NULL,
    ADD COLUMN IF NOT EXISTS stock_item_group_name VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS stock_item_group_path VARCHAR(1000) NULL,
    ADD COLUMN IF NOT EXISTS stock_item_category_id VARCHAR(100) NULL,
    ADD COLUMN IF NOT EXISTS stock_item_category_name VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS stock_item_category_path VARCHAR(1000) NULL;

-- Suggested invoice-classification rules during extraction:
-- 1. Exactly one distinct group/category across invoice lines: use it.
-- 2. More than one distinct value: use 'MULTIPLE' unless a company-approved
--    deterministic classification rule exists.
-- 3. No value or incomplete source linkage: use 'Not specified'.
-- 4. Do not create a stock-item dimension in the standard receivables engine.
-- 5. Reconcile invoice-level balances before and after all dimensional joins.
