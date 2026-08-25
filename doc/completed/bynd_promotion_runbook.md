# 本番昇格 手順書（BYND / MNST / AVB / 仮想テーマ170本）

- **前提ブランチ**: `worktree-bynd-split-adjust`（main へマージ済みであること）
- **所要時間の目安**: 30〜45分（T4 の全期間再計算が4回走る）
- **関連**: `doc/in_progress/bynd_split_adjust_plan.md`

> [!CAUTION]
> **日次パイプラインと重ねないこと。** `StockTool_DailyUpdate` は **07:00 と 13:00** の
> 週2トリガー（Tue-Sat）で、T2 だけで約2時間かかる（2026-08-25 は 13:00 開始 → 15:06 完了）。
> 各スクリプトは `pipeline_lock` を取るので同時実行にはならないが、**待たされるか弾かれる**。
> 実施前に必ず次回実行時刻を確認する。
>
> ```powershell
> Get-ScheduledTask -TaskName "StockTool_DailyUpdate" | Get-ScheduledTaskInfo |
>     Select-Object LastRunTime, LastTaskResult, NextRunTime
> ```

## 0. 事前確認

```powershell
# パイプラインが動いていないこと（update_pipeline.py が居ないこと）
Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
    Select-Object ProcessId, CommandLine | Where-Object { $_.CommandLine -like "*update_pipeline*" }

# 現行世代を控える（ロールバック先）
Get-Content data\parquet_master\latest_master.json
```

> [!IMPORTANT]
> **旧世代は prune しない。** 検証に合格するまで MVCC 旧世代がロールバックの唯一の手段
> （`doc/agent_execution_rules.md` §10.1）。各スクリプトは prune しない作りになっている。

API サーバ（uvicorn）は**動かしたままでよい**。読み取りは WAL で競合しない。
ただし昇格直後は表示が古い世代のままになることがあるので、完了後に再起動する。

## 1. 適用（この順で実行する）

順番に意味がある。**構成銘柄の価格を直してから**テーマを再合成しないと、
テーマが古い価格で合成される。

```powershell
$env:PYTHONPATH = "backend"
# STOCKTOOL_DB_PATH / STOCKTOOL_ENV は **設定しないこと**（本番を指す）
Get-ChildItem Env:STOCKTOOL_*        # 何も出ないことを確認

# 1) BYND — 1:30 併合（2026-08-14 ET）の遡及補正
.\venv\Scripts\python.exe backend\scripts\adjust_symbol_split.py `
    --ticker BYND --before 2026-08-13 --factor 30 `
    --reason "1:30 併合 (2026-08-14 ET)" --dry-run
.\venv\Scripts\python.exe backend\scripts\adjust_symbol_split.py `
    --ticker BYND --before 2026-08-13 --factor 30 `
    --reason "1:30 併合 (2026-08-14 ET)" --apply

# 2) MNST — 2:1 分割（2026-08-11）の遡及補正
.\venv\Scripts\python.exe backend\scripts\adjust_symbol_split.py `
    --ticker MNST --before 2026-08-10 --factor 0.5 `
    --reason "2:1 分割 (2026-08-11)" --dry-run
.\venv\Scripts\python.exe backend\scripts\adjust_symbol_split.py `
    --ticker MNST --before 2026-08-10 --factor 0.5 `
    --reason "2:1 分割 (2026-08-11)" --apply

# 3) AVB — ffill で捏造された行の削除
.\venv\Scripts\python.exe backend\scripts\delete_symbol_rows.py `
    --ticker AVB --auto-fabricated --from 2026-08-15 --to 2026-08-23 `
    --reason "ffill による捏造行（上流欠損）" --dry-run
.\venv\Scripts\python.exe backend\scripts\delete_symbol_rows.py `
    --ticker AVB --auto-fabricated --from 2026-08-15 --to 2026-08-23 `
    --reason "ffill による捏造行（上流欠損）" --apply

# 4) 仮想テーマ170本 — 2026-08-07 の再ベースを是正
.\venv\Scripts\python.exe backend\scripts\rebuild_virtual_indexes.py --all --dry-run
.\venv\Scripts\python.exe backend\scripts\rebuild_virtual_indexes.py --all --apply
```

**`--dry-run` で止まったら `--apply` に進まない。** 接合部の検算に落ちるのは
「比率を取り違えている」か「既に補正済みの系列に再適用しようとしている」かのどちらかで、
どちらも書き込んではいけない状態である。

## 2. 検収

```powershell
$env:PYTHONPATH = "backend"
.\venv\Scripts\python.exe tmp\verify_bynd_adjust.py data\parquet_master data\stocktool.db
.\venv\Scripts\python.exe tools\db_health_check.py --all --check-nulls
.\venv\Scripts\python.exe backend\scripts\scan_price_anomalies.py
```

| 確認 | 期待 |
| :--- | :--- |
| BYND 2026-08-12 → 08-13 の比率 | 1.0 近傍（補正前 30.10） |
| MNST 2026-08-07 → 08-10 の比率 | 1.0 近傍（補正前 0.506） |
| AVB の 2026-08-17〜08-21 | 行が存在しない |
| 仮想テーマの 2024-08-06 | 段差なし（補正前は165本が ~1000 にリセット） |
| `db_health_check` | T2/T3 行数一致・NULL なし |
| `scan_price_anomalies` | `split_suspect` に BYND / MNST が出ないこと |

> [!NOTE]
> **AVB は捏造行を消しても段差が残る。** 上流（Yahoo）の chart 系列が実勢価格
> （regularMarketPrice=184.06）と桁の違う $65 帯を返しており、`2.793:1` という
> 実在しない分割記録まで付いている。これは手元では直せない。監視に回す。

## 3. API サーバの再起動

```powershell
Get-Process | Where-Object { $_.ProcessName -eq "python" } |
    Where-Object { $_.CommandLine -like "*uvicorn*" }   # PID を確認して停止
Start-ScheduledTask -TaskName "StockTool_RunServer"
```

`/api/system/info` の `is_production` が `true`、フロントの警告バッジが**出ていない**ことを確認する。

## 4. ロールバック

ポインタを旧世代に戻すだけでよい（世代ファイルは prune していない）。

```powershell
Copy-Item data\parquet_master\data_version_<旧TS>.json data\parquet_master\latest_master.json -Force
```

**ただし SQLite は戻らない。** ホットキャッシュの差し替えは済んでいるので、
ロールバックする場合は `backend\scripts\run_production_restore.py` で
旧世代の Parquet から SQLite を作り直す。

## 5. 昇格後にやること

- [ ] 週次メンテナンスのタスク再登録（`worktree-fix-weekly-task-registration` をマージ後）
      `run\register_weekly_maintenance.bat` を**ファイルとして実行**する（cmd の外から
      中身を貼らない — `%~dp0` が展開されず空コマンドで登録される）
- [ ] 週次メンテナンスを `--fix` で1回走らせ、SEC で確定した退役6件
      （`EA` `AVNS` `AXIA` `ORLA` `SKYT` `TMHC`）を反映する
- [ ] `doc/in_progress/bynd_split_adjust_plan.md` を `doc/completed/` へ移動
