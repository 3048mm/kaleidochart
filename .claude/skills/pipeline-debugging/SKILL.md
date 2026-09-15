---
name: pipeline-debugging
description: Diagnosis and repair procedures for T1-T5 data pipeline inconsistencies - missing indicators, rank gaps, T2/T3 row count mismatches, stale symbols, or empty virtual themes. Use when data looks wrong in the dashboard/screener, a pipeline run failed midway, or db_health_check reports NG.
---

# パイプライン診断・修復ガイド (pipeline-debugging)

ダッシュボードやスクリーナーの表示がおかしい・パイプラインが途中で失敗した・データが欠けている、といった場合の診断と修復の手順書。
パイプラインは T1→T2→T3→T4→T5 の依存チェーンであり、**上流の欠損は必ず下流に伝播する**。診断は常に上流から行う。

---

## 1. まず診断（修復の前に必ず実行）

```powershell
# Parquet マスターと SQLite の健全性を一括チェック
python tools/db_health_check.py --all --check-nulls

# 特定銘柄だけ調べる場合
python tools/db_health_check.py --ticker NVDA --check-nulls
```

診断用の使い捨てスクリプトを書く場合は**必ず `tmp/` に置く**（`backend/scripts/` を汚染しない。用済み後は削除）。

## 2. 症状 → 原因 → 修復の対応表

| 症状 | 原因の階層 | 修復方法 |
| :--- | :--- | :--- |
| T2 最新日が SPY より古い（Synced: No） | T2: yfinance 取得漏れ | `python backend/scripts/update_pipeline.py`（SPY 主導の差分キャッチアップが自動で走る） |
| T2 行数 ≠ T3 行数（Consistent: No） | T3: 指標計算漏れ | `update_pipeline.py --rebuild-from T3`（T4/T5 も連鎖再計算される。**Parquet 基点で全期間を再計算**） |
| 特定日の relative_ranks が欠損 | T4 | `update_pipeline.py` 再実行（T3.MAX > T4.MAX の不足日を Delete-Insert で補完）。ピンポイント修復の過去例: `backend/scripts/fix_missing_ranks.py` |
| market_signals の NULL・欠損 | T5 | `update_pipeline.py` 再実行（NULL 欠損は過去に遡ってバックフィルされる）。**この修復は落ちない**（遡りガードは例外にしない。次項） |
| T5 の遡りガードが `logger.error` で日付を除外した | T5: SPY の遡りガード | `sync_phase_t5_signals()` は `gap_dates` のうち SPY の遡りが `SPY_LOOKBACK_MIN_BARS`（220本）未満の日付を**例外にせず書き込み対象から除外し、`logger.error` で除外日付数・範囲・対処を警告する**（日次更新やホット期間内の NULL 埋めが毎晩落ちて rotate に到達できなくなる不具合があったため §7-7・5-7c で変更）。除外された日付に正しい値を入れるには `--rebuild-from T5`（Parquet 基点）で再計算する |
| 指標カラム追加後に過去分が NULL | T3 | パイプラインが NULL レコードを自動検知して補完する。効かない場合は `--rebuild-from T3` |
| 仮想テーマ (`_XXX_`) の T2 が 0 件 | T1/T2+: 構成銘柄ゼロ | スプレッドシートの `tags` 紐付けを確認 → T1 同期 → `refresh_constituents.py` |
| 株価自体が誤っている（分割未反映等） | T2 | `update_pipeline.py --rebuild-from T2`（以降の全テーブルを刷新、時間がかかる） |
| ロジック変更後の全面再計算 | 複数 | まず [[sandbox-workflow]] で Sandbox 検証してから本番へ |

## 3. `--rebuild-from` の連鎖規則

`update_pipeline.py --rebuild-from <T2|T3|T4|T5>` は指定階層と**それに依存する下流すべて**を Delete-Insert で再生成する:
- `T2` → T2, T3, T4, T5（yfinance 再取得を含む・最も重い）
- `T3` → T3, T4, T5
- `T4` → T4, T5
- `T5` → T5 のみ

