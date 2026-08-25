# 価格データの補修（BYND / MNST / AVB）と仮想テーマ指数の再合成 計画書

- **ステータス**: 🚧 進行中（計画はユーザーレビュー済み 2026-08-25）
- **実施者**: AI エージェント (Claude Opus 5)
- **開始日**: 2026-08-25 / **完了日**: —
- **作業ブランチ**: `worktree-bynd-split-adjust`
- **対象 issue / 関連ドキュメント**:
  - `.claude/skills/upstream-data-diagnosis/SKILL.md` §4「未調整の分割か、実際の急変か」・§6 限界1/限界3
  - `doc/completed/split_anomaly_noise_reduction_plan.md` §8（「補正は不要と判断した(2026-08-06)」— **本件で前提が変わる**）
  - `backend/pipeline/parquet_recompute.py`（`recompute_indicators` / `recompute_ranks` / `find_affected_virtual_themes`）

## 1. 背景と目的

`BYND`（Beyond Meat, symbol_id=680）が **2026-08-14 09:30 ET 付で 1:30 の株式併合**を実施した。
手元のデータは併合前スケールのまま残り、**2026-08-13 に ×30.1 の段差**がある。

```
2026-08-12   close 0.4141   volume 108,607,358     ← 併合前スケール
2026-08-13   close 12.4650  volume   2,574,106     ← 併合後スケール（段差 ×30.10）
```

### 上流(Yahoo)も壊れているため「取り直し」では直らない

`query1` / `query2` × 3周の決定論テスト（SKILL §2）を実施。AAPL は毎回 11,516 行で正常、
BYND は毎回**まったく同一の壊れ方**をする＝レート制限でも一過性でもない**供給側の欠陥**。

Yahoo の全期間系列 1,838 行のうち、**7営業日だけが ×30 調整済み**で残りは生値という歯抜け状態:

```
07-20 17.85  07-21 18.30  07-22 17.91  07-31 16.98  08-06 15.84  08-11 12.54  08-12 12.42   ← 調整済み
07-23  0.56  07-24  0.56  07-27  0.55  07-28  0.54  07-29  0.53  07-30  0.56              ← 生値
```

分割イベント記録自体は存在する（`events.splits` に `1:30`, ts=1786714200 = 2026-08-14 09:30 ET）。
つまり **限界1（記録が無い）ではなく、記録はあるのに適用が歯抜け**という新種。

> [!CAUTION]
> **全期間再取得は禁止。** 歯抜けの ×30 が混じったギザギザ系列になり、現状より悪化する。
> SKILL §7「完全再構築で上書きしない」にも該当する。

### 波及: 仮想テーマ指数3本が汚染されている

仮想テーマ指数は構成銘柄の**日次平均リターンを連鎖**して合成する（`orchestrator.build_virtual_index_prices`）。
BYND の 08-13 のリターンが +2910% として入ったため、所属テーマ3本すべてが同日に飛んでいる。

| テーマ | 名称 | 構成数 | 2026-08-12 | 2026-08-13 | 倍率 |
| :--- | :--- | ---: | ---: | ---: | ---: |
| `_CNSM0A_` (id=317) | 生活::健康・食品・飲料 | 12 | 972.23 | 3,343.20 | ×3.44 |
| `_GRCL29_` (id=240) | 農業::代替タンパク・フードテック | 6 | 528.46 | 3,082.00 | ×5.83 |
| `_NTRTFC_` (id=320) | 栄養・食品::植物性食品・代替肉 | 6 | 827.58 | 4,859.06 | ×5.87 |

### なぜ3週間気付かなかったか

週次メンテナンス（監査項目5が段差を検出する）が **2026-08-09 / 08-16 / 08-23 の3回とも
実行されていなかった**（タスクの登録コマンドが空。別ブランチ `worktree-fix-weekly-task-registration`
で修正済み）。BYND の段差は 08-13 なので、**08-16 の回で検出されるはずだった**。

### 成功条件

1. BYND の全期間系列（2019-05-02 〜）が単一スケール（併合後）で連続すること。08-13 の段差が消える。
2. 汚染テーマ3本の指数が全期間で正しい値に戻ること。
3. BYND と3テーマの T3（指標）・T4（順位）が補正後の価格から再計算されていること。
4. Parquet と SQLite の両方が直っていること（**片方だけだと翌日のデイリーで元に戻る**）。
5. `db_health_check.py` と `scan_price_anomalies.py` が NG を出さないこと。

