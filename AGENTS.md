# AGENTS.md — FuelV Developer & Agent Guide

> **Project Name:** FuelV — The Visual Fuel Logger  
> **Repository:** `MrMallik/fuelv`  
> **Production Target:** `https://fuelv.pritabrata.com/` (or `https://pritabrata.com/fuelv/`)

This document is the single source of truth for AI agents and developers working on the FuelV codebase. It details the project's purpose, architecture, domain rules, testing procedures, and production deployment pipeline.

---

## 1. Project Overview & Mission

FuelV is a lightweight, mobile-first visual fuel logging application designed for motorcycles and cars. 

Instead of manual data entry at the fuel station, drivers capture two quick snapshots:
1. **Odometer Display**: Vehicle mileage.
2. **Fuel Pump Display**: Total amount paid (₹) and volume dispensed (Liters).

The application feeds these images through a Vision-Language Model (VLM) pipeline to extract the structured contract:
```json
{
  "odometer_reading": 14520.5,
  "total_expense": 550.0,
  "fuel_dispensed": 5.38
}
```

### Core Value Propositions:
- **Zero-Friction Logging**: Automated snapshot extraction with instant manual fallback.
- **Real-Time Fuel Efficiency (km/L)**: Dynamic chronological delta computation ($\Delta \text{km} / \text{Liters}$) tracking every fill-up.
- **Interactive Heatmap & Telemetry**: Chronological matrix aggregating expenses and distance by Day, Week, Month, and Year.
- **Privacy & Security**: Zero third-party tracker bloat, local-first UUID write-key protection, and private SQLite WAL storage.

---

## 2. System Architecture

```
                    ┌──────────────────────────────────────────┐
                    │          Client Browser (Vue 3)          │
                    │   • Mobile-First Responsive SPA          │
                    │   • PWA / Camera / Heatmap / UI Tables   │
                    └─────────────────────┬────────────────────┘
                                          │
                            HTTP / REST   │ (Reverse Proxy :8000)
                                          ▼
                    ┌──────────────────────────────────────────┐
                    │           FastAPI Backend                │
                    │   • Static SPA Catch-All Delivery        │
                    │   • UUID Timing-Safe Auth Check          │
                    │   • Image Upload & Sanitization          │
                    └───────────┬───────────────────┬──────────┘
                                │                   │
              ┌─────────────────┴────┐        ┌─────┴────────────────┐
              ▼                      ▼        ▼                      ▼
      SQLite3 (WAL Mode)     Cloudflare R2    Gemini 3.5 Flash Lite  Custom Model
      `data/fuelv.db`        `public-bucket`  (Default VLM)          (Self-Hosted)
                             `fvs/<file>`
```

### Tech Stack:
* **Frontend**: Vue 3 (`<script setup lang="ts">`), Vite 5, Vue Router 4, Lucide Icons, Shadcn-style UI primitives (`UiTable`, `UiButton`, `UiBadge`, `UiInput`).
* **Backend**: Python 3.12+ FastAPI, Uvicorn, SQLite3 with WAL journal mode, Pydantic v2 schemas.
* **Extraction Pipeline**: Swappable VLM factory (`GeminiExtractor` via Google Gemini API, or custom HTTP microservice).
* **Containerization**: Multi-stage Docker build (Node 22 builder $\to$ Python 3.12 slim runtime).

---

## 3. Directory Layout