その他の主要オプション: `--category`（対象カテゴリ限定。ただし SPY 等の基準銘柄は常に計算対象に含まれる）、`--skip-fetch` / `--skip-sync` / `--skip-t3`（部分スキップ）、`--re-calculate`（全再取得+全再計算）。

**T3/T4/T5 の再構築は Parquet 基点で全期間を再計算する**（`--rebuild-from T3/T4/T5` は `_rebuild_from_parquet()` に委譲され、SQLite 基点では計算しない）。SQLite はホット期間（約730日）しか持たないため、SQLite 基点で全日付を再計算すると窓の先頭が壊れる（2026-08-29 に T3 で 772,526行、2026-09-04〜09-06 に T5 で MTS の38日を壊した実例がある）。

## 4. 修復時の注意点

- **修復は必ずべき等に**: T4/T5 は対象日を DELETE してから INSERT する方式（重複 IntegrityError の防止）。修復スクリプトを書く場合も同じパターンに従う。
- **SPY を最優先で処理**: 他銘柄の RS 計算は SPY の T3 に依存する。SPY が欠けたまま個別銘柄を再計算しても RS 系カラムが NULL になる。
- **API サーバー稼働中の修復**: WAL モードを維持したまま行う。`journal_mode` の一時変更は `database is locked` の原因（詳細: [[sqlite-wal-handling]]）。大量 DML では `PRAGMA synchronous = OFF; PRAGMA temp_store = MEMORY;` を使い、終了後に戻す。
- **本番での修復前に Sandbox で試す**: データを DELETE する修復は特に、`STOCKTOOL_DB_PATH` で Sandbox に向けて手順を確認してから本番に適用する。
- **SPY の値が変わる変更をしたら、T3 以降をリフレッシュすること**。SPY は全銘柄の RS の基準であり、MTS の SPY 由来列の入力でもある。
- **最終手段（完全再構築）**: SQLite キャッシュ側の不整合がひどい場合は、テーブルをクリアして `restore_sqlite_cache_from_parquet` で Parquet マスターから直近2年分を再構築できる（約3分）。Parquet マスター自体が壊れている場合のみ `--rebuild-from T2` に頼る。

## 5. NULL 判定の例外（誤検知しないために）

以下の NULL は**正常**であり、修復対象ではない:
- SPY 自身の `rs_value`, `rs_ratio_e21`, `rs_momentum_e21`（自分自身との相対強度は計算しない）
- 新規上場等でデータ期間が短い銘柄のウォームアップ期間: `ema_21` は 21日未満、`rs_ratio_e21` は 30日未満、`rs_momentum_e21` は 75日未満
- FX レート銘柄（`=X` 系）は T2/T3 チェックの対象外

---

## 6. 全期間の復元・再計算の実手順（2026-08-03 実測）

Parquet を直接修正した後など、SQLite と T4/T5 を作り直す必要があるときの順序と所要時間。

| # | 手順 | 所要 | 注意 |
| :--- | :--- | ---: | :--- |
| 0 | **SQLite の方が新しいテーブルを Parquet へ退避** | — | **省くと消える。** `parquet-data-quality` §8 参照 |
| 1 | API サーバー・パイプラインを停止 | — | **孤児プロセスの残存を確認**（`sqlite-wal-handling` §6） |
| 2 | `run_production_restore.py` | **906秒** | `stocktool.db` を削除して Parquet から復元 |
| 3 | `update_pipeline.py --rebuild-from T4` | **15分** | T4 は 2,096日を全期間再計算。T5 も連鎖 |
| 4 | Parquet へアーカイブ（3の末尾で自動） | 1,407秒 | 世代が切り替わる。旧世代は prune しない |
| 5 | 730日パージ（3の末尾で自動） | **7,745秒** | 約400万行×3テーブルの DELETE。**2時間かかる** |
| 6 | `wal_checkpoint(TRUNCATE)` → REINDEX → VACUUM | 75s + 50s | **接続ゼロで実行**。6,068MB → 1,627MB（73%削減） |
| 7 | `db_health_check.py --all` で検収 | 数分 | — |

