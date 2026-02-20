# SecureMail — Webmail Aggregator

Modern webmail client that acts as a beautiful frontend for Outlook accounts via OAuth2 + IMAP.

## Architecture

```
┌──────────────┐     ┌──────────────────────┐     ┌──────────────────┐
│   Frontend   │────▶│   FastAPI Backend     │────▶│  Outlook IMAP    │
│  (HTML/JS)   │◀────│  (Auth, Mail, Admin)  │◀────│  (OAuth2 XOAUTH2)│
└──────────────┘     └──────────┬───────────┘     └──────────────────┘
                               │
                     ┌─────────▼─────────┐
                     │   PostgreSQL DB    │
                     │  (Users, Accounts) │
                     └───────────────────┘
```

## Database Schema

- **outlook_accounts** — stores Outlook email, refresh_token, client_id
- **users** — login/password pairs linked to one Outlook account each

## Quick Start (Local)

```bash
# 1. Clone and cd into the project
cd mail2

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env file
copy .env.example .env
# Edit .env with your database URL and secrets

# 5. Run
uvicorn app.main:app --reload --port 8000
```

Then open:
- **Mail client:** http://localhost:8000
- **Admin panel:** http://localhost:8000/admin

## Deploy to Railway

### 1. Create Railway project
- Go to [railway.app](https://railway.app), create new project
- Add a **PostgreSQL** service (click "New" → "Database" → "PostgreSQL")
- Add a new service from your **GitHub repo** (or deploy via CLI)

### 2. Set environment variables
In your Railway service settings, add these variables:

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | `postgresql+asyncpg://...` (use Railway's `${{Postgres.DATABASE_URL}}` but replace `postgresql://` with `postgresql+asyncpg://`) |
| `SECRET_KEY` | Random string (e.g., `openssl rand -hex 32`) |
| `ADMIN_PASSWORD` | Your admin panel password |
| `APP_NAME` | Your app name (e.g., "SecureMail") |
| `APP_DOMAIN` | Your domain |

> **Important:** Railway provides `DATABASE_URL` in format `postgresql://...`. You need to use `postgresql+asyncpg://...` for async SQLAlchemy. Set it as a variable reference: `postgresql+asyncpg://${{Postgres.USER}}:${{Postgres.PASSWORD}}@${{Postgres.HOST}}:${{Postgres.PORT}}/${{Postgres.DATABASE}}`

### 3. Deploy
Railway auto-detects the Dockerfile or Procfile and deploys.

## Usage

### Admin workflow:
1. Go to `/admin`, enter your `ADMIN_PASSWORD`
2. Paste Outlook accounts in bulk format:
   ```
   email@outlook.com:pass123:recovery@mail.com:recpass:M.R3_BL2.xxxxx:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```
3. Create users (login + password + link to Outlook account ID)
4. Give login/password credentials to end users

### User workflow:
1. Go to `/` (main page)
2. Enter the login and password provided by admin
3. See inbox, read emails

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/login` | User login |
| GET | `/api/mail/inbox` | Get inbox emails |
| GET | `/api/mail/message/{uid}` | Get single email |
| POST | `/api/mail/refresh` | Clear mail cache |
| POST | `/api/admin/login` | Admin login |
| POST | `/api/admin/bulk-upload` | Bulk import accounts |
| GET | `/api/admin/accounts` | List Outlook accounts |
| POST | `/api/admin/users` | Create user |
| GET | `/api/admin/users` | List users |
| POST | `/api/admin/link-account` | Link account to user |
| DELETE | `/api/admin/users/{id}` | Delete user |
| DELETE | `/api/admin/accounts/{id}` | Delete account |

## Tech Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy (async), uvicorn
- **Frontend:** Vanilla JS, TailwindCSS (CDN), modern minimal design
- **Database:** PostgreSQL (async via asyncpg)
- **Auth:** JWT tokens (python-jose), bcrypt passwords
- **Mail:** Microsoft OAuth2 refresh_token → access_token → IMAP XOAUTH2
- **Caching:** In-memory TTLCache (tokens ~50min, emails ~2min)
