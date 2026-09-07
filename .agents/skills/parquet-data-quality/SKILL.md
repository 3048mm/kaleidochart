---
name: parquet-data-quality
description: Data quality checks and high-performance patterns for the Parquet master / pandas layer. Use when reading, writing, merging, or validating Parquet master files (data/parquet_master/), diagnosing dtype corruption, or optimizing large DataFrame loads to avoid OOM.
---

# Parquet データ品質・高速化ガイド (parquet-data-quality)

本プロジェクトの真のマスターデータは `data/parquet_master/` の Parquet ファイル群である（SQLite は直近2年のキャッシュに過ぎない）。
Parquet を読み書き・マージ・検証するコードを書く際は、以下のルールを最優先で適用すること。
実装の実体は `backend/pipeline/parquet_cache_manager.py` にあり、変更時はこのファイルのパターンに合わせる。

---

## 1. MVCC 世代管理の絶対ルール

Windows のファイル共有ロック（WinError 32/5）を回避するため、Parquet マスターは**上書き禁止**。

1. **常に新しいタイムスタンプ付きファイルとして書く**: `prices_YYYYMMDD_HHMMSS.parquet` 形式。既存ファイルへの `to_parquet` 上書きは絶対に行わない。
2. **ポインタ経由で読む**: 最新版のファイルパスは `latest_master.json` から解決する（`get_latest_master_files()`）。ファイル名をハードコードしたり `glob` の最新を直接掴んだりしない。
3. **ポインタ更新はアトミック**: 一時ファイルに書いてから `os.replace()`。競合時は指数バックオフでリトライ（`update_pointer_with_retry()`）。
4. **旧世代の削除は非同期・ベストエフォート**: 他プロセスが掴んでいたらスキップして次回に委ねる（`clean_old_parquet_versions()`、直近2世代保持）。

## 2. 読み込みの高速化・OOM 回避（必須パターン）

数百万行規模のデータを扱うため、以下を徹底する:

- **カラム射影**: 必要なカラムだけ読む。
  ```python
  df = pd.read_parquet(path, columns=['symbol_id', 'date', 'close'])
  ```
- **PyArrow 日付フィルタ**: 期間が決まっているならディスク読み込みの段階で絞る（1.8GB → 740MB / 約1秒でロード可能）。
  ```python
  df = pd.read_parquet(path, filters=[('date', '>=', cutoff_str)])
  ```
- **最新日付だけ知りたい場合**: `columns=['date']` で date 列のみロードして `max()`（全カラムロード禁止）。
- **SQLite からの大量読み出し**: `pd.read_sql(..., chunksize=50_000〜100_000)` でチャンク分割。一括ロードはメモリがデータサイズの数十倍に膨れる。
- **ループ内での全走査禁止**: `unique()` / `max()` 等の DataFrame 全走査は日付ループの外で事前計算する（O(N²) 化を防ぐ）。

## 3. 型汚染の防止（マージ時の最重要ポイント）

Parquet 同士・Parquet と SQL のマージ（`concat` + `drop_duplicates`）では型不整合が頻発する。`parquet_cache_manager.py` の `process_and_merge_table()` に準拠:

- **`date` カラム**: マージ前に両側とも `str` に揃える（Timestamp と `datetime.date` の混在は比較不全・シグナル誤検知の原因）。
- **ID カラム (`symbol_id`, `theme_id`)**: `pd.to_numeric(..., errors='coerce').astype('Int64')`（nullable Int64）に統一。**文字列型 (object) への汚染は重大バグ** — `tools/db_health_check.py` の `check_parquet_health()` が検出する。
- **重複排除**: テーブルごとの自然キーで `drop_duplicates(subset=key_columns, keep='last')`（新しいデータを優先）。
  - `daily_prices` / `indicators` / `relative_ranks`: `["symbol_id", "date"]`
  - `symbols`: `["ticker", "exchange"]`
  - `theme_constituents`: `["theme_id", "symbol_id"]`
  - `market_signals`: `["date"]`
- **バックテスト側でのロード直後**: `pd.to_datetime(df['date']).dt.date` で明示変換してから比較に使う。

## 4. データ品質の検証手順

Parquet やパイプラインに変更を加えたら、必ず以下で検証する:

