# PersonalFinanceTrackingSystem
PersonalFinanceTrackingSystem - submit your statement, we will tell you how you have been doing!

## Overview
A local-first personal finance tracking system. You drop monthly statements (bank, credit card, brokerage, mortgage) as PDFs into an inbox, and the system parses, normalizes, categorizes, computes metrics (monthly/YTD spending and income, assets/net worth, mortgages), and generates a monthly report as HTML and/or PDF. All data is stored in a local SQLite database; no cloud services are required.

## High-Level Architecture
- **User Interfaces**
  - **CLI**: primary interface for importing statements, running categorization, computing metrics, and generating reports.
  - **Optional Local Web UI**: read-only dashboards and manual overrides (can be added later).
- **Ingestion Subsystem**
  - **Statements Inbox**: a local folder where you drop PDFs. A watcher or on-demand import picks them up.
  - **Parser Plugins**: provider-specific PDF extractors using text extraction and optional OCR; output normalized records.
  - **Deduplication**: hashes statement files and content to avoid re-importing the same period.
- **Normalization & Enrichment**
  - **Transaction Normalizer**: converts raw statement-specific rows into a unified transaction schema.
  - **Rules-based Categorizer**: applies ordered rules (regex/substring/merchant map) with manual override capability.
  - **Transfer/Reconciliation Engine**: detects inter-account transfers to avoid double-counting income/expense.
- **Computation Engine**
  - **Balances & YTD**: aggregates monthly and YTD income/expense by category.
  - **Assets & Net Worth**: computes asset/liability snapshots; supports holdings and mortgage balances.
  - **Mortgage Amortization**: splits payments into principal/interest/escrow; derives outstanding balance.
- **Storage**
  - **SQLite** on local disk; one file DB. ACID, simple backups, easy to query.
- **Reporting**
  - **HTML Reports** (templated): interactive tables/plots.
  - **PDF Reports**: rendered from HTML (wkhtmltopdf/WeasyPrint) for archival.

## Local-Only Assumptions
- Runs entirely on your machine; uses only local files and a SQLite DB.
- Optional price data for assets can be imported via CSV (local) or fetched on-demand (can be disabled for full offline).
- Backups are simple file copies of the SQLite DB and `data/` directory.

## Data Model (SQLite)
Key tables (simplified). Indices are essential for date/account lookups and rule application.

