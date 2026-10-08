# Program Standards

## Architecture

- Backend: Django + Django REST Framework.
- Storage: PostgreSQL in production, SQLite allowed for local development.
- File uploads: store outside the code tree.
- Deployment: Ubuntu 22.04 NUC directly runs PostgreSQL, Gunicorn, Nginx, and systemd. Docker is not a delivery dependency.

## Code style

- Use explicit model names and verbose status enums.
- Favor small apps over a single large app.
- Keep serializers narrow.
- Keep views thin.
- Use service helpers for code generation and audit trail creation.

## Data rules

  - Assets and workflow records must be auditable.
  - Inventory checks must use a frozen book snapshot, immutable scan records, and administrator-reviewed adjustment records; counters must not edit balances directly.
- QR codes should point to stable asset identifiers.
- Photos and attachments must be linked to concrete records.

## API rules

- Use `/api/` prefix.
- Keep response shapes predictable.
- Add pagination before list endpoints grow.

## Security rules

- Do not expose database ports publicly.
- Use internal-only access for admin surfaces.
- Store secrets in `.env`.