```powershell
# Parquet マスター全体の健全性（行数・日付範囲・ID型汚染チェック）
python tools/db_health_check.py --parquet

# 特定銘柄の T2/T3 整合性 + 重要カラムの NULL チェック
python tools/db_health_check.py --ticker AAPL --check-nulls

# 全アクティブ銘柄のスキャン
python tools/db_health_check.py --all --check-nulls
```

**合格基準（データ完全性ポリシー）**:
1. 全アクティブ銘柄の T2 最新日 >= SPY の最新日
2. 銘柄ごとに T2 行数 == T3 行数（完全一致）
3. ID カラムが数値型であること（object 型は NG）
4. 重要カラム（`sma_200`, `ema_21`, `rs_value`, `rs_ratio_e21`, `rs_momentum_e21`）の直近5営業日に NULL がないこと
   - 例外: SPY 自身の RS 系カラム、データ期間が短い新規銘柄のウォームアップ期間（ema_21 は 21日未満、rs_ratio_e21 は 30日未満、rs_momentum_e21 は 75日未満）

## 5. SQLite への大量書き込み（リストア/修復時）

`restore_sqlite_cache_from_parquet()` のパターンに準拠:

- ネイティブ `sqlite3` の `executemany` を使う（SQLAlchemy ORM の行単位 INSERT は数十倍遅い）。
- バルク中のみ `PRAGMA synchronous = OFF` / `journal_mode = MEMORY`、**完了後に必ず WAL / NORMAL へ戻す**（API サーバー稼働中に戻し忘れると全体がフリーズする）。
- `NaN` は `df.where(pd.notnull(df), None)` で None（SQL NULL）に変換してから挿入。
- 自動採番の `id` カラムは `symbols` 以外では drop する（`symbols.id` は他テーブルの FK なので保持必須）。
- DataFrame のカラムをモデル定義の有効カラムでフィルタしてから挿入（スキーマ差分による失敗防止）。

---

## 6. 修正はどこまで届くか（データを直す前に必ず確認する）

「SQLite を直せば Parquet も直る」は**半分しか正しくない**。テーブルによって伝播の仕方が違う。

| テーブル | アーカイブ時の扱い | 修正の届き方 |
| :--- | :--- | :--- |
| `symbols` / `theme_constituents` / `fx_rates` | **置換**（SQLite が全期間を持つ） | SQLite を直せば**必ず届く** |
| `daily_prices` / `indicators` / `relative_ranks` / `market_signals` | **マージ**（`drop_duplicates(keep='last')` で SQLite 側が勝つ） | **SQLite が持つ範囲（直近730日）だけ届く** |

`rotate_and_archive_to_parquet` の `process_and_merge_table` が
`pd.concat([df_old, df_sql])` → `keep='last'` なので、**同じ `(symbol_id, date)` は SQLite が上書きする**。

### 730日より古い行を直したいとき

**SQLite 経由では届かない。** 実測（2026-08-05）で Parquet の **73.8%（4,459,171行）**がこの範囲。

選択肢は2つしかない。

1. **Parquet を直接操作して新世代を書く**（`backend/scripts/restore_truncated_symbol_history.py` が実装例）
2. 全期間再構築 — **推奨しない。** 上流から取り直す操作なので、上流が返せなくなった銘柄の履歴を失う
   （2026-08-02 に17銘柄・8年分を失った。`backend/scripts/archive_parquet_master.py` 参照）

### 行の削除は伝播しない

マージなので **Parquet から行が消えることはない**。退役銘柄・旧ティッカーの価格は残り続ける
（2026-08-05 時点で15銘柄・26,649行 = 0.4%）。バックテストは `active == 1` で除外するため
正しさの問題は起きないが、**Parquet は単調増加する**。

## 7. 世代を跨いで復元するときの落とし穴

旧世代の Parquet から履歴を継ぐ場合（`restore_truncated_symbol_history.py` の実作業で踏んだもの）:

- **世代間で `symbols.id` は一致しない。** 2026-08-02 の復元では対象17件が**全件別 id** だった。
  **必ず ticker で突合して `symbol_id` を振り直す。** 旧 id のまま入れると別銘柄の系列を破壊する。