```sql
-- Accounts and statements
CREATE TABLE accounts (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL,             -- 'bank','credit_card','brokerage','mortgage','cash','other'
  name TEXT NOT NULL,
  institution TEXT,
  currency TEXT NOT NULL DEFAULT 'USD',
  opened_at DATE,
  closed_at DATE
);
CREATE INDEX idx_accounts_type ON accounts(type);

CREATE TABLE statements (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  source_file_path TEXT NOT NULL,
  source_file_hash TEXT NOT NULL, -- file content hash
  imported_at DATETIME NOT NULL,
  status TEXT NOT NULL DEFAULT 'imported', -- 'imported','parsed','failed'
  UNIQUE (account_id, period_start, period_end)
);
CREATE INDEX idx_statements_account_period ON statements(account_id, period_start, period_end);
CREATE INDEX idx_statements_hash ON statements(source_file_hash);

-- Normalized transactions
CREATE TABLE transactions (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  statement_id INTEGER REFERENCES statements(id),
  posted_at DATE NOT NULL,
  description TEXT NOT NULL,
  merchant TEXT,
  amount NUMERIC NOT NULL,        -- signed; expenses negative, income positive
  currency TEXT NOT NULL DEFAULT 'USD',
  category_id INTEGER,            -- nullable; assigned by rules or manual
  is_transfer INTEGER NOT NULL DEFAULT 0, -- 0/1
  is_income INTEGER NOT NULL DEFAULT 0,
  is_expense INTEGER NOT NULL DEFAULT 0,
  hash TEXT NOT NULL,             -- content hash for dedup
  raw_json TEXT                   -- parser original fields for audit
);
CREATE INDEX idx_tx_account_date ON transactions(account_id, posted_at);
CREATE INDEX idx_tx_category_date ON transactions(category_id, posted_at);
CREATE UNIQUE INDEX idx_tx_hash ON transactions(hash);

-- Split transactions (budgeting/categories)
CREATE TABLE transaction_splits (
  id INTEGER PRIMARY KEY,
  transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  amount NUMERIC NOT NULL,        -- signed; must sum to transaction amount
  category_id INTEGER NOT NULL
);
CREATE INDEX idx_tx_splits_tx ON transaction_splits(transaction_id);

-- Categories and rules
CREATE TABLE categories (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id INTEGER,              -- support hierarchy (e.g., 'Food' > 'Dining Out')
  type TEXT NOT NULL              -- 'expense','income','transfer','asset','liability'
);
CREATE UNIQUE INDEX idx_categories_name ON categories(name);

CREATE TABLE category_rules (
  id INTEGER PRIMARY KEY,
  priority INTEGER NOT NULL,      -- lower number = higher priority
  pattern_type TEXT NOT NULL,     -- 'regex','substring','exact','merchant'
  pattern TEXT NOT NULL,
  category_id INTEGER NOT NULL REFERENCES categories(id),
  account_type_filter TEXT,       -- optional, limit to certain account types
  assign_income INTEGER,          -- 0/1 overrides
  assign_expense INTEGER          -- 0/1 overrides
);
CREATE INDEX idx_rules_priority ON category_rules(priority);

-- Holdings and prices (assets)
CREATE TABLE holdings (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  symbol TEXT NOT NULL,           -- ticker or asset symbol
  quantity NUMERIC NOT NULL,
  as_of DATE NOT NULL
);
CREATE INDEX idx_holdings_account_date ON holdings(account_id, as_of);
CREATE INDEX idx_holdings_symbol_date ON holdings(symbol, as_of);

CREATE TABLE prices (
  id INTEGER PRIMARY KEY,
  symbol TEXT NOT NULL,
  as_of DATE NOT NULL,
  price NUMERIC NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  source TEXT                     -- 'manual','csv','yf','alpaca', etc.
);
CREATE UNIQUE INDEX idx_prices_symbol_date ON prices(symbol, as_of);

-- Mortgages (liabilities)
CREATE TABLE mortgages (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id), -- links to the mortgage account
  lender TEXT,
  property_name TEXT,
  principal NUMERIC NOT NULL,
  rate_annual NUMERIC NOT NULL,   -- e.g., 0.0525 for 5.25%
  term_months INTEGER NOT NULL,
  start_date DATE NOT NULL,
  payment_day INTEGER             -- e.g., 1..31
);

CREATE TABLE mortgage_payments (
  id INTEGER PRIMARY KEY,
  mortgage_id INTEGER NOT NULL REFERENCES mortgages(id),
  due_date DATE NOT NULL,
  paid_date DATE,
  amount NUMERIC NOT NULL,
  principal_paid NUMERIC NOT NULL,
  interest_paid NUMERIC NOT NULL,
  escrow_paid NUMERIC DEFAULT 0,
  extra_principal NUMERIC DEFAULT 0
);
CREATE INDEX idx_mortgage_payments_mortgage_date ON mortgage_payments(mortgage_id, due_date);

-- Snapshots and reports
CREATE TABLE net_worth_snapshots (
  id INTEGER PRIMARY KEY,
  as_of DATE NOT NULL,
  assets_total NUMERIC NOT NULL,
  liabilities_total NUMERIC NOT NULL,
  net_worth NUMERIC NOT NULL
);
CREATE UNIQUE INDEX idx_snapshots_date ON net_worth_snapshots(as_of);

CREATE TABLE reports (
  id INTEGER PRIMARY KEY,
  month TEXT NOT NULL,            -- 'YYYY-MM'
  generated_at DATETIME NOT NULL,
  format TEXT NOT NULL,           -- 'html','pdf'
  file_path TEXT NOT NULL,
  checksum TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_reports_month_format ON reports(month, format);
```

## Ingestion & Parsing
- **Inbox/Input Folder**: configurable via `pf import --input <folder>` (defaults to `/data/inbox/`). Processed files are moved to `/data/archive/YYYY/MM/`.
- **Statement Classification (auto)**: a lightweight, local classifier inspects the first pages' text and table headers to identify statement type and provider (e.g., credit card vs bank vs brokerage vs mortgage) and routes to the correct parser. It relies on issuer keywords, field cues (e.g., "Payment Due Date", "Minimum Payment" → credit card; "Escrow", "Principal", "Interest" → mortgage; "Dividends", "Positions" → brokerage), and layout signatures. No LLM is required; deterministic rules achieve high accuracy. Ambiguous/low-confidence files are moved to `/data/unclassified/` for manual review or an override flag.
- **Parser Plugins**:
  - Each provider (e.g., Chase, AmEx, Fidelity, Wells Fargo, mortgage lender) has a small adapter.
  - Use PDF text extraction (e.g., pdfplumber) and optional OCR (e.g., Tesseract) for scanned PDFs.
  - Adapters emit a normalized structure (transactions, holdings, mortgage payment rows) and attach `raw_json` for audit.
- **Deduplication**: content-hash files; refuse duplicates for same account+period; allow override flag to re-import.
- **Error Handling**: failures logged in DB with status `failed` and message in a local log.
-. **Dynamic Account Provisioning**: on first sighting of a new institution + account number (e.g., "Account ending in 1234"), an `accounts` row is created automatically with inferred `type` and `institution`. Future statements reconcile to the same account by key features (institution + masked account, address blocks, or account alias mapping).