**合計で約3時間。** 大半は手順5（パージ）が占める。CPU 使用率は 0 に見えるが
**I/O バウンドで動いている**ので、止まったと誤認しないこと（I/O カウンタで進捗を確認できる）。

### 復元範囲は730日ではない

`restore_sqlite_cache_from_parquet` は `config.toml` の `default_start_date`（既定 2018-04-01）
から復元する。**復元直後の SQLite は全期間を持つ**ため、
`--rebuild-from T4` は 730日ではなく**全期間**の順位を作り直せる。
その後の日次パージで730日へ戻る。

- `--rebuild-from T4` は `RelativeRank` を全削除するので `t4_max=None` となり、
  `default_start_date` から再計算される（`orchestrator.py` の段階削除）。
- したがって「全期間の順位を作り直したい」なら **復元 → `--rebuild-from T4`** の順に実行する。

### 検収の観点

- **T2/T3 の行数が完全一致すること**（`db_health_check` が見る）
- **T4 の増分が復元行数と一致すること**（2026-08-03 は Ranks が +27,460 = 復元行数と完全一致）
- `fx_rates` の件数と土日行ゼロ

---

## 7. 銘柄を追加・改称すると T4 だけ追随しない（2026-08-05 実測）

**T2/T3 は自動で全期間を埋めるが、T4（順位）は直近数日しか作られない。**
デイリー更新は正常終了するので、**気づかない。**

### 実測（改称6件をデイリー更新で反映した直後）

```
ticker    T2      T3      T4     T2期間
HAPN    2,096   2,096       6    2018-04-02〜2026-08-03
SHOE    2,096   2,096       6    2018-04-02〜2026-08-03
AAPL    2,096   2,096   2,096    ← 対照
```

### 原因: T3 は銘柄駆動、T4 は日付駆動

| フェーズ | 走査単位 | 新規銘柄の扱い |
| :--- | :--- | :--- |
| T2 | 銘柄ごと（`current_max` が無ければ `default_start_date` から） | **全期間を取得する** |
| T3 | 銘柄ごと（`i_max_map` で銘柄別の到達点を見る） | **全期間を計算する** |
| T4 | **日付ごと**（`start_date = t4_max - 7日` の日付集合を回し、各日付で全銘柄を再計算） | **過去日は再計算されない** |

T4 は「日付」を単位に delete-insert するため、**銘柄が増えても過去の日付は処理対象にならない**。
`t4_max` は全銘柄共通の最大値なので、新規銘柄がいくら過去データを持っていても
ウィンドウ（直近7日）は動かない。

### 影響

- スクリーナーの当日判定は動く（最新日の順位はある）
- **バックテストで RS ランク系フィルタを使うと、その銘柄は過去期間で候補に出ない**
  （`min_rs_ratio_rank_e21` などが NULL 扱いになる）

### 対応 — `--rebuild-from T4` 単体では足りない

> [!WARNING]
> **`--rebuild-from T4` だけでは Parquet の全期間は埋まらない**（2026-08-05 実測）。
> T4 は **SQLite の `indicators` に存在する日付しか計算できない**。SQLite は730日分しか
> 持たないため、**直近730日の順位しか作られない**。
>
> ```
> --rebuild-from T4 を5時間かけて実行した結果
>   HAPN  T3=2096  T4=500    ← 730日窓のみ
>   AAPL  T3=2096  T4=2096   ← 対照（過去の全期間再構築で埋まっている）
> ```

**Parquet 上で直接再計算する。約80秒で終わる。**

```powershell
python backend/scripts/recompute_parquet_ranks.py --dry-run   # 差分を確認
python backend/scripts/recompute_parquet_ranks.py --apply
```

SQLite に一切触れないため、API サーバー・パイプラインとのロック競合が起きない。
実装は `backend/pipeline/parquet_recompute.py`。
SQLite の `relative_ranks` と 4日付 × 22列で最大誤差 0.000e+00 を確認済み。

