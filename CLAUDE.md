# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 言語設定
- 常に日本語で会話する
- コメントも日本語で記述する
- エラーメッセージの説明も日本語で行う
- ドキュメントも日本語で生成する

## ツール呼び出しの書式（AIエージェント向け・厳守）
- ツール呼び出しは名前空間接頭辞付きの正規の書式で出力すること。接頭辞を欠いた壊れた書式は malformed として送信が弾かれ、ツールが実行されない。
- malformed で弾かれたら、同じ書式のまま闇雲に再送しない。直近で成功したツール呼び出しの書式に合わせ、1呼び出しずつ確実に送り直す。

## エラーリトライ規律（AIエージェント向け・厳守）
- **同一エラーで3回失敗したら打ち切る**（`doc/agent_execution_rules.md` §4）。打ち切り後はアプローチを変えるか、エラー内容を報告してユーザーの判断を仰ぐ。同じ呼び出しをそのまま再送することを禁止する。
- `File has not been read yet` / `File has been modified since read` で Edit が拒否されたら、**対象ファイルを Read し直してから** Edit する。長いセッションやコンテキスト要約（compaction）後は Read 状態が失われているため、記憶を頼りに Edit しない。
- `sleep` によるポーリング待機を禁止する（この環境ではブロックされる）。長時間コマンドは `run_in_background` で実行し、完了通知を待つ。
- Python 実行は常に**リポジトリ本体の venv** を使う。本体では `.\venv\Scripts\python.exe`、**ワークツリー内には venv が存在しない**ため `..\..\..\venv\Scripts\python.exe` または絶対パスで本体の venv を参照する。素の `python` は venv 外の Python を拾い、`ModuleNotFoundError`（pytest 等が見つからない）の原因になる。
- `No module named 'backend'` が出たら PYTHONPATH と import 形式の不一致を疑う（import 規約は「Coding conventions」参照）。
- その他の共通ルール（`python -c` の制限、文字コード、パス、Git 合意形成）は `doc/agent_execution_rules.md` §1〜§8 を参照する。

## サブエージェント委譲（オーケストレーター運用）

役割分担: 方針立て・アーキテクチャ判断・実装計画書の作成とユーザーレビュー・成果物の検収は**メインセッション（オーケストレーター）**が行い、確定した計画の機械的な作業は低コストモデルのワーカーに委譲する。

- **implementer**（`.claude/agents/implementer.md`）— 計画確定後のコード実装
- **test-writer**（`.claude/agents/test-writer.md`）— テスト実装（TDD red フェーズ、テスト追加）

委譲時のルール:
- ワーカーは会話コンテキストを引き継がない。**計画書パス（`doc/in_progress/<name>_plan.md`）と対象チェックリスト項目を明示した自己完結プロンプト**を渡す。
- 委譲単位は計画書のチェックリスト1項目程度（1機能の実装＋テスト）。関数1個のような細かすぎる委譲はコンテキスト再構築コストで逆に割高。
- ワーカー完了後、オーケストレーターが **diff の確認とテスト全体実行を検収として必ず行う**。検収なしで次の項目に進まない。
- 複数ワーカーを並列に走らせる場合は `isolation: "worktree"` で作業コピーの衝突を防ぐ。
- 設計判断が必要になった・計画に穴があった、という報告がワーカーから来たら、オーケストレーターが判断して計画書を更新してから再委譲する（ワーカーに判断させない）。

## Git 運用（AIエージェント向け）

- 本体チェックアウトでのコミットは禁止（`git add` まで）。**ワークツリー（`.claude/worktrees/` 配下）では自ブランチへのコミットを許可** — 完了報告にブランチ名・SHA・取り込みコマンドを明記する。
- main への直接コミット・push・マージ、`--amend`・force-push は場所を問わず禁止。push・PR 作成は都度ユーザー指示。
- ワークツリーで作成した `doc/in_progress/` の計画書は未コミットで置き残さない（その場でコミット）。
- `git add` は**明示パスのみ**（`-A` / `.` 禁止）。
- **データ（DB / Parquet）は git に乗らない** — アクセス方法・変更種別（A〜D、完了報告に必須記載）・merge 後の昇格は `doc/agent_execution_rules.md` §10 を参照。
- 機械判定可能な違反（`git add -A`/`.`、force-push、main 宛 push、`--amend`、本体での commit）は PreToolUse フック `tools/hooks/git_guard.ps1` が自動でブロック/確認する。
- 詳細: `doc/agent_execution_rules.md` §7。未取り込み作業の棚卸し: `tools/check_worktrees.ps1`

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

**Development-item workflow**: when starting a development item, copy `doc/in_progress/_TEMPLATE.md` to `doc/in_progress/<name>_plan.md`, fill it in, and get the plan reviewed by the user before implementing. Keep the checklist / notes / issues sections updated while working (the plan doubles as a handoff document for other sessions/agents), and move the file to `doc/completed/` when done. See `doc/agent_execution_rules.md` §9.

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
.\venv\Scripts\python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 8000
```
(`run/run_server.bat` / `run/KickBackend.bat` do this with the venv auto-activated.)

### Run frontend (port 5173, proxies `/api` to `http://127.0.0.1:8000`)
```powershell
cd frontend
npm run dev
```

