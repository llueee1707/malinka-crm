# Malinka CRM

CRM/PWA for a beauty salon built with `FastAPI`, `SQLAlchemy`, `SQLite`, and `Bootstrap 5`.

## Features

- JWT authentication in `HttpOnly` cookies
- Roles: director, administrator, master
- Appointments calendar and day schedule
- Clients, services, users, avatars, and master service prices
- Salon working hours and master availability statuses
- Booking requests from the public landing page
- PWA manifest and service worker

## Clean Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Paste the generated value into `SECRET_KEY` in `.env`, then set:

```dotenv
SEED_DEMO_DATA=false
BOOTSTRAP_DIRECTOR_LOGIN=Admin
BOOTSTRAP_DIRECTOR_PASSWORD=<set-client-password>
BOOTSTRAP_DIRECTOR_FULL_NAME=<client-admin-name>
```

Run the server:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8099
```

On the first start, the app creates a fresh `salon.db` with one director user from `.env`. Demo clients, masters, services, and appointments are not created when `SEED_DEMO_DATA=false`.

See [DEPLOY.md](DEPLOY.md) for the Windows client laptop setup.
