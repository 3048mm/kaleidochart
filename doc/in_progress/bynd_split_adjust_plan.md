# BYND 併合(1:30)の価格補正と、汚染された仮想テーマ指数の再合成 計画書

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

- [ ] Sandbox 準備（`data/sandbox/parquet_master/` に本番 Parquet をコピー、環境変数2つを絶対パスで設定）
- [x] テスト先行: `rebuild_virtual_index_prices` の同値性テスト（SQLAlchemy 版との一致）
- [x] `rebuild_virtual_index_prices` を `parquet_recompute.py` に実装
- [x] テスト先行: `adjust_symbol_split` の接合部検算・スケーリング・market_cap 非スケールのテスト
- [x] `backend/scripts/adjust_symbol_split.py` を実装
- [ ] Sandbox で `--dry-run` → `--apply`、SQL で値を直接検証
- [ ] `tools/db_health_check.py --all --check-nulls` / `scan_price_anomalies.py` で確認
- [ ] pytest 全件パス
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
  .env\Scripts\python.exe backend\scriptsdjust_symbol_split.py `
      --ticker BYND --before 2026-08-13 --factor 30 `
      --reason "1:30 併合 (2026-08-14 ET)" --db-path <sandbox.db> --dry-run
  ```

## 6. 検証プラン / 結果

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

（作業中に追記）

## 8. スコープ外・残作業

- **他の未処理段差**（週次が3週間止まっていた間に蓄積）。08-07 以降だけで以下が未判定:
  `MNST`(08-10 ×0.506, 代金比0.67 — 1:2分割の未調整の疑い) / `AVB`(08-21 ×0.358 — 価格自体がありえない) /
  `SION` / `EYPT` / `CVRX` / `INV` / `TDUP` / `MYGN` / `GWH` / `NCMI` / `WWR` / `STKH` / `HYFM` / `MRNA`
  → 週次メンテナンス復旧後に全数を出してから個別判断する。
- **限界3の予防**（Yahoo の分割記録を日次で適用して段差の蓄積自体を防ぐ）は引き続き対象外。
  ただし本件で「記録があっても上流の適用が歯抜け」というケースが実在すると分かったため、
  適用方式を採る場合は**上流の適用状況を信用しない**設計が要る。