### Backend tests (pytest, from project root)
```powershell
$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/ -v
```
Run a single test file/case: `.\venv\Scripts\python.exe -m pytest backend/tests/api/test_watchlist_earnings.py -v` or `::test_name`.
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
- `universe.db` is the **symbol-definition editing master** (`symbols_master` / `theme_members` / `ticker_history`) — T1 syncs from it into `stocktool.db`. Treat it like `user_data.db`: back up + in-place migration only, never swap/rebuild (it holds manual edits and rename history). **The T1 sync upserts on the `(ticker, exchange)` natural key and must preserve `symbols.id`** — ~1.57M rows × 3 tables plus all of Parquet reference it as an integer FK. Renames/delistings are detected weekly against SEC EDGAR via the stable `cik`/`sec_class_id` keys. **Full spec: `doc/universe_db_specification.md`.**
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
- `backend/backtest/` — CLI backtest/scenario engine (`backtest_runner.py`, `backtest_screener.py`, `backtest_simulator.py`, `scenario_*.py` for multi-regime/Monte Carlo scenario testing), driven by `backtest_config.toml`. テストは役割の異なる3種類（型1: スクリーン条件最適化＝無限資金・コストなし・レジーム非依存が**意図** / 型2: ETFシナリオ / 型3: 個別銘柄シナリオ＝有限資産・コストあり・MTS連動）— 片方の設計判断をもう片方の基準で評価しないこと。詳細: `doc/backend_specification.md` §6.1
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
2. Point at sandbox via env vars: `$env:STOCKTOOL_ENV="sandbox"` — this automatically points all databases (system and user) to isolated locations under `data/sandbox/` and prevents setting-mismatch errors like the API's `heal_*_ids()` destructively NULLing out production `user_data.db` (see `.claude/skills/sandbox-workflow/SKILL.md`).
3. Verify visually — the frontend shows an orange warning badge when connected to a non-production DB
4. Promote: swap `parquet_master_sandbox/` → `data/parquet_master/`, then clear+restore the SQLite hot cache from the new Parquet master (`restore_sqlite_cache_from_parquet`)

Check `/api/system/info` (`is_production` flag) to confirm which environment you're pointed at.

## Coding conventions specific to this repo

- **File encoding/line endings**: UTF-8 without BOM, LF line endings for `.py/.md/.toml/.json/.tsx` etc. (`.bat` files are CRLF, `.sh` are LF — see `.gitattributes`). Windows Notepad/PowerShell redirection (`>`) can silently corrupt this — be careful when writing files.
- **SQLite (WAL mode)**: every connection must set `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout>=5000`, `PRAGMA synchronous=NORMAL`; writes should use `BEGIN IMMEDIATE` to avoid upgrade deadlocks. Don't change `journal_mode` at runtime while other connections are open (causes `database is locked`). Inside a write session NEVER: `pd.read_sql(q, db.bind)` (self-deadlock — use `get_read_engine_for(db)` after `db.commit()`), `PRAGMA synchronous`, or `VACUUM` (both fail in-transaction). Full details: `.claude/skills/sqlite-wal-handling/SKILL.md`.
- **SQL/ORM**: no `SELECT *`, avoid N+1 (use `joinedload`/`selectinload`), always parameterize queries, never run unscoped `DELETE`/`UPDATE`. Details: `.claude/skills/sql-best-practices/SKILL.md`.
- **import 規約**: `PYTHONPATH=backend` 前提の `api.x` / `pipeline.x` / `indicators.x` 形式が基本。`backend.x` プレフィックス形式は `scenario_*` 系など一部のみ（プロジェクトルートから直接実行する前提）。両形式が混在しているため、**編集対象ファイルの既存 import 形式に必ず合わせる**。
- **Tests mirror source 1:1** under `backend/tests/` with a `test_` prefix (e.g. `backend/indicators/screener_filters.py` → `backend/tests/indicators/test_screener_filters.py`). **Only tests belong under `backend/tests/`** — pytest imports every module at collection time, so a throwaway script that does I/O at import will hang the whole suite (`backend/tests/api/test_api.py` blocked it for 45+ minutes until removed on 2026-08-06). Put scratch scripts in `tmp/`.
- TDD is the intended workflow for new backend features (see `.claude/skills/tdd/SKILL.md`): write one failing test, minimal code to pass, refactor, repeat — not "write all tests then all code."
- Data-integrity expectations (enforced by `tools/db_health_check.py`): every active symbol's T2 data must be at least as recent as SPY's; T2 and T3 row counts per symbol must match exactly; benchmark symbols (SPY etc.) are always included in RS calculations even when `--category` filters are used.

## Project skills (`.claude/skills/`)

Project-specific skills live in `.claude/skills/` (originals in `.agents/skills/` are kept for other AI tools). Invoke the relevant one before working on its domain:

- **sandbox-workflow** — mandatory before any DB schema / indicator / pipeline-logic change (sandbox isolation → verify → promote to production)
- **pipeline-debugging** — diagnosing/repairing T1–T5 data inconsistencies (missing ranks, T2/T3 mismatch, stale data) — 手元側の問題
- **upstream-data-diagnosis** — 上流(yfinance/Yahoo)起因のデータ異常の切り分けと既知の限界カタログ。履歴が短い・上場廃止候補に出た・価格に段差がある・退役の可否を判断する・完全再構築を実行する前に必ず参照（誤診断で本番データを削除した前例あり）
- **parquet-data-quality** — reading/writing/merging Parquet master files, dtype-corruption checks, OOM-safe loading
- **sqlite-wal-handling** — WAL mode, lock/deadlock avoidance
- **sql-best-practices** — query performance, injection safety, N+1 avoidance
- **tdd** — red-green-refactor workflow for new backend features
- **backtest-statistical-analysis**, **frontend-uiux-refinement**, **modern-css** — domain-specific guidance for backtest evaluation and frontend work
