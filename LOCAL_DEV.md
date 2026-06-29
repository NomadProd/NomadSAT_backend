# Local development setup

This guide explains how to run the TuranSAT backend and frontend together after cloning the repositories.

You need **both** repositories:

- `NomadSAT_backend` (this repo) — FastAPI API on port **8000**
- `NomadSAT_frontend` — Flutter web app (any localhost port)

```
Flutter web (localhost)  →  FastAPI (localhost:8000)  →  Supabase Postgres
```

---

## Prerequisites

| Tool | Purpose |
|------|---------|
| **Python 3.11+** | Backend runtime |
| **Flutter SDK** (web enabled) | Frontend (`flutter config --enable-web`) |
| **Supabase project** | Hosted Postgres database |
| **Cloudinary account** | Optional; only needed for homework photo uploads |

---

## 1. Backend setup

### Clone and install

```bash
git clone https://github.com/NomadProd/NomadSAT_backend.git
cd NomadSAT_backend

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Or use the dev script (creates the venv and installs deps automatically):

```bash
./scripts/dev-backend.sh
```

### Environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in real values:

| Variable | Where to get it |
|----------|-----------------|
| `DATABASE_URL` | Supabase → Project Settings → Database → Connection string → **URI** → **Session pooler** (port 5432) |
| `JWT_SECRET_KEY` | Generate: `openssl rand -hex 32` |
| `CLOUDINARY_*` | Cloudinary dashboard (optional for local login/testing) |

#### Database URL (important)

Use the **session pooler** URI, **not** the direct connection (`db.xxx.supabase.co`).

```
postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require
```

- Username must be `postgres.PROJECT_REF` (not plain `postgres`)
- Copy `PROJECT_REF` and `REGION` from your Supabase dashboard (region may be `aws-1-...`, not always `aws-0-...`)
- Keep `?sslmode=require` at the end

Direct connections often fail from local machines. The pooler URL is required for reliable local dev.

#### Local dev values

These settings are required for cookie auth over HTTP localhost:

```env
AUTH_COOKIE_SECURE=false
AUTH_COOKIE_DOMAIN=
```

`CORS_ORIGINS` can list production URLs only. The backend also allows any `http://localhost:*` origin automatically during development.

### Run the backend

```bash
source .venv/bin/activate
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Verify: open [http://localhost:8000/docs](http://localhost:8000/docs)

Quick DB check:

```bash
.venv/bin/python -c "from database import engine; from sqlalchemy import text; print(engine.connect().execute(text('SELECT 1')).scalar())"
```

Should print `1`.

---

## 2. Frontend setup

In a **separate terminal**:

```bash
git clone https://github.com/NomadProd/NomadSAT_frontend.git
cd NomadSAT_frontend

flutter pub get
flutter run -d chrome
```

Or use the dev script (runs on port 55555):

```bash
./scripts/dev-frontend.sh
```

### API URL

When running on `localhost`, the frontend automatically uses `http://localhost:8000` as the API base URL. No extra flags are needed.

To override (e.g. hit staging):

```bash
flutter run -d chrome --dart-define=API_BASE_URL=https://api.turansat.com
```

### Auth

The app uses **HTTP-only cookies**. The browser sends credentials to the backend on every request (`withCredentials: true`). The backend must be running before you log in.

---

## 3. Smoke test

1. Backend running → `http://localhost:8000/docs` loads
2. Frontend running → login page loads on `http://localhost:<port>/#/login`
3. Log in with a user that exists in the Supabase database
4. DevTools → Network → login response should include `Set-Cookie: access_token=...`
5. Subsequent `/auth/me` requests should return 200

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Failed to fetch, uri=https://api.turansat.com/...` | Frontend not on localhost, or you passed a production `API_BASE_URL`. On localhost it should auto-detect `http://localhost:8000`. |
| CORS error in browser | Restart backend after changing `.env`. Any `http://localhost:<port>` origin is allowed by default. |
| Login works, then `/auth/me` fails | Set `AUTH_COOKIE_SECURE=false` in backend `.env` for HTTP localhost. |
| `ModuleNotFoundError: No module named 'fastapi'` | Activate `.venv` and run `pip install -r requirements.txt`. |
| `InterfaceError` / can't connect to `db.xxx.supabase.co` | Switch `DATABASE_URL` to the **session pooler** URI from Supabase. |
| `401 Wrong credentials` | User doesn't exist in DB, or password is wrong. |
| Backend 500 on login | Database connection or schema issue. Confirm `SELECT 1` works and tables exist. |

---

## Production notes

For production deployments:

- Set `AUTH_COOKIE_SECURE=true`
- Set `AUTH_COOKIE_DOMAIN` if cookies must work across subdomains
- Set `CORS_ORIGINS` to your real frontend URLs
- Use `--dart-define=API_BASE_URL=https://api.turansat.com` when building the Flutter web app

See `NomadSAT_frontend/DEPLOY_FRONTEND.md` for frontend deployment details.