<details><summary>旧手順（SQLite 経由・約3.5時間）</summary>

```powershell
python backend/scripts/run_production_restore.py           # SQLite を全期間復元
python backend/scripts/update_pipeline.py --rebuild-from T4
```

**`--rebuild-from T4` 単体では730日窓しか埋まらない**ため、復元が先に必要だった。
Parquet 直接再計算が使えるなら、この経路を選ぶ理由はない。
</details>

### 再計算の副作用（実行前に理解する）

**現在の `active` フラグで計算する。** Parquet は行削除が伝播しないため、
退役・改称した銘柄の古い順位が残っている。再計算するとそれらは母集団から外れる。

- バックテスト結果への直接影響はない（`backtest_screener.py` が `active == 1` で除外）
- **他銘柄のパーセンタイル値はわずかに変わる**（母集団の変動による）
- したがって**過去の最適化結果は厳密には再現しなくなる**

2026-08-05 の実績: 26,649行が消え（退役銘柄の残骸）、8,307行が増え（改称先の過去分）、
さらに**旧コードが NULL を 1.0 とランク付けしていた1,348行も修正**された
（SQLite の `PERCENT_RANK` は NULL に 0.0 を返す。合成テストと本番データで確認済み）。

### 影響を見積もってから判断する

全期間を埋め直すかは、**バックテスト期間との重なり**で判断する。

- `backtest_config.toml` / `run_scenario_batch.py` の期間（例: 2022-01-01〜）と
  T4 が欠けている範囲が重なるなら、その銘柄は**候補に出ない**
- 2026-08-05 の実例: 改称6件の T4 が 2024-08 以降のみ = バックテスト期間の約62%で欠落
- **改称は既存銘柄のカバレッジを一時的に後退させる**（旧ティッカーは `active=0` で除外され、
  新ティッカーは直近しか順位を持たないため）

### 設計通りで問題ないケース（誤検知しない）

| 事象 | 理由 |
| :--- | :--- |
| `市場` カテゴリで T3=4109 / T4=2096 | T2 は `index_start_date`（2010-04-01）から、T4 は `min_allowed_date`（2018-04-01）から。**仕様** |
| `レバレッジ` / `指標` で T4=0 | `t4_ranks.py` の `NOT IN` で対象外 |

### 検知方法

```sql
-- T3 はあるのに T4 が極端に少ない銘柄を洗い出す
-- 注: GROUP BY の無い HAVING は SQLite で使えないため、サブクエリで包む
SELECT ticker, t3, t4 FROM (
    SELECT s.ticker,
           (SELECT COUNT(*) FROM indicators     i WHERE i.symbol_id = s.id) AS t3,
           (SELECT COUNT(*) FROM relative_ranks r WHERE r.symbol_id = s.id) AS t4
    FROM symbols s
    WHERE s.active = 1
      -- レバレッジ / 指標 は T4 の計算対象外（t4_ranks.py の NOT IN 句）。
      -- 除外しないと恒久的に誤検知し続ける
      AND s.category NOT IN ('レバレッジ', '指標')
)
WHERE t3 > 100 AND t4 * 10 < t3      -- 整数除算を避けるため掛け算で比較する
ORDER BY t3 DESC;
```

T4 が作られるのは `個別` / `テーマ` / `市場` / `セクタ` の4カテゴリのみ
（`レバレッジ` と `指標` は相対比較の意味がないため意図的に除外されている）。

> [!WARNING]
> **`--rebuild-from T4` の実行中にこのクエリを流さないこと。**
> T4 は日付ごとの delete-insert で進むため、進行中は全銘柄が「T4 が極端に少ない」状態に見える。
> 実際 2026-08-05 に、再構築の最中に流して**全 active 銘柄 3,215件が検知される**という
> 誤った結果を得た。`logs/pipeline.log` で `Phase 4 Progress` が出ていないことを先に確認する。