- **旧世代の T3 / T4 を流用してはいけない。** 指標列は 50 → 63、順位列は 17 → 26 に増えている。
  流用すると新しい列（`avg_dollar_volume_21` / `rs_macd_*` 等）が欠損したままスクリーナーへ入る。
  **T3 は再計算する。T4 は横断的なので `--rebuild-from T4` で作り直す。**
- **`date` 列の型は世代で揺れる**（str / `datetime.date` / `Timestamp`）。
  突合とマージの前に必ず文字列へ正規化する。型が違うと重複排除がすり抜ける。
- **旧世代を prune しない。** health check 合格まで MVCC 旧世代がバックアップを兼ねる
  （`agent_execution_rules.md` §10.1）。

## 8. 本番 SQLite を作り直す前のチェック

`run_production_restore.py` は **`stocktool.db` をファイルごと削除**して Parquet から復元する。
**Parquet に無いデータは消える。**

2026-08-03 に事故一歩手前だった実例:

```
Parquet fx_rates    24行（2026-07-01〜08-01）
SQLite  fx_rates 7,715行（1996-10-30〜2026-07-31）  ← バックフィル直後でアーカイブ前だった
```

そのまま実行していれば **7,715行の為替履歴を再び失っていた**（同じ事故が 2026-07-30 に起きている）。

**削除前に「SQLite の方が新しい/多いテーブル」が無いか必ず確認する:**

```python
# 置換対象テーブル（symbols / theme_constituents / fx_rates）は特に注意
for name, key in [("symbols","symbols"), ("theme_constituents","tc"), ("fx_rates","fx")]:
    p = len(pd.read_parquet(latest[key]))
    s = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    assert p >= s, f"{name}: Parquet {p} < SQLite {s} — 先にアーカイブすること"
```


## 9. 全期間再構築（`refresh_All.bat`）の手順書

**2026-08-06 の実行で、想定していなかった問題が5つ出た。** 次回は必ずこの順で行う。
再構築は「Yahoo から取り直す」ので、**手元でしか持っていないものは全部失う**と考える。

### 実行前

```powershell
# 1. スケジュールタスクの次回実行を確認する（並走すると世代が壊れる。後述）
Get-ScheduledTask -TaskName "StockTool_DailyUpdate" | Get-ScheduledTaskInfo | Select NextRunTime

# 2. API サーバ（uvicorn）を止める。読み取りロックで DROP TABLE が無限に待つ
#    2026-08-06 は "Forcing DROP ALL tables" から5分以上復帰せず、CPU 8秒で停止していた

# 3. 退避（削除ではなく移動）。**これが唯一の生命線**
.\venv\Scripts\python.exe backend\scripts\archive_parquet_master.py

# 4. user_data.db をバックアップ（後述の id 振り直しに備える）
```

### 実行

```powershell
$env:CURL_CA_BUNDLE = "C:\ProgramData\Norton\Antivirus\wscert.pem"   # 傍受環境のみ。§upstream-data-diagnosis 2.1
$env:STOCKTOOL_DB_PATH = "data/stocktool_restoring.db"
.\venv\Scripts\python.exe backend\scripts\update_pipeline.py --re-calculate    # 約4時間20分
```

### 実行後（**全部やる。1つでも飛ばすとデータが欠ける**）

| # | やること | なぜ |
| :--- | :--- | :--- |
| 1 | `restore_truncated_symbol_history.py --backup-dir <退避先>` | 上流が返さなかった銘柄の履歴が消える。2026-08-06 は `BDRY` `SXC`（active・約2,100行）が丸ごと、`TMHC` 2086→2、`RSHO` 779→1、`TOI` 1549→1171、`CORZZ` 78→2 |
| 2 | `restore_fx_from_generation.py --from <退避先>` | **`fx_rates` が直近30日だけになる**（7,717→23行）。§8 と同じ事故の3度目 |
| 3 | `recompute_parquet_ranks.py --apply` | 1 で履歴が増えると横断的な T4 が変わる。**`--rebuild-from T4` は使わない**（730日窓しか埋まらない） |
| 4 | `truncate_symbol_history.py` を再適用 | 逆さ合併の切り詰めは再取得で戻る（`JBIO` 318→1280行） |
| 5 | **`reapply_corrections.py --apply`**（または `run/tool/reapply_corrections.bat`） | **手で当てた価格補正が全部巻き戻る。** 未調整の分割（`BYND` 1:30 / `MNST` 2:1）と `ffill` の捏造行（`AVB`）が該当。台帳は `data/price_corrections.toml` |
| 6 | `run_production_restore.py` | SQLite を Parquet から作り直す |
| 7 | `remap_user_data_symbol_ids.py --apply` | **`symbols.id` が再採番される**（後述） |
| 8 | `scan_price_anomalies.py` / `tools/db_health_check.py` | 検収 |