## 2. スコープと設計判断

### 2.1 変更すること

1. 新規スクリプト `backend/scripts/adjust_symbol_split.py`
   — Parquet と SQLite の両方に対して、指定日より前の OHLC を `×factor` / volume を `÷factor` する。
   T3 再計算 → 仮想テーマ再合成 → T4 再計算 → 新世代書き出し → SQLite 同期まで行う。
2. `backend/pipeline/parquet_recompute.py` に `rebuild_virtual_index_prices()` を追加
   — 現状の仮想指数合成は SQLAlchemy 依存（`orchestrator.build_virtual_index_prices`）で
   Parquet 経路から呼べない。**同じアルゴリズムの Parquet 版**を切り出す。
3. テスト `backend/tests/scripts/test_adjust_symbol_split.py` / `test_parquet_recompute.py` への追加。
4. `doc/completed/split_anomaly_noise_reduction_plan.md` §8 に「2026-08-25 に前提が変わった」旨を追記。
5. `.claude/skills/upstream-data-diagnosis/SKILL.md` §6 に「限界1'（記録はあるが適用が歯抜け）」を追記。

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| 上流から全期間取り直して直す | **やらない** | Yahoo 側が歯抜けで、取り直すと現状より悪化する。SKILL §7 |
| `market_cap` もスケールする | **やらない** | 併合は株数÷30・株価×30 なので時価総額は不変。実データでも併合前の market_cap は正しい |
| 08-13 の `market_cap` (6.43e9) | **この1行だけ補正** | 株数が未更新のまま価格×30 が入った1日だけのスパイク。08-14 以降は 2.3e8 で正常 |
| 汎用の「補正テーブル」を作る | **やらない** | 実データ調査（2026-08-06）では要補正は年に数件。都度スクリプトで足りる |
| 分割比を自動推定する | **やらない** | 比率は Yahoo の `events.splits` から取得し、`--factor` で明示指定＋接合部で検算する。推定値を本番に書かない |
| 他の段差（MNST/AVB/SION 等）も同時に直す | **やらない** | 本プランは BYND のみ。他は週次メンテの全数棚卸し後に個別判断（§8） |

## 3. 変更内容

### 3.1 `backend/scripts/adjust_symbol_split.py`（新規）

`truncate_symbol_history.py` と同じ骨格（`pipeline_lock` / dry-run 既定 / 世代書き出し / SQLite 同期）。

```
--ticker BYND --before 2026-08-13 --factor 30 --reason "1:30 併合(2026-08-14 ET)" --dry-run / --apply
```

処理順:

| # | 内容 | 対象 |
| :--- | :--- | :--- |
| 1 | 接合部の検算（`close[before] / close[before-1]` が `factor` の ±5% 内か） | 失敗したら**中断** |
| 2 | `date < --before` の `open/high/low/close` を `×factor`、`volume` を `÷factor` | Parquet `prices` |
| 3 | 08-13 の `market_cap` を前後の中央値で補正 | Parquet `prices` |
| 4 | BYND の T3 を全期間再計算（`recompute_indicators`） | Parquet `indicators` |
| 5 | `find_affected_virtual_themes` で3テーマを特定 → 全期間再合成（新規 `rebuild_virtual_index_prices`） | Parquet `prices` |
| 6 | 再合成したテーマの T3 も再計算 | Parquet `indicators` |
| 7 | T4 を全期間再計算（`recompute_ranks`） | Parquet `ranks` |
| 8 | 新世代を書き出して `latest_master.json` を更新（旧世代は prune しない） | Parquet |
| 9 | SQLite の `daily_prices` / `indicators` / `relative_ranks` を同じ内容に同期 | `stocktool.db` |
| 10 | `data/virtual_theme_hashes.json` から3テーマのハッシュを削除 | 次回 T2 で強制再合成させる |

> [!IMPORTANT]
> **9 を省くと翌日のデイリー更新で補正が元に戻る。** `process_and_merge_table` は
> `drop_duplicates(keep='last')` で SQLite 側を優先するため、SQLite に残った併合前スケールの
> 495行（2024-08-21 〜 2026-08-12）がそのまま Parquet に復活する。
> `truncate_symbol_history.py` の教訓（2026-08-06 の `JBIO`）と同じ罠。

> [!IMPORTANT]
> **10 を省くと差分モードのまま過去が再計算されない。** 再合成の判定は
> **構成銘柄集合のハッシュ**で決まるため、構成銘柄の「価格」が変わってもハッシュは変わらない
> （`parquet_recompute.find_affected_virtual_themes` の docstring に既出）。

