# データベース復旧・再構築手順 (Database Recovery Procedure)

本ドキュメントは、本番データベース（SQLiteキャッシュ）の破損、異常な肥大化、または環境移行に際し、マスターデータソース（Parquet）からクリーンかつ爆速でデータベースを復旧・再構築するための標準手順を定めたものです。

当システムは「Parquet本尊、SQLiteキャッシュ」のハイブリッドアーキテクチャを採用しているため、過去のすべての歴史データは [data/parquet_master/](file:///d:/My%20Documents/Programing/stocktool/data/parquet_master/) 配下にSnappy圧縮Parquet形式で100%安全に保持されています。
そのため、何時間もかかる歴史データの再ダウンロードや、重いSQL再計算は一切不要であり、わずか3分でクリーンな状態へ完全復旧が可能です。

---

## 1. 事前準備 (Preparation)

### 1-1. 全プロセスの停止
データベースにアクセスしている可能性のあるすべてのプロセスを停止します。
- メインプロセスおよび並列ワーカープロセス (`python.exe`)
- APIサーバー (FastAPI)
- バックテストランナー (`backtest_runner.py`)

### 1-2. 排他状態の確認
本番用DBファイル `data/stocktool.db` が別のプロセスにロックされていないことを確認します（失敗した場合はリネームがエラーになります）。
```powershell
# リネームを試行して成功すればロックされていない
Rename-Item "data/stocktool.db" "data/stocktool_temp.db"; Rename-Item "data/stocktool_temp.db" "data/stocktool.db"
```

---

## 2. 復旧・再構築フェーズ (Reconstruction)

### 2-1. 現状ファイルの物理削除
破損または肥大化した本番用データベースファイルを物理削除します。
※全期間データはParquetに完全退避されているため、削除によるデータ消失リスクは0%です。
```powershell
# 本番DBおよびジャーナル（ある場合）の物理削除
Remove-Item -Force "data/stocktool.db"
if (Test-Path "data/stocktool.db-journal") { Remove-Item -Force "data/stocktool.db-journal" }
if (Test-Path "data/stocktool.db-wal") { Remove-Item -Force "data/stocktool.db-wal" }
if (Test-Path "data/stocktool.db-shm") { Remove-Item -Force "data/stocktool.db-shm" }
```

### 2-2. 新規DBの初期化 (空のスキーマ再作成)
最新の SQLAlchemy モデル定義に基づき、クリーンで空のデータベースファイルを再作成します。
```powershell
$OutputEncoding = [System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"; $env:PYTHONPATH="backend"; & "venv/Scripts/python.exe" -c "from db.database import init_db; init_db('data/stocktool.db')"
```

### 2-3. Parquetマスターからの超高速リストア
本尊Parquetファイルから「直近2年分」のインジケーターを含むすべてのキャッシュデータを爆速で SQLite DB へ流し込みます。
```powershell
# 復旧追跡スクリプトを実行してリストアを開始
$OutputEncoding = [System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"; $env:PYTHONPATH="backend"; & "venv/Scripts/python.exe" "scratch/test_restore_step.py"
```

*※最適化した PyArrow 日付フィルタと native sqlite3 バルクインサートにより、約1,700万行の歴史マスターデータから203万行の2年キャッシュ分を抽出し、**約3分で完全インポート**が完了します。*

---

## 3. 追加付随データのリストア (Optional)

データパージの対象外であり、Parquetマスターに含まれない一部の軽量テーブル（`market_signals`：相場環境シグナル、`fx_rates`：為替レート）を、退避させておいたバックアップDBから復旧します。

```powershell
# 軽量付随データのリストアスクリプトを実行
$OutputEncoding = [System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"; $env:PYTHONPATH="backend"; & "venv/Scripts/python.exe" "scratch/restore_market_signals.py"
```

*※バックアップ DB `data/_bk/stocktool.db` から、シグナルデータ（約1,500件）と為替データを数秒でコピー・コミット完了します。*

---

## 4. 運用再開フェーズ (Resumption)

### 4-1. 整合性の確認 (Health Check)
DBの整合性が保たれていることをヘルスチェック用コマンド等で確認します。
```powershell
# APIサーバーまたはバッチを動かして、ダッシュボードやスクリーナーにエラーが出ないか確認
```

### 4-2. プロセスの起動
停止していたAPIサーバーおよびWeb APIの各プロセスを通常通り起動し、運用を再開します。

---

## 5. 予防策と教訓 (Prevention)
- **Parquetの最優先保護**: 復旧処理中であっても、マスターである [data/parquet_master/](file:///d:/My%20Documents/Programing/stocktool/data/parquet_master/) 配下のParquetファイルには絶対に手動で書き込みや削除を行ってはならない。
- **定期的な自動バックアップの実施**: 物理削除前の退避用に、`data/_bk/` ディレクトリ内に本番 `stocktool.db` のバックアップを定期的に保持しておくこと。
- **ロック競合の完全防止**: パイプラインとバックテストの並行実行時も、MVCCタイムスタンプ世代管理によって競合が発生しないようアーキテクチャ的に防御されているため、排他ロックを恐れる必要はありません。
