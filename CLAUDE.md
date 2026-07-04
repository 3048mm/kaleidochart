# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 言語設定
- 常に日本語で会話する
- コメントも日本語で記述する
- エラーメッセージの説明も日本語で行う
- ドキュメントも日本語で生成する

## Project overview

A personal stock analysis/screening web tool (Japanese-language docs and UI). It computes Relative Strength (vs SPY), ATR-based volatility, volume surge, and Minervini Trend Template signals, then serves them through a batch-computed backend so the frontend only reads pre-aggregated data.

- `backend/` — Python: FastAPI server + data collection/indicator batch pipeline + backtest engine
- `frontend/` — Vite + React (TypeScript) SPA, TradingView `lightweight-charts`
- `doc/` — architecture and spec documents (source of truth — read before making non-trivial backend changes)
- `data/` — production databases (`stocktool.db`, `user_data.db`) and Parquet master files
- `run/` — `.bat`/`.sh` launchers for daily updates and servers
- `tools/` — DB maintenance / utility scripts
- `tmp/` — scratch scripts and diagnostics; **always put throwaway/investigation scripts here, never in `backend/scripts/`**

Read `doc/architecture.md`, `doc/backend_specification.md`, and `doc/frontend_specification.md` before working on backend data model, pipeline, or API changes — they are detailed and authoritative (DB schema, column semantics, indicator formulas, API contracts). `doc/issue_list.md` tracks open backlog items.

## Commands

### Setup
```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
cd frontend && npm install
```

### Run backend API (from project root, port 8000)
```powershell
$env:PYTHONPATH="backend"
.\venv\Scripts\python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 8001
```
(`run/run_server.bat` / `run/KickBackend.bat` do this with the venv auto-activated.)

### Run frontend (port 5173, proxies `/api` to `http://127.0.0.1:8000`)
```powershell
cd frontend
npm run dev
```

### Backend tests (pytest, from project root)
```powershell
$env:PYTHONPATH="backend"; python -m pytest backend/tests/ -v
```
Run a single test file/case: `python -m pytest backend/tests/api/test_watchlist_earnings.py -v` or `::test_name`.
`pytest.ini` already sets `pythonpath = backend` and `testpaths = backend/tests`, so plain `pytest -v` from the root also works.
**All tests must pass before committing.**

### Frontend tests / build
```powershell
cd frontend
npm test          # vitest
npm run build      # tsc -b && vite build
```

### Daily data update
```powershell
run\run_daily_update.bat
```
Or manually: `python backend/scripts/update_pipeline.py` (supports `--category`, `--rebuild-from T2|T3|T4|T5`).

### Backtest engine
```powershell
python backend/backtest/backtest_runner.py                      # all strategies
python backend/backtest/backtest_runner.py --strategy F_elite_momentum97
python backend/backtest/backtest_runner.py --refresh-cache
```

### Optuna parameter optimization
```powershell
run\run_optimization.bat
run\run_optuna_dashboard.bat   # dashboard at :8080
```

## Architecture

### Hybrid hot/cold data model (read this before touching persistence)
The system inverts the usual "DB is master" relationship:
- **Cold master (source of truth)**: Parquet files in `data/parquet_master/`, full history (7+ years), Snappy-compressed, timestamp-generation MVCC (`prices_YYYYMMDD_HHMMSS.parquet` + `latest_master.json` pointer) to dodge Windows file-lock issues.
- **Hot cache (UI/API only)**: `stocktool.db` (SQLite), last 730 days only, tuned for high-frequency OLTP random access. Old rows are purged by the pipeline.
- **Backtest engine reads Parquet directly** and never touches SQLite — this guarantees zero lock contention between batch/backtest and the live API.
- `user_data.db` is separate and holds only user-owned state (watchlist, portfolios, transactions) — never purged/rebuilt from Parquet.
- Schema (columns) is identical between Parquet and SQLite for any given table.