### 3.2 `rebuild_virtual_index_prices()`（`parquet_recompute.py` に追加）

`orchestrator.build_virtual_index_prices` と**同じ式**を Parquet 上で実装する。
DB 依存を外すだけで、アルゴリズムは変えない:

- 構成銘柄の `close` から `pct_change` → 日付ごとの平均 → `1000.0` から連鎖して指数化
- `open`/`high`/`low` は `close` と同値
- `volume` は「21日平均売買代金に対する当日売買代金の比（surge）」の日次平均 × 1,000,000

**同値性はテストで固定する**（同じ入力に対し SQLAlchemy 版と一致すること）。

### 3.3 影響範囲

- BYND の指標・順位・スクリーナー結果・バックテスト母集団が変わる（**現状が誤りなので変わるのが正しい**）。
- 汚染テーマ3本のチャート・RRG・グループ画面の表示が変わる。
- `symbols.id` は変更しない（T1 の id 温存制約に抵触しない）。
- `user_data.db` は触らない。

## 4. ユーザー確認事項

| # | 事項 | 状態 |
| :--- | :--- | :--- |
| 1 | 補正方式を「ローカルで ×30 バックアジャスト」とすること | **合意済み**（2026-08-25） |
| 2 | 他の段差（MNST/AVB/SION 等）は週次メンテの全数棚卸し後に別途判断 | **合意済み**（2026-08-25） |
| 3 | 昇格（本番 Parquet の swap + SQLite 同期）は**日次パイプライン停止中**に行う必要がある | 要調整（本日 07:00 の日次が実行中） |
| 4 | `market_cap` の 08-13 スパイク (6.43e9) の扱い | **合意済み**（2026-08-25）— 前後（08-12 の 2.14e8 / 08-14 の 2.32e8）から補間して埋める。mcap フィルタを使うスクリーナー・バックテストが 08-13 だけ別の母集団にならないようにするため |

## 5. 実装順序と進捗チェックリスト

- [x] Sandbox 準備（`tmp/reset_bynd_sandbox.py` で本番の現世代から作り直す）
- [x] テスト先行: `rebuild_virtual_index_prices` の同値性テスト（SQLAlchemy 版との一致）
- [x] `rebuild_virtual_index_prices` を `parquet_recompute.py` に実装
- [x] テスト先行: `adjust_symbol_split` の接合部検算・スケーリング・market_cap 非スケールのテスト
- [x] `backend/scripts/adjust_symbol_split.py` を実装
- [x] Sandbox で `--dry-run` → `--apply`、SQL で値を直接検証（§6.1）
- [x] `scan_price_anomalies` の分類を検証（BYND/MNST が split_suspect に出ることを確認）
- [x] pytest 全件パス（pipeline + indicators で 403 passed）
- [ ] フロントエンドで BYND チャートと汚染テーマ3本を目視確認（オレンジバッジ＝Sandbox である確認込み）
- [ ] ユーザーレビュー → main へマージ
- [ ] 本番昇格（日次パイプライン・API 停止 → 適用 → health check → 再開）
- [x] ドキュメント更新（§2.1 の 4, 5）
- [ ] 本計画書を `doc/completed/` へ移動

### 作業中メモ

- **次にやること**: 日次パイプライン完了 → Sandbox 作成 → `--dry-run` → `--apply` → 検証。
- 2026-08-25 08:20 時点で日次パイプラインが実行中（07:00 開始、約2,100/3,051）。本番適用は完了後。
- BYND の SQLite 側は 495 行（2024-08-21 〜 2026-08-12）、Parquet 側は 1,830 行（2019-05-02 〜）が補正対象。
- 実行コマンド（Sandbox）:
  ```powershell
  $env:PYTHONPATH="backend"
  .\venv\Scripts\python.exe backend\scripts\adjust_symbol_split.py `
      --ticker BYND --before 2026-08-13 --factor 30 `
      --reason "1:30 併合 (2026-08-14 ET)" --db-path <sandbox.db> --dry-run
  ```

## 6. 検証プラン / 結果

### 6.1 Sandbox 検証結果（2026-08-25 / 世代 20260825_145931 を元に実施）

