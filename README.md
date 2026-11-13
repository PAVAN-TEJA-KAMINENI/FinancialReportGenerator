# Financial Report Generator

A small Flask application for company-scoped, cash-basis double-entry bookkeeping and financial reporting. The Income Statement, Balance Sheet, and direct-method Cash Flow Statement all derive from posted journal entries. Amounts are stored as `Numeric(18, 2)` and handled as `Decimal` in accounting services.

## Technology Stack

- Python 3.11 and Flask
- SQLAlchemy and Flask-Migrate/Alembic
- PostgreSQL for deployment and SQLite for local development
- Flask-Login for authentication and Flask-WTF for CSRF protection
- Bootstrap 5 and Jinja2 templates
- Chart.js for dashboard charts
- ReportLab for PDF exports and openpyxl for Excel exports
- pytest for tests, Gunicorn for the production web server, and psycopg 3 for PostgreSQL connectivity

## Features

- Company registration, login, and authenticated user management with Administrator and Accountant roles
- Company-scoped accounts and financial records
- Chart of Accounts management with account types and cash-flow classifications
- Manual income, expense, asset-purchase, and cash-transfer entry
- CSV transaction import with preview, validation, duplicate detection, and atomic posting
- Balanced opening balances for asset, liability, and equity accounts
- Income Statement, Balance Sheet, and direct-method Cash Flow Statement
- Dashboard with company financial summaries and charts
- PDF and Excel exports, including a combined workbook for all three statements
- Reversal-based corrections for posted journal entries

## Requirements and setup

Use Python 3.11. From the project directory in PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set a random `SECRET_KEY` in `.env` before sharing access. For production, set `FLASK_CONFIG=production`, a secret of at least 32 random characters, `DATABASE_URL`, and `ENABLE_PUBLIC_REGISTRATION=false`; known development/example secrets are rejected and production cookies require HTTPS. Production defaults registration off if the setting is absent. Local development defaults to `instance/financial_reports.db` with public registration enabled. The application never deletes or replaces that database. Database tables are managed through Flask-Migrate; registration does not create or alter the schema.

## Render deployment

The local development setup continues to use the existing SQLite database at `instance/financial_reports.db` when `DATABASE_URL` is unset. Do not point a deployment at the local SQLite file. Render should use a Render PostgreSQL internal connection URL; PostgreSQL URLs are configured to use psycopg 3.

Configure the Render Web Service with:

- **Build command:** `pip install -r requirements.txt`
- **Pre-deploy command:** `flask --app run:app db upgrade`
- **Start command:** `gunicorn --bind 0.0.0.0:$PORT run:app`

Set these Render environment variables:

- `FLASK_CONFIG=production`
- `SECRET_KEY` to a unique random value of at least 32 characters
- `DATABASE_URL` to the Render PostgreSQL internal URL
- `ENABLE_PUBLIC_REGISTRATION=true` to allow public company registration (set to `false` to disable it)
- `DEFAULT_CURRENCY=INR` if INR is the desired default

Render supplies `PORT` to the service. Keep it out of source control along with all secrets. Do not upload sensitive financial data to a public demonstration deployment.

## Database migrations

The repository includes Flask-Migrate/Alembic and an initial schema revision. For a fresh local SQLite database, apply it before starting the server:

```powershell
$env:FLASK_CONFIG = "development"
.venv\Scripts\flask.exe --app run:app db upgrade
```

This creates the configured database if it does not exist and applies revisions; it does not delete or replace an existing database. Before upgrading an existing installation, make a separate backup and check the current revision with `flask db current`. For a legacy database created with `db.create_all()` before opening-balance tracking was added, compare its schema to the initial revision (`61f1d9fad4ef`) first. If it matches that schema, baseline at that revision without replaying table creation, then apply later revisions:

```powershell
.venv\Scripts\flask.exe --app run:app db stamp 61f1d9fad4ef
.venv\Scripts\flask.exe --app run:app db upgrade
```

For a legacy database whose schema already includes all revisions, stamp `head` only after comparing it to the latest migration. Do not stamp a database whose schema differs from the revision you select. Review and create a tailored baseline/upgrade revision for that database instead. Generate later revisions with `flask db migrate -m "describe schema change"`, inspect the generated file, and apply with `flask db upgrade`. Alembic autogeneration must always be reviewed before use.

## Run

