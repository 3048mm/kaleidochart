# データベース復旧・再構築手順 (Database Recovery Procedure)

本ドキュメントは、データベースの破損、異常な肥大化（WALファイルの暴走等）、または並行処理によるデータ不整合が発生した際の標準的な復旧手順を定めたものです。

---

## 1. 事前準備 (Preparation)

### 1-1. 全プロセスの停止
データベースにアクセスしている可能性のあるすべてのプロセスを停止します。
- メインプロセスおよび並列ワーカープロセス (`python.exe`)
- 外部コピープロセス (`robocopy.exe` 等)
- APIサーバー (FastAPI)

### 1-2. 排他状態の確認
以下のコマンド等で、DBファイルがロックされていないことを確認します。
```powershell
# リネームを試行して成功すればロックされていない
Rename-Item "data/stocktool.db" "data/stocktool_temp.db"; Rename-Item "data/stocktool_temp.db" "data/stocktool.db"
```

---

## 2. 再構築フェーズ (Reconstruction)

### 2-1. 現状のバックアップ
現在の `stocktool.db` を `data/backup_YYYYMMDD/` 等に退避します。
※WALファイルがある場合は、それもセットで移動します。

### 2-2. 新規DBの初期化
最新のモデル定義に基づき、空のDBファイルを作成します。
```python
from db.database import init_db
init_db("data/stocktool_new.db")
```

### 2-3. 基礎データの移植 (T1/T2 Migration)
破損の可能性が低い基礎データ（T1: 銘柄メタ情報、T2: 株価データ）のみを旧DBから抽出して移植します。
SQLite の `ATTACH` 命令を使用することで、高速かつ確実に移行できます。

```sql
ATTACH DATABASE 'data/backup_YYYYMMDD/stocktool.db' AS old_db;
INSERT INTO main.symbols SELECT * FROM old_db.symbols;
INSERT INTO main.daily_prices SELECT * FROM old_db.daily_prices;
```

---

## 3. 運用再開フェーズ (Resumption)

### 3-1. DBファイルの置換
作成した `stocktool_new.db` を本番用の `stocktool.db` にリネームします。

### 3-2. 指標データの再構築
移植しなかった T3（指標）以降のデータを、クリーンな状態で再計算します。
```bash
python backend/scripts/update_pipeline.py --rebuild-from T3
```

---

## 4. 教訓と予防策 (Prevention)
- **WALモードの使用禁止**: Windows環境での多プロセス同時書き込みにおいて、WALファイルの異常肥大化リスクが確認されたため、原則として `DELETE` モード（デフォルト）を使用する。
- **二重起動の物理的防止**: `update_pipeline.py` 自体にファイルロック機能を実装し、定時タスク等との衝突を回避する。
- **定期的な Integrity Check**: 大規模更新の前後には `PRAGMA integrity_check` を実行し、早期に異常を検知する。