| 検証 | 期待 | 結果 |
| :--- | :--- | :--- |
| BYND の 08-12 → 08-13 の比率 | 1.0 近傍 | **1.0034**（補正前 30.10）✅ |
| BYND の日次リターンが変わった日 | 接合日のみ | **1,837日中 2026-08-13 の1日だけ** ✅ |
| BYND 全期間が単一スケール | 最小終値 ≥ 1.0 | 11.63 ✅ |
| market_cap の接合日スパイク | 前後から補間 | 6.43e9 → **2.226e8** ✅ |
| 売買代金の連続性 | 接合部で連続 | 比 0.713 ✅ |
| SQLite と Parquet の一致 | 全行一致 | BYND / テーマ3本とも一致 ✅ |
| 2024-08-06 の再ベース是正 | 段差の解消 | **169テーマが是正**（`_CNSM0A_` −53.2%→+0.2% / `_BLCK3F_` −92.5%→+3.3% / `_GRCL29_` +209.9%→−0.7%）✅ |
| 日中値の保持 | `open != close` | `open==close` 0.25%（本番 0.26%）✅ |
| 変更範囲 | 最小限 | テーマ 356,625行中 **3,386行**のみ変化（大半が 2024-08-06）✅ |

残存: `|日次リターン| > 40%` が 89行 / 15テーマ（`_DRON_` 38 / `_QNTMDD_` 26 ほか）。
**2018〜2020年に集中しており本番にも存在する**構成銘柄側の古いデータ起因。本プランの対象外（§8）。

### 6.1.1 最終 Sandbox 検証（2026-08-25 / 世代 20260825_220805）

本番と同じ順序で4本を通した結果。

| 対象 | 検証 | 結果 |
| :--- | :--- | :--- |
| BYND | リターンが変わった日 | **1,837日中 2026-08-13 の1日だけ**（+2910% → +0.34%）✅ |
| MNST | リターンが変わった日 | **2,110日中 2026-08-10 の1日だけ**（−49.4% → +1.18%）✅ |
| BYND / MNST | SQLite と Parquet の一致 | 500行照合・不一致0 ✅ |
| BYND / MNST | ホットキャッシュの窓 | SQLite 500行 / Parquet 1,838・2,111行 ✅ |
| AVB | 捏造5行の削除 | Parquet・SQLite とも消えている ✅ |
| 仮想テーマ170本 | 2024-08-06 の再ベース | **残存0件 / 170テーマ** ✅ |
| 仮想テーマ170本 | ホットキャッシュの窓 | 最大 500行/テーマ ✅ |

残存: BYND の 2025-10-13 / 10-20 / 10-21 に |リターン| > 40% があるが、
**本番と完全に同一の比率**で、実際の急変（-48% / +128% / +146%）。補正の対象外。

### 6.2 検証コマンド

```powershell
.\venv\Scripts\python.exe tmp\reset_bynd_sandbox.py          # Sandbox を本番の現世代から作り直す
$env:PYTHONPATH="backend"; $env:STOCKTOOL_DB_PATH="<sandbox.db>"
.\venv\Scripts\python.exe backend\scripts\adjust_symbol_split.py --ticker BYND --before 2026-08-13 --factor 30 --apply
.\venv\Scripts\python.exe backend\scripts\rebuild_virtual_indexes.py --all --apply
.\venv\Scripts\python.exe tmp\verify_bynd_adjust.py <sandbox parquet_master> <sandbox.db>
```

> [!IMPORTANT]
> **本番のポインタは動く。** 日次が 07:00 と 13:00 に走るので、Sandbox を作るときは
> 毎回ポインタを読み直して足りないファイルをコピーすること。「前回と同じ世代だろう」と
> 仮定して差分だけ消すと Sandbox を壊す（2026-08-25 に実際にやった）。

### 6.3 当初の検証項目

| 検証 | 期待 |
| :--- | :--- |
| BYND の 08-12 → 08-13 の比率 | `0.99` 前後（現状 `30.10`） |
| BYND 全期間の `scan_price_anomalies` 判定 | 段差なし |
| 補正前後の BYND 日次リターン系列 | 08-13 **以外**は完全一致（スケール不変） |
| 3テーマの 08-12 → 08-13 の比率 | 構成銘柄の平均リターン相当（現状 ×3.44 / ×5.83 / ×5.87） |
| 3テーマの 08-13 より前の指数リターン | 補正前と完全一致（BYND のリターンが不変のため） |
| T2/T3 行数 | 補正前後で一致（行は増減しない） |
| `db_health_check.py --all --check-nulls` | OK |

## 7. 途中発生した課題

### 7.1 Sandbox 検証で見つけた3つのバグ（いずれも例外が出ない）