```
fuelv/
├── .github/workflows/
│   └── docker-image.yml         # CI/CD: checks, multi-arch image build & SSH deploy
├── backend/
│   ├── db/
│   │   ├── repository.py        # SQLite repository & chronological chain recalculator
│   │   └── schema.sql           # SQLite table schema & WAL configuration
│   ├── extraction/
│   │   ├── base.py              # Base extractor class & swappable factory
│   │   └── gemini.py            # Gemini 3.5 Flash Lite VLM extraction implementation
│   ├── scripts/
│   │   ├── import_csv.py        # Fuelio CSV import utility
│   │   └── migrate_images_to_r2.py # Production local-to-Cloudflare-R2 image migrator
│   ├── storage/
│   │   └── r2.py                # Cloudflare R2 (public-bucket/fvs/) SigV4 client
│   ├── tests/
│   │   ├── test_backend.py      # Core unit tests (normalization, repository, auth)
│   │   └── test_intensive_qa_security.py # Intensive QA & security attack vector suite
│   ├── main.py                  # FastAPI application, route handlers & static file serving
│   └── requirements.txt         # Backend Python dependencies
├── data/                        # Persistent volume mount point (git-ignored)
│   ├── fuelv.db                 # SQLite database
│   └── images/                  # Stored snapshot photos
├── deploy-scripts/
│   ├── remote-deploy.sh         # CI runner script to trigger remote SSH deployment
│   ├── registry-deploy.sh       # Server-side script to pull GHCR image & restart compose
│   └── smoke.sh                 # Post-deployment health verification script
├── frontend/
│   ├── public/                  # Static assets (favicons, touch icons)
│   ├── src/
│   │   ├── assets/logo/         # Multi-resolution brandmark PNG suite (1024px to 16px)
│   │   ├── components/          # Reusable UI components (Heatmap, CameraModal, EditLogModal)
│   │   ├── router/index.ts      # Vue Router routes
│   │   ├── services/
│   │   │   ├── api.ts           # REST API client with timeout/retry & localStorage state
│   │   │   ├── draftStorage.ts  # Client IndexedDB draft & photo persistence across refreshes
│   │   │   └── imageOptimizer.ts # Client-side canvas compression (~250KB high-clarity JPEG)
│   │   ├── views/               # Single-page views (DashboardView, CaptureView)
│   │   ├── App.vue              # App shell & header navigation
│   │   └── style.css            # Dark-theme design tokens & typography
│   ├── index.html               # SPA root template with mobile-viewport zoom locks
│   ├── package.json             # Frontend dependencies & Vite scripts
│   └── vite.config.ts           # Vite build & proxy configuration
├── compose.yaml                 # Production Docker Compose definition
├── Dockerfile                   # Multi-stage production container definition
├── dev.sh                       # Local simultaneous development launcher
├── start-api.sh                 # Local backend API launcher
└── README.md                    # Public project documentation
```

---

## 4. Key Domain Rules & Conventions

1. **Terminology**:
   - Always standardize on **"log / logs"** for user-facing UI copy and internal symbols (e.g. `total_logs`, "All Fuel Logs", "Log Fuel"). Avoid mixed terms like "entry" or "fill-up" in UI labels.
