# 📡 Crypto Alert System

A **self-hosted, Dockerized, multi-user cryptocurrency alert system** powered by [Kraken](https://www.kraken.com/) public market data.

> ⚠️ **Disclaimer:** This is a price-monitoring and alerting tool only. It does **not** execute trades, manage funds, or provide financial advice.

---

## Overview

Crypto Alert System lets you define price threshold rules for any Kraken-listed USD asset and receive notifications when those thresholds are crossed. Everything runs on your own infrastructure — no cloud subscriptions, no data sharing.

### Key Features

| Feature | Description |
|---|---|
| 🔔 Price alerts | Above / below / % change triggers |
| 📊 Charts | Price history with alert overlays and threshold zones |
| 📡 Live ticker | Dedicated full-screen scrolling market display |
| 👥 Multi-user | Per-user alerts, preferences, and notification settings |
| 📧 Email (SMTP) | SSL, STARTTLS, or plain — configurable per server |
| 🔔 Push notifications | ntfy, Discord webhook, Telegram bot |
| ⚙️ Admin panel | Manage users, featured markets, retention, backup/restore |
| 🌐 Kraken integration | Dynamic asset discovery — any USD pair, not just a fixed list |
| 🔒 Security | Rate limiting, login lockout, session expiry, IP allowlist |
| 🗄️ Backup & restore | JSON export/import of rules, settings, and history |
| 🧪 Simulation mode | Replay historical data against your rules before going live |

### Screenshots

> _Screenshots go here — open an issue or PR to contribute them._

---

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) ≥ 24
- [Docker Compose](https://docs.docker.com/compose/) ≥ 2

### 1. Clone and configure

```bash
git clone https://github.com/jweese74/crypto-alerts.git
cd crypto-alerts
cp .env.example .env
```

Open `.env` and set **at minimum** these three values:

```env
POSTGRES_PASSWORD=choose-a-strong-password
SECRET_KEY=run: openssl rand -hex 32
FIRST_ADMIN_EMAIL=you@example.com
FIRST_ADMIN_PASSWORD=YourAdminPassword1!
```

> Generate a secure secret key:
> ```bash
> openssl rand -hex 32
> ```

### 2. Start the stack

```bash
docker compose up -d
```

The app will:
- start PostgreSQL and wait until it is healthy
- create all database tables automatically on first run
- seed the initial admin account using `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD`

### 3. Open the browser

```
http://localhost:8000
```

Log in with your admin credentials. The default port is `8000` — change `APP_PORT` in `.env` to use a different one.

### Using a pre-built image

Instead of building locally, you can pull the published image:

```bash
docker pull jweese74/crypto-alerts:latest
```

Edit `docker-compose.yml` and replace the `build: .` line under the `app` service with:

```yaml
image: jweese74/crypto-alerts:latest
```

---

## Configuration

All configuration is done through environment variables in `.env`. See `.env.example` for the full list with descriptions.

### Required

| Variable | Description |
|---|---|
| `POSTGRES_PASSWORD` | Database password |
| `SECRET_KEY` | 32+ byte random hex string for signing sessions |

### Admin account (first run)

| Variable | Default | Description |
|---|---|---|
| `FIRST_ADMIN_EMAIL` | — | Email for the initial admin user |
| `FIRST_ADMIN_PASSWORD` | — | Password for the initial admin user |
| `FIRST_ADMIN_USERNAME` | `admin` | Display username |

If `FIRST_ADMIN_EMAIL` is not set, no admin is auto-created — you can register one manually if `REGISTRATION_ENABLED=true`.

### SMTP (email alerts)

| Variable | Default | Description |
|---|---|---|
| `SMTP_HOST` | `localhost` | Mail server hostname |
| `SMTP_PORT` | `587` | Port number |
| `SMTP_MODE` | `starttls` | `ssl` / `starttls` / `none` |
| `SMTP_USER` | — | SMTP username |
| `SMTP_PASSWORD` | — | SMTP password |
| `SMTP_FROM` | `alerts@example.com` | Sender address |

**Port guidance:**
- Port **465** → use `SMTP_MODE=ssl`
- Port **587** → use `SMTP_MODE=starttls`
- Port **25** → use `SMTP_MODE=none` (no auth relay)

Test your SMTP settings from **Admin → Settings → SMTP → Send Test Email**.

### Alert engine

| Variable | Default | Description |
|---|---|---|
| `KRAKEN_POLL_INTERVAL_SECONDS` | `60` | How often to fetch prices from Kraken |
| `ALERT_COOLDOWN_MINUTES` | `60` | Minimum time between repeat alerts for the same rule |

### Security

| Variable | Default | Description |
|---|---|---|
| `LOGIN_MAX_ATTEMPTS` | `5` | Failed logins before lockout |
| `LOGIN_LOCKOUT_MINUTES` | `30` | Lockout duration |
| `HTTPS_ONLY` | `false` | Set `true` behind a reverse proxy with TLS |
| `SESSION_IDLE_TIMEOUT_HOURS` | `8` | Idle session expiry |

---

## Usage

### Creating an alert

1. Log in and go to **Alerts → New Rule**
2. Select an asset (any Kraken USD pair)
3. Choose a condition: *above*, *below*, or *% change*
4. Set a threshold price
5. Choose notification channels
6. Optionally set active hours (e.g. only alert during 09:00–17:00 in your timezone)
7. Save — the rule is active immediately

### Viewing charts

Go to **Assets** and click any asset to see its price history with:
- Your alert threshold lines overlaid
- Shaded trigger zones
- Moving averages (10 / 50 period)
- Tooltips showing price, timestamp, and alert info

### Ticker view

Click **📡 Ticker** in the navigation bar to open the live ticker page.

The ticker shows:
- **Featured markets** — selected by the admin (Admin → Assets → Featured Markets)
- **Your personal assets** — added via the Manage Ticker link
- **Alert assets** — any asset you have an active alert for appears automatically

The ticker page is designed for passive full-screen monitoring. Press F11 for browser full-screen.

### Managing featured markets (admin)

Go to **Admin → Assets → Featured Markets** to:
- Add any supported Kraken USD pair as a featured market
- Set a friendly display name
- Reorder with ▲ / ▼ buttons
- Enable or disable individual entries

Featured markets appear at the top of the ticker and on the dashboard.

### Simulation mode

Go to **Alerts → Simulate** to test a rule against historical price data without triggering real notifications.

### Backup & restore

Go to **Admin → Backup** to:
- Export all rules, settings, and history as JSON
- Restore from a previously exported file

---

## Docker

### Building locally

```bash
docker compose build
docker compose up -d
```

### Volumes

| Volume | Purpose |
|---|---|
| `postgres_data` | PostgreSQL data — persists across restarts |

### Stopping and restarting

```bash
docker compose down        # stop (data preserved)
docker compose down -v     # stop and DELETE all data
docker compose restart app # restart only the app
```

### Viewing logs

```bash
docker compose logs -f app
docker compose logs -f db
```

---

## Updating

```bash
# Pull latest image (if using pre-built)
docker compose pull

# Or rebuild from source
git pull
docker compose build

# Apply and restart
docker compose up -d
```

Database schema changes are applied automatically on startup.

---

## Running tests

```bash
docker compose run --rm app python -m pytest -q
```

---

## Reverse proxy (optional)

For production, place the app behind nginx or Caddy with TLS. Set `HTTPS_ONLY=true` in `.env` once TLS is in place.

Example Caddy config:

```
alerts.example.com {
    reverse_proxy localhost:8000
}
```

---

## Security notes

- This app does **not** connect to any exchange account
- No API keys, trading credentials, or wallets are used or stored
- All market data is fetched from the [Kraken public REST API](https://docs.kraken.com/api/) — no authentication required
- Passwords are hashed with bcrypt
- Sessions are signed with `SECRET_KEY`
- Keep your `.env` file private and never commit it

---

## Project structure

```
crypto-alerts/
├── app/
│   ├── main.py                  # FastAPI app, lifespan hooks, startup seeding
│   ├── api/
│   │   ├── deps.py              # Shared dependencies (auth, CSRF, DB)
│   │   └── routes/              # auth, alerts, admin, dashboard, ticker, …
│   ├── crud/                    # Database access layer
│   ├── models/                  # SQLAlchemy ORM models
│   ├── schemas/                 # Pydantic request/response models
│   ├── services/                # alert_engine, market_data, email, scheduler, …
│   ├── core/                    # config, database, security, logging
│   ├── templates/               # Jinja2 HTML templates
│   └── static/                  # CSS, JS
├── migrations/                  # Alembic migration environment
├── tests/                       # pytest test suite
├── docker/postgres/init.sql     # PostgreSQL initialisation SQL
├── scripts/
│   └── init.sh                  # Convenience first-run helper
├── .github/workflows/           # GitHub Actions CI
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── requirements.txt
└── alembic.ini
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 (async) |
| Database | PostgreSQL 16 |
| Migrations | Alembic |
| Templates | Jinja2 |
| Market data | Kraken public REST API |
| Container | Docker + Docker Compose |

---

## License

[MIT License](LICENSE) — free to use, modify, and self-host.

---

## Contributing

Issues and pull requests are welcome. Please open an issue before submitting large changes.