| 事象 | 症状 | 対応 |
| :--- | :--- | :--- |
| `recompute_indicators` が object dtype を返す | 既存 indicators と concat すると600万行×63列がまるごと object に巻き上げられ、`sort_values` のコピーで OOM。**ディスク上は pyarrow が double に落とすので Parquet は汚染されず、メモリ上でだけ膨らむ** | 返す前に数値 dtype へ落とす。`truncate_symbol_history` / `restore_truncated_symbol_history` にも効く |
| `symbol_id` が BLOB で INSERT される | ID 列は Parquet 規約で `Int64` なので `itertuples()` が `numpy.int64` を返す。numpy スカラはバッファプロトコルを持つため sqlite3 が BLOB として束縛し、**挿入は成功して件数も合うのに `WHERE symbol_id = ?` が1件も返らない** | `[tuple(x) for x in df.to_numpy()]` に変更（`restore_sqlite_cache_from_parquet` と同じ形） |
| 仮想指数の移植先違い | 合成関数が2つあり、テスト専用の簡易版 `build_virtual_index_prices`（OHLC を全部 close と同値にする）を移植していた。本番テーマは 100% の行で `open != close` | `build_all_virtual_indexes_prices` に合わせ直す。テスト用の構成銘柄データにも日中値を持たせた（全部同値だと欠陥が素通りする） |

```
numpy.core._exceptions._ArrayMemoryError: Unable to allocate 1.26 GiB
for an array with shape (28, 6057722) and data type object
```
```
(b'=      ', 2110, '2018-04-03', '2026-08-24')
```

### 7.2 仮想テーマ170本の再ベース事故（**当初スコープ外・拡大して対応**）

BYND のテーマ3本を再合成したところ、本番との差分が **2026-08-13（今回の対象）と
2024-08-06 の2日だけ**だった。2024-08-06 を調べたところ、全170本中165本が
その日に終値 ~1000 を持ち、152本が大きな段差を持っていた。

原因はログに残っていた:

```
2026-08-07 07:49:34  Virtual Index Progress: 80/170 - Processing _SFTW10_ (rebuild)
2026-08-07 07:49:38  Virtual Index Progress: 170/170 - Processing _NTRTCE_ (rebuild)
```

構成銘柄の変更をきっかけに全170テーマが `rebuild` モードで再合成されたが、
`build_all_virtual_indexes_prices` は **SQLite（直近730日）**から読むため、
当時の最古日 2024-08-06 で指数が基準値1000から振り直され、Parquet の古い履歴の上に
マージされた。T4 の「SQLite に存在する日付しか計算できない」問題と同じ根本原因。

```
_HLTHCB_  2024-08-06  close=1019.65  当日リターン -98.2%   ← 直前は ~55,700
_BLCK3F_  2024-08-06  close=1033.16  当日リターン -92.5%
_BLOK_    2024-08-06  close=1015.56  当日リターン -91.5%
```

皮肉なことに 2026-08-07 は**週次メンテナンスが最後に走った日**で、以降3週間
止まっていたため誰も気付かなかった。

ユーザー判断（2026-08-25）で **170本すべての再合成と T2 本体の修正を本プランに含める**。

### 7.3 補修スクリプトの共通部品を切り出した

`adjust_symbol_split` と `rebuild_virtual_indexes` で「世代書き出し → ポインタ差し替え
→ SQLite 同期」が同型になるため、`pipeline/parquet_maintenance.py` に集約した。
ここを複製すると必ず片方だけ直され、「Parquet は直ったが SQLite は古いまま」という
半端な状態を生む。

## 8. スコープ外・残作業

- **他の未処理段差**（週次が3週間止まっていた間に蓄積）。08-07 以降だけで以下が未判定:
  `MNST`(08-10 ×0.506, 代金比0.67 — 1:2分割の未調整の疑い) / `AVB`(08-21 ×0.358 — 価格自体がありえない) /
  `SION` / `EYPT` / `CVRX` / `INV` / `TDUP` / `MYGN` / `GWH` / `NCMI` / `WWR` / `STKH` / `HYFM` / `MRNA`
  → 週次メンテナンス復旧後に全数を出してから個別判断する。
- **限界3の予防**（Yahoo の分割記録を日次で適用して段差の蓄積自体を防ぐ）は引き続き対象外。
  ただし本件で「記録があっても上流の適用が歯抜け」というケースが実在すると分かったため、
  適用方式を採る場合は**上流の適用状況を信用しない**設計が要る。