### Pipeline phases (T1–T5), `backend/pipeline/`
Each phase catches up independently by comparing max dates between source and target:
1. **T1** `symbols` — Google Sheets sync (`backend/data_collection/`)
2. **T2** `daily_prices` — yfinance, SPY-driven batch fetch; virtual/theme indices are synthesized from constituents
3. **T3** `indicators` — per-symbol technical/RS indicators (`backend/indicators/`), SPY computed first (other symbols' RS depends on it)
4. **T4** `relative_ranks` — cross-sectional percentile ranks per group, delete-insert per date
5. **T5** `market_signals` — market phase / Market Trend Score / distribution days, computed from T3/T4

`update_pipeline.py --rebuild-from T3` cascades to T4/T5 automatically to preserve consistency. Phase modules live in `backend/pipeline/phases/`.

### Backend layout
- `backend/api/` — FastAPI: `server.py` (app + DB init + CORS), routers split by responsibility: `screener_router.py` (+`screener_cross_section.py` adapter), `chart_router.py`, `dashboard_router.py` (+`panel_builders.py`), `watchlist_router.py`, `portfolio_router.py`/`portfolio_service.py`/`portfolio_logic.py` (router → service → pure-function layering), `backtest_router.py`, `routers.py` (core: /ping, /system/info, /symbols), `deps.py`, `schemas.py`. Screener special filters live in `indicators/screener_filters.py` — the SAME functions the backtest uses; never re-implement them in SQL.
- `backend/db/` — SQLAlchemy models/engine: `database.py`+`models.py` for `stocktool.db`, `database_user.py`+`models_user.py` for `user_data.db`
- `backend/indicators/` — pure calculation modules (moving averages, volatility, relative strength, volume/trend, market signals, screener filters) — split by responsibility
- `backend/pipeline/` — orchestrator + phases + `parquet_cache_manager.py` (MVCC read/write of the Parquet master)
- `backend/backtest/` — CLI backtest/scenario engine (`backtest_runner.py`, `backtest_screener.py`, `backtest_simulator.py`, `scenario_*.py` for multi-regime/Monte Carlo scenario testing), driven by `backtest_config.toml`
- `backend/scripts/` — batch entry points and one-off migration scripts

### Frontend layout (`frontend/src/`)
- `pages/` — one file per route (Dashboard, Chart, Group, Screener + ScreenerResult, Watchlist, TotalPortfolio, PortfolioDetail, Backtest + RegimeComparison)
- `components/` — shared UI (Sparkline, EtfFeaturePanel, SummaryTable, RrgChart, RsLineChart, ChartWidget, WatchlistButton, etc.)
- `api/` — HTTP client calls to the backend
- `hooks/` — data hooks (e.g. `useWatchlist`)
- Dev server proxies `/api` to `http://127.0.0.1:8000` (see `vite.config.ts`); backend must be started on port 8000 (or update the proxy) for the frontend to work locally.

### Sandbox workflow for schema/pipeline changes
Never test schema or indicator-logic changes against production data directly:
1. Copy production Parquet to `data/parquet_master_sandbox/`
2. Point at sandbox via env vars: `$env:STOCKTOOL_DB_PATH="data/stocktool_sandbox.db"` **AND** `$env:STOCKTOOL_USER_DB_PATH="data/user_data_sandbox.db"` — setting only the former lets the API's `heal_*_ids()` destructively NULL out symbol_ids in the production `user_data.db` (see `.claude/skills/sandbox-workflow/SKILL.md`)
3. Verify visually — the frontend shows an orange warning badge when connected to a non-production DB
4. Promote: swap `parquet_master_sandbox/` → `data/parquet_master/`, then clear+restore the SQLite hot cache from the new Parquet master (`restore_sqlite_cache_from_parquet`)

Check `/api/system/info` (`is_production` flag) to confirm which environment you're pointed at.

## Coding conventions specific to this repo

- **File encoding/line endings**: UTF-8 without BOM, LF line endings for `.py/.md/.toml/.json/.tsx` etc. (`.bat` files are CRLF, `.sh` are LF — see `.gitattributes`). Windows Notepad/PowerShell redirection (`>`) can silently corrupt this — be careful when writing files.
- **SQLite (WAL mode)**: every connection must set `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout>=5000`, `PRAGMA synchronous=NORMAL`; writes should use `BEGIN IMMEDIATE` to avoid upgrade deadlocks. Don't change `journal_mode` at runtime while other connections are open (causes `database is locked`). Inside a write session NEVER: `pd.read_sql(q, db.bind)` (self-deadlock — use `get_read_engine_for(db)` after `db.commit()`), `PRAGMA synchronous`, or `VACUUM` (both fail in-transaction). Full details: `.claude/skills/sqlite-wal-handling/SKILL.md`.
- **SQL/ORM**: no `SELECT *`, avoid N+1 (use `joinedload`/`selectinload`), always parameterize queries, never run unscoped `DELETE`/`UPDATE`. Details: `.claude/skills/sql-best-practices/SKILL.md`.
- **Tests mirror source 1:1** under `backend/tests/` with a `test_` prefix (e.g. `backend/indicators/moving_averages.py` → `backend/tests/indicators/test_moving_averages.py`).
- TDD is the intended workflow for new backend features (see `.claude/skills/tdd/SKILL.md`): write one failing test, minimal code to pass, refactor, repeat — not "write all tests then all code."
- Data-integrity expectations (enforced by `tools/db_health_check.py`): every active symbol's T2 data must be at least as recent as SPY's; T2 and T3 row counts per symbol must match exactly; benchmark symbols (SPY etc.) are always included in RS calculations even when `--category` filters are used.

## Project skills (`.claude/skills/`)

Project-specific skills live in `.claude/skills/` (originals in `.agents/skills/` are kept for other AI tools). Invoke the relevant one before working on its domain:

- **sandbox-workflow** — mandatory before any DB schema / indicator / pipeline-logic change (sandbox isolation → verify → promote to production)
- **pipeline-debugging** — diagnosing/repairing T1–T5 data inconsistencies (missing ranks, T2/T3 mismatch, stale data)
- **parquet-data-quality** — reading/writing/merging Parquet master files, dtype-corruption checks, OOM-safe loading
- **sqlite-wal-handling** — WAL mode, lock/deadlock avoidance
- **sql-best-practices** — query performance, injection safety, N+1 avoidance
- **tdd** — red-green-refactor workflow for new backend features
- **backtest-statistical-analysis**, **frontend-uiux-refinement**, **modern-css** — domain-specific guidance for backtest evaluation and frontend work