2. **Chronological Chain Recalculation**:
   - In [`backend/db/repository.py`](file:///Users/pritabrata/code/fuelv/backend/db/repository.py), `_recalculate_chronological_chain(conn)` guarantees that whenever a log is added, edited, or deleted, all chronological subsequent records receive automatically updated `previous_odometer`, `distance_delta`, and `km_per_l`.
   - Never compute deltas in isolation without ensuring subsequent logs remain consistent.
3. **Fuel Efficiency Metric Calculation**:
   $$\text{km/L} = \frac{\text{Current Odometer} - \text{Previous Odometer}}{\text{Fuel Dispensed (L)}}$$
   - Guard against zero or negative fuel volumes.
   - If current odometer $\le$ previous odometer, delta and km/L must not be computed.
4. **Local-First Write Protection**:
   - Write operations (`POST /api/snapshots`, `PUT /api/snapshots/{id}`, `DELETE /api/snapshots/{id}`, `/api/extract`) require a UUID access key sent via header `X-FuelV-Key`.
   - Key verification uses `hmac.compare_digest` in `backend/main.py` for timing-attack resistance.
   - Read-only queries (`GET /api/snapshots/grouped`, `GET /api/snapshots/latest`, `GET /api/health`) are publicly accessible without authentication.
5. **Mobile Viewport Constraints**:
   - Enforce `font-size: 16px !important` on mobile form inputs to prevent iOS Safari auto-zoom on focus.
   - Set `<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover" />` in `frontend/index.html`.
6. **Strict Test-Driven Development (TDD) Mandate**:
   - This repository is strictly built using Test-Driven Development (TDD). For any new feature, bug fix, or refactor, write comprehensive tests **BEFORE** writing production code.
   - Test suites are organized under `backend/tests/`:
     - `backend/tests/feature/`: Specification tests for new features, domain contracts, and user flows.
     - `backend/tests/regression/`: Regression test suites verifying that existing system guarantees (e.g. chronological chain recalculation, Cloudflare R2 SigV4 uploads, UUID write-key timing-safe checks, SQLite WAL concurrency, draft restoration) never break.
     - Root `backend/tests/`: Baseline unit and configuration tests (`test_backend.py`, `test_config.py`, `test_intensive_qa_security.py`).
   - Every test must be runnable via standard Python `unittest discover`.

---

## 5. Development & Testing Commands

### Mise Polyglot Tool Manager (Recommended)
This repository uses [mise](https://mise.jdx.dev) (`.mise.toml`) to pin **Python 3.12** and **Node 22**, auto-activate `.venv`, auto-load `.env`, and provide unified tasks:
```bash
# First-time setup (installs runtimes and packages)
mise run setup

# Run all 82 backend tests + frontend production build
mise run test

# Run backend tests only
mise run test:backend

# Run feature or regression tests individually
mise run test:feature
mise run test:regression

# Start simultaneous local development (Backend :8000 + Frontend :5173)
mise run dev

# Validate shell syntax & TypeScript types
mise run lint
```

### Direct Tooling Fallbacks (Without Mise)
#### Python Backend Tests
```bash
.venv/bin/python3 -m unittest discover -s backend/tests -p "test_*.py" -v
```

#### Frontend Build & Lint
```bash
cd frontend && npm install && npm run build
```

#### Simultaneous Local Dev Server
```bash
./dev.sh
# Backend runs on http://127.0.0.1:8000
# Frontend runs on http://127.0.0.1:5173
```

---

## 6. Production Deployment Pipeline

Deployment is fully automated through GitHub Actions ([`.github/workflows/docker-image.yml`](file:///Users/pritabrata/code/fuelv/.github/workflows/docker-image.yml)) upon merging into `main`.

### Pipeline Stages:
1. **Validation & Checks**:
   - Runs shell validation (`bash -n deploy-scripts/*.sh`).
   - Validates `compose.yaml` syntax.
   - Runs backend test suite (`unittest discover`).
   - Runs frontend production build (`npm run build`).
2. **Container Build & Registry Publish**:
   - Multi-stage build packages Vue SPA assets directly into the FastAPI container.
   - Pushes release image to GitHub Container Registry (`ghcr.io/mrmallik/fuelv:<sha>`).
3. **Automated SSH Rollout**:
   - GitHub Actions connects to production host via SSH (`deploy-scripts/remote-deploy.sh`).
   - Executes `deploy-scripts/registry-deploy.sh` on the server:
     - Pulls newly published container digest from GHCR.
     - Performs zero-downtime container recreation (`docker compose up -d`).
     - Verifies health check on `http://127.0.0.1:8000/api/health`.
4. **Data Persistence**:
   - Stored in Docker named volume `fuelv_data` mounted at `/app/data`:
     - SQLite Database: `/app/data/fuelv.db`
     - Snapshot Photos: `/app/data/images/`
   - Data persists across all container updates and image rebuilds.

---

## 7. Agent Git Commit & Pull Request Rules

Whenever an AI agent creates git commits, pushes branches, or opens pull requests:
1. **Commit Attribution**:
   Always attribute automated commits to the bot identity via inline environment variables:
   ```bash
   GIT_AUTHOR_NAME="Antigravity (Gemini)" \
   GIT_AUTHOR_EMAIL="gemini-bot@users.noreply.github.com" \
   GIT_COMMITTER_NAME="Antigravity (Gemini)" \
   GIT_COMMITTER_EMAIL="gemini-bot@users.noreply.github.com" \
   git commit -m "feat: description"
   ```
2. **User Config Integrity**:
   Never modify the user's personal git configuration (`git config user.name` / `git config user.email`).
3. **Pull Request Quality Standards**:
   - Always run frontend and backend test suites before raising a PR.
   - PR titles must follow Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`).
   - PR bodies must contain clear change bullet points, test verification performed, and attribution note:
     `> 🤖 Autonomous PR prepared by **Antigravity (Gemini)** for review by @MrMallik.`