### Normalization
- Normalize amounts (income positive, expenses negative).
- Standardize dates/timezones to local.
- Map brokerage positions to `holdings`; map mortgage statements to `mortgage_payments`.

### Categorization
- Automatic during import; no separate step required.
- Ordered rules applied by priority until first match.
- Rule types: regex on description, substring, exact merchant, account-type scoped.
- Low-confidence or no-match transactions are assigned to `Uncategorized` and highlighted in reports for later review.
- Manual overrides: optional local UI or editing rules in config to set `category_id`; the system can persist new rules from overrides.

### Transfer Detection
- Heuristics: opposite-signed amounts between two accounts within N days and similar magnitude; mark both as `is_transfer=1` and exclude from income/expense.
- Special cases: credit card payments from checking; brokerage cash transfers; internal account moves.

## Computations
- **Monthly & YTD Spending by Category**: sum `transactions` grouped by `category_id`, month boundaries; include splits.
- **Monthly & YTD Earnings**: sum income categories; include dividends/interest from brokerage.
- **Assets & Net Worth**: aggregate latest `holdings` × `prices` per symbol + bank balances; liabilities from `mortgages` outstanding principal; snapshot monthly.
- **Outstanding Mortgage**: compute remaining principal from schedule or from cumulative `mortgage_payments`; reconcile with lender balance when available.

## Reporting
- **HTML**: templated summaries with charts and drilldowns:
  - Monthly spend by category + YTD
  - Monthly earnings + YTD
  - Asset breakdown and net worth trend
  - Mortgage status and amortization
- **PDF**: render the same HTML to PDF for archival.
- **Output Folder**: configurable via `pf report --output <folder>` (defaults to `/data/reports/`). Files are written to `<output>/YYYY/MM/report.html` and `<output>/YYYY/MM/report.pdf`.

## Configuration
YAML/JSON config at `/config/config.yml`:
- **accounts**: define each account with type, display name, currency.
- **parsers**: provider signatures and parser plugins; OCR on/off; page ranges if needed; optional hard overrides (force a specific parser for certain files or folders).
- **categorization**: default categories, seed rules, transfer window settings.
- **reporting**: theme, currency display, output paths.
- **paths**: defaults for `input_dir` and `output_dir` used when CLI args are omitted.
- **classification**: keyword lists, header patterns, confidence thresholds, tie-breakers, and policy for ambiguous files (`move_to_unclassified` or `prompt_once`).
- **privacy**: redaction options for reports (mask account numbers, merchants).

## Directory Layout
Defaults if CLI args are not provided.
```
/data/
  inbox/           # drop PDFs here
  unclassified/    # PDFs requiring manual resolution (low-confidence classification)
  archive/YYYY/MM/ # processed originals
  reports/YYYY/MM/ # generated reports
  db/finance.sqlite
/config/
  config.yml
/parsers/          # provider parser plugins
/templates/        # report templates (HTML/CSS)
```

## CLI Surface (local)
```bash
pf init                          # create folders, DB, seed categories/rules
pf import --input /path/to/pdfs  # parse & import all PDFs in folder
pf compute --month 2025-10       # compute aggregates and snapshots
pf report --month 2025-10 --output /path/to/reports --pdf  # generate report; HTML by default, PDF optional
pf open --month 2025-10          # open latest report in browser
pf backup --out backup/          # copy db and data directories
```

Notes: Categorization is automatic on import. `--input` sets the source folder for statements; `--output` sets the destination folder for reports. If omitted, defaults from `/config/config.yml` are used.

## Privacy & Security (Local)
- All processing and storage happen locally.
- SHA-256 hashes for files and transaction content aid deduplication and integrity.
- Optionally encrypt the SQLite DB with a passphrase (via SQLite extension) and store the key locally.

## Extensibility
- **Parsers**: simple interface per provider; add a new adapter with a few lines of glue code.
- **Categories/Rules**: evolve over time; rules export/import via YAML.
- **Reports**: add sections by extending templates and SQL views.

## Implementation Notes (suggested stack)
- **Language**: Python or Node.js—both work well with SQLite and PDF parsing; Python has excellent PDF/OCR libraries.
- **PDF**: `pdfplumber`/`pdfminer.six`; OCR with `tesseract` only when needed.
- **DB Layer**: SQLAlchemy/SQLite (Python) or better-sqlite3/knex (Node).
- **Reporting**: Jinja2 templates + WeasyPrint/wkhtmltopdf for PDF.
- Everything offline by default; optional price CSV import for assets.
 - **Classification**: implement as deterministic rules with provider keyword dictionaries and header/layout patterns. An LLM is not required. If desired, a small local model can assist only for low-confidence cases while keeping all data local.

## Next Steps
1) Initialize repo folders and SQLite schema.
2) Implement 2–3 parser plugins for your most-used institutions.
3) Seed categories and a handful of categorization rules.
4) Generate your first monthly report; iterate on rules and templates.