```powershell
$env:FLASK_CONFIG = "development"
.venv\Scripts\flask.exe --app run:app db upgrade
.venv\Scripts\python.exe run.py
```

Open `http://127.0.0.1:5000`. With public registration enabled, the first person registering a company becomes its administrator. Administrators can add accountants from **Team** and maintain the company’s chart of accounts from **Accounts**. When registration is disabled, create the initial administrator interactively after applying migrations:

```powershell
.venv\Scripts\flask.exe --app run:app create-admin
```

The command prompts for the company, administrator, and a hidden confirmed password; it does not create or alter database tables.

## Use

1. Register a company (or use `create-admin` when public registration is disabled). The first user is its administrator and receives a seeded chart of accounts.
2. From **Opening balances**, enter starting balances for asset, liability, and equity accounts. Assets normally debit; liabilities and equity normally credit. Negative values record the opposite-side balance. The whole opening journal must balance, and only one active initialization is allowed per company/date. To correct it, reverse the existing opening entry, then submit the corrected balances for the same date.
3. Administrators can add, edit, and remove unused accounts from **Accounts**. Account codes are unique within a company. Posted entries prevent deletion, and the type/classification/cash status of used accounts is locked to preserve historical reporting.
4. Record income and expenses, or download the CSV template from **Import CSV**. Preview validates account codes, duplicates, dates, and amounts; select the destination Cash/Bank account on the preview before atomic import.
5. Use **Reports** to select a reporting period and view the Income Statement, Balance Sheet, and Cash Flow Statement. PDF and Excel downloads use the same calculations; Excel can export each statement or all three.

CSV columns remain `date,type,amount,description,account_code`; `type` is `income`, `expense`, or `asset_purchase`, dates use `YYYY-MM-DD`, and account codes must identify an account of the matching type. For `asset_purchase`, choose an eligible non-cash investing asset (for example Equipment 1500). Choose the destination Cash/Bank account from the current company during the preview confirmation. Asset purchases debit the asset and credit selected cash/bank; they do not reduce current profit. The downloaded CSV template includes an asset-purchase example and uses the same columns.

Posted entries are immutable. Corrections should be made using a reversal entry. Income and expense entries require a cash/bank line and use the normal posting side; opening entries are restricted to balance-sheet accounts. Internal cash/bank transfers do not affect cash flows. Opening-balance entries establish opening balances and are excluded from period income and cash activity. Non-cash-only, balance-sheet entries do not affect cash flows. Cash flow classification is required for the non-cash account paired with cash movement.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Tests use an isolated in-memory SQLite database and do not open the development database.

PostgreSQL workflow tests are opt-in and require a local disposable database named exactly `financial_report_generator_test`. The `postgresql_testing` configuration only accepts a `postgresql+psycopg` URL on loopback and has no SQLite fallback. On a machine with Docker Desktop, run these commands from PowerShell to create and use a temporary PostgreSQL 16 container:

```powershell
docker run --rm --detach --name frg-postgres-test `
  --env POSTGRES_USER=frg_test `
  --env POSTGRES_PASSWORD=frg_test `
  --env POSTGRES_DB=financial_report_generator_test `
  --publish 127.0.0.1:55432:5432 postgres:16-alpine

$ready = $false
while (-not $ready) {
  docker exec frg-postgres-test pg_isready -U frg_test -d financial_report_generator_test
  $ready = ($LASTEXITCODE -eq 0)
  if (-not $ready) { Start-Sleep -Seconds 1 }
}

.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:FLASK_CONFIG = "postgresql_testing"
$env:POSTGRES_TEST_DATABASE_URL = "postgresql+psycopg://frg_test:frg_test@127.0.0.1:55432/financial_report_generator_test"
$env:DATABASE_URL = $env:POSTGRES_TEST_DATABASE_URL
.venv\Scripts\flask.exe --app run:app db upgrade
.venv\Scripts\flask.exe --app run:app db current
$env:FRG_TEST_POSTGRES = "1"
.venv\Scripts\python.exe -m pytest -q tests\test_accounting_workflows.py
docker stop frg-postgres-test
```

Use this only for a newly created disposable container. The PostgreSQL workflow test fixture creates and drops tables in its selected test database. Keep `DATABASE_URL` pointed at that same disposable database for the migration commands; never use a production or personal URL.

## Current limits

This is a simplified, single-currency cash-basis ledger. It does not include tax, payroll, inventory, depreciation, banking connections, or multi-currency. Back up the SQLite file regularly.