> [!NOTE]
> **4 と 5 は同じ性質の作業**（人が判断して当てた修正の再生）。5 は台帳を持つので
> **`--dry-run` で「何が再適用されるか」を先に見せる**。既に正しいものは接合部の検算で
> 弾かれて「適用不要」と報告されるため、**全件を通しで走らせてよい**。
> 台帳は推定値を作らない — 人が書いた比率をそのまま再生するだけ
> （自動で補正値を割り出す案は 2026-08-06 に見送っている。
>  `doc/completed/split_anomaly_noise_reduction_plan.md` §8）。

### 落とし穴1: `symbols.id` が再採番される

再構築はサンドボックスの**空 DB** から始まるため、T1 の「既存 id を温存する upsert」が
働く相手が居ない。退役済み銘柄は新 DB に作られないので、その分だけ後続の id が前へ詰まる。

```
2026-08-06: 共通3,220ティッカーのうち 2,378件で id が変化
  CAT 845→844 / CATY 846→845 / CAVA 847→846 ...
```

`user_data.db`（ウォッチリスト・ポートフォリオ）は `symbol_id` で参照するため
**別銘柄を指したままになる**。`symbol_id` を持つテーブルは `ticker` も持っているので
`remap_user_data_symbol_ids.py` で振り直せる。

同じ理由で、**Parquet と SQLite の id が食い違う期間ができる**。この間に
`truncate_symbol_history.py` を走らせると Parquet 側の id で SQLite を DELETE し、
**別銘柄の履歴を消す**（実際に961行を誤削除した。現在は ticker 照合で止まる）。

### 落とし穴2: 非 active 銘柄の履歴は消える

再構築後の T1 は active のみを作るため、退役・改称済みの旧ティッカーは
`symbols` ごと消える（2026-08-06 は18件）。バックテストは `active == 1` で絞るので
実害は無いが、**旧ティッカーの履歴は二度と戻らない**。§6「行の削除は伝播しない」で
単調増加していた分が、ここで一括して消える。

### 落とし穴3: スケジュールタスクとの並走で世代が壊れる

**最悪の事故がこれ。** 2026-08-06 に発生:

```
13:00:14  スケジューラの日次更新が開始（気づいていなかった）
13:03〜   手動で履歴復元 → fx 復元 → T4 再計算（新 id 体系の世代を作る）
13:10:02  日次更新が完了し、**旧 id 体系の SQLite をマージした世代**で
          latest_master.json を奪う
```

結果、**symbols は旧 id・prices は新 id** の世代が本番ポインタになり、
`CAT` の終値が 871.08 → 64.13 になるなど全銘柄がずれた。

`latest_master.json` の差し替え自体はアトミックだが、**「読んで・作って・差し替える」の
一連が排他されていない**ため後勝ちで壊れる。現在は Parquet を書き換える全スクリプトが
`pipeline/pipeline_lock.py` で `update_pipeline.lock` を取る（取れなければ即中断）。

**検知方法**: 世代ごとに symbols 件数と ticker→id を突き合わせる。

```python
# 現行世代の ticker→id が退避世代と「完全一致」なら、それは再構築後の世代ではない
common = m_cur.index.intersection(m_old.index)
print(int((m_cur[common] == m_old[common]).sum()), "/", len(common))
```

### 検収: 終値をティッカーで突き合わせる

`symbol_id` は当てにならないので**ティッカーで突合**し、直近終値の比を見る。
別銘柄に紐づいていれば大量に外れる。

```
2026-08-06 の合格例: 共通3,220ティッカーのうち 3,194件が比 0.8〜1.25 に収まった
  外れた26件 = 決算日の値動き（±20〜70%）と、基準が変わる仮想テーマ指数（`_XXX_`）
```
