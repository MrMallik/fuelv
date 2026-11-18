# FuelV — The Visual Fuel Logger

A lightweight, extensible fullstack fuel logging web app built with a **Vue 3 (Vite + Vue Router)** frontend (`frontend/`) and a **Python FastAPI + SQLite3** backend (`backend/`), styled to match `portfolio-website`.

## Core Features
1. **Dual-Image Snapshot Capture + Manual Fallback**: Capture/upload photos of the **Odometer** and **Fuel Pump Display**, or enter values manually.
2. **Swappable JSON Extraction Pipeline**:
   - Returns a strict 3-parameter JSON contract:
     ```json
     {
       "odometer_reading": 14520.5,
       "total_expense": 550.0,
       "fuel_dispensed": 5.38
     }
     ```
   - Uses **Gemini 3.5 Flash Lite** (`EXTRACTOR_BACKEND=gemini`) out of the box and can be swapped to your own fine-tuned model (`EXTRACTOR_BACKEND=custom`) via environment variables without touching UI or database code.
3. **Extensible SQLite3 Schema + Ground-Truth Training Data**:
   - Stores confirmed values in indexed columns (`odometer_reading`, `total_expense`, `fuel_dispensed`, `captured_at`) while preserving `raw_extraction_json` (for future fine-tuning dataset collection) and `extra_data` (a JSON column for arbitrary future fields like `vehicle`, `station`, `unit_price`, `km_per_l`, etc.).
4. **Grouped Views by Week / Month / Year**:
   - View snapshots and period expense/fuel totals grouped chronologically by **Week**, **Month**, or **Year**.

---

## Quick Start

```bash
cp .env.example .env
# Edit .env and set GEMINI_API_KEY=your_key_here

# Start both backend (:8000) and frontend (:5173)
./dev.sh
```

Or run them separately:
- **Backend (`backend/`)**: `./start-api.sh`
- **Frontend (`frontend/`)**: `cd frontend && npm install && npm run dev`

---

## Running Backend Unit Tests
```bash
python3 -m unittest discover -s backend/tests -p "test_*.py" -v
```
