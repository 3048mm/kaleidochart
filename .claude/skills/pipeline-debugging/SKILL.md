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
| T2 行数 ≠ T3 行数（Consistent: No） | T3: 指標計算漏れ | `update_pipeline.py --rebuild-from T3`（T4/T5 も連鎖再計算される） |
| 特定日の relative_ranks が欠損 | T4 | `update_pipeline.py` 再実行（T3.MAX > T4.MAX の不足日を Delete-Insert で補完）。ピンポイント修復の過去例: `backend/scripts/fix_missing_ranks.py` |
| market_signals の NULL・欠損 | T5 | `update_pipeline.py` 再実行（NULL 欠損は過去に遡ってバックフィルされる） |
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

## 4. 修復時の注意点

- **修復は必ずべき等に**: T4/T5 は対象日を DELETE してから INSERT する方式（重複 IntegrityError の防止）。修復スクリプトを書く場合も同じパターンに従う。
- **SPY を最優先で処理**: 他銘柄の RS 計算は SPY の T3 に依存する。SPY が欠けたまま個別銘柄を再計算しても RS 系カラムが NULL になる。
- **API サーバー稼働中の修復**: WAL モードを維持したまま行う。`journal_mode` の一時変更は `database is locked` の原因（詳細: [[sqlite-wal-handling]]）。大量 DML では `PRAGMA synchronous = OFF; PRAGMA temp_store = MEMORY;` を使い、終了後に戻す。
- **本番での修復前に Sandbox で試す**: データを DELETE する修復は特に、`STOCKTOOL_DB_PATH` で Sandbox に向けて手順を確認してから本番に適用する。
- **最終手段（完全再構築）**: SQLite キャッシュ側の不整合がひどい場合は、テーブルをクリアして `restore_sqlite_cache_from_parquet` で Parquet マスターから直近2年分を再構築できる（約3分）。Parquet マスター自体が壊れている場合のみ `--rebuild-from T2` に頼る。

## 5. NULL 判定の例外（誤検知しないために）

以下の NULL は**正常**であり、修復対象ではない:
- SPY 自身の `rs_value`, `rs_ratio_e21`, `rs_momentum_e21`（自分自身との相対強度は計算しない）
- 新規上場等でデータ期間が短い銘柄のウォームアップ期間: `ema_21` は 21日未満、`rs_ratio_e21` は 30日未満、`rs_momentum_e21` は 75日未満
- FX レート銘柄（`=X` 系）は T2/T3 チェックの対象外
