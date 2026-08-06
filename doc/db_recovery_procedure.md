# データベース復旧・再構築手順 (Database Recovery Procedure)

`stocktool.db`（SQLite ホットキャッシュ）の破損・肥大化・環境移行に対する標準手順です。

本システムは「**Parquet が本尊・SQLite はキャッシュ**」なので、
`stocktool.db` は**いつでも捨てて作り直せます**。歴史データの再ダウンロードは不要です。

> [!IMPORTANT]
> **「キャッシュの復旧」と「全期間の再構築」は別物です。取り違えると数時間と履歴を失います。**
>
> | | 何をするか | 所要 | 失うもの |
> | :--- | :--- | :--- | :--- |
> | **A. キャッシュ復旧**（本書 §2） | Parquet → SQLite を入れ直す | 約15分 | なし |
> | **B. 全期間再構築**（§4） | Yahoo から取り直して Parquet ごと作り直す | 約5時間 | **手作業の補正・上流が返さない銘柄・`fx_rates`** |
>
> **まず A を試してください。** B が要るのは「Parquet 自体が壊れている」ときだけです。

---

## 1. 事前準備

### 1-1. 排他の確保

```powershell
# 1. スケジュールタスクの次回実行を確認（並走すると Parquet 世代が壊れる）
Get-ScheduledTask -TaskName "StockTool_DailyUpdate" | Get-ScheduledTaskInfo | Select NextRunTime

# 2. API サーバ（uvicorn）を停止する
Get-CimInstance Win32_Process -Filter "name='python.exe'" |
  Where-Object { $_.CommandLine -like '*uvicorn*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

> [!WARNING]
> **API サーバを止めずに実行すると `DROP TABLE` が読み取りロックで無限に待ちます。**
> 2026-08-06 に "Forcing DROP ALL tables" から5分以上復帰せず（CPU 8秒で停止）、
> プロセスを落として再実行する羽目になりました。
> サーバが生きていると物理削除に失敗し、遅い in-place truncation 経路に落ちます。

パイプライン系スクリプトは `update_pipeline.lock` を共有するため、
日次更新・週次メンテとの同時実行は**自動的に弾かれます**（`pipeline/pipeline_lock.py`）。
`run_production_restore.py` もこのロックを取ります。

### 1-2. `user_data.db` のバックアップ

`stocktool.db` はキャッシュなので失っても再生成できますが、
`user_data.db`（ウォッチリスト・ポートフォリオ）と `universe.db`（銘柄定義）は**ユーザー資産**です。

```powershell
Copy-Item data/user_data.db "data/user_data.db.bak_$(Get-Date -f yyyyMMdd_HHmmss)"
```

---

## 2. A. キャッシュ復旧（推奨・約15分）

```powershell
$env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
.\venv\Scripts\python.exe backend\scripts\run_production_restore.py
```

このスクリプトが以下をまとめて行います。

1. `stocktool.db` とジャーナル（`-wal` / `-shm`）を物理削除
   （ロックされていれば DROP ALL による in-place truncation にフォールバック）
2. 最新の SQLAlchemy モデルからスキーマを再作成
3. Parquet マスターから **2018-04-01 以降**を一括インポート
4. `pipeline_meta` を Parquet の最新日に合わせる（次回の差分取得が正しい位置から始まる）

実測（2026-08-06 / 601万行）: **約12分**。

> [!NOTE]
> `market_signals` と `fx_rates` も Parquet に含まれるため、別途の復旧は不要です
> （`fx_rates` は 2026-08-01 に Parquet 対象化。それ以前の手順書は「Parquet に無い」と
> 書いていましたが**現在は誤り**です）。

### 2-1. 直後に必ず確認する

```powershell
$env:PYTHONPATH="backend"
.\venv\Scripts\python.exe tools\db_health_check.py
```

- `symbols` の件数が Parquet と一致するか
- `daily_prices` と `indicators` の行数が銘柄ごとに一致するか
- dtype 汚染（`symbol_id` が object 型など）が無いか

---

## 3. 復旧後の運用再開

```powershell
# API サーバ
$env:PYTHONPATH="backend"
.\venv\Scripts\python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 8000 --reload
```

`/api/system/info` の `is_production` で接続先を確認します。
フロントエンドは本番以外に繋がるとオレンジの警告バッジを出します。

`/api/watchlist` を開いて `symbol_id` が `null` の項目が無ければ、
heal（`symbol_id` 自己修復）も正常です。

---

## 4. B. 全期間再構築が必要なとき

**Parquet マスター自体が壊れている場合のみ。** 手順は
**`.claude/skills/parquet-data-quality/SKILL.md` §9 を必ず参照してください。**

再構築は Yahoo から取り直すため、**手元にしか無いものを全部失います**。
2026-08-06 の実行では、想定していなかった問題が5つ出ました。

| # | 失うもの / 起きること | 対処 |
| :--- | :--- | :--- |
| 1 | 上流が返さなかった銘柄の履歴（`BDRY` `SXC` が丸ごと、`TMHC` 2086→2行 ほか） | `restore_truncated_symbol_history.py --backup-dir <退避先>` |
| 2 | **`fx_rates` が直近30日だけになる**（7,717→23行） | `restore_fx_from_generation.py --from <退避先>` |
| 3 | 手作業の切り詰め（逆さ合併の `JBIO` が 318→1280行に戻る） | `truncate_symbol_history.py` を再適用 |
| 4 | **`symbols.id` が再採番される**（3,220中2,378件） | `remap_user_data_symbol_ids.py --apply` |
| 5 | 非 active 銘柄（退役・旧ティッカー）の履歴が消える（18件） | 対応不要（`active==1` で除外されるため） |

> [!CAUTION]
> **再構築の前に必ず `archive_parquet_master.py` で退避してください（削除ではなく移動）。**
> 上記1〜3の復旧はすべて「退避した旧世代から戻す」ことで成立します。
> 2026-08-06 は上流が全滅して空の Parquet が公開されましたが、
> **退避があったおかげで 11GB・604万行を失わずに済みました。**

---

## 5. 予防策と教訓

- **Parquet を手で消さない・書かない。** 世代管理（MVCC）は
  `latest_master.json` ポインタで行われます。直接操作すると世代が食い違います。
- **並走を侮らない。** 「MVCC だから排他ロックを恐れる必要はない」という旧記述は**誤り**でした。
  ポインタの差し替え自体はアトミックですが、**「読んで・作って・差し替える」の一連**が
  排他されていないと後勝ちで壊れます。2026-08-06 に日次更新との並走で
  「symbols は旧 id・prices は新 id」という世代が本番になり、`CAT` の終値が 871.08 → 64.13 に
  なりました。現在は Parquet を書く全スクリプトが `update_pipeline.lock` を取ります。
- **「成功しました」を信じない。** 上流が全滅しても T2〜T5 は 0行のまま完走します。
  現在は `is_publishable_master()` と T2 の SPY ガードで止まりますが、
  **復旧後は必ず健全性チェックと終値の妥当性確認**を行ってください。
- **旧世代を急いで消さない。** health check 合格まで MVCC 旧世代がバックアップを兼ねます
  （`agent_execution_rules.md` §10.1）。

---

## 6. 関連ドキュメント

| 文書 | 内容 |
| :--- | :--- |
| `.claude/skills/parquet-data-quality/SKILL.md` §9 | **全期間再構築の手順書**（本書 §4 の詳細） |
| `.claude/skills/upstream-data-diagnosis/SKILL.md` §2.1 | 全銘柄が `possibly delisted` になったときの切り分け |
| `.claude/skills/sqlite-wal-handling/SKILL.md` | WAL・ロック・チェックポイント |
| `universe_db_specification.md` | 銘柄定義マスターの仕様（再構築の対象外） |
| `architecture.md` §11 | ホット/コールドの二層設計 |
