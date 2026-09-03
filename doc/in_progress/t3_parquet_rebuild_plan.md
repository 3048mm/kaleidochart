# T3 リビルドを Parquet 基点に統一し、価格履歴の充足機能を作る 計画書

- **ステータス**: 🚧 計画レビュー中
- **実施者**: AI エージェント (Claude Opus 5) — オーケストレーター
- **開始日**: 2026-09-03 / **完了日**: —
- **作業ブランチ**: `worktree-t3-parquet-rebuild`（`.claude/worktrees/t3-parquet-rebuild` / main 基点）
- **前提（実施済み）**: `28982d8` — rotate のマージ失敗でコールド履歴を捨てないようにする修正
- **旧ドラフト**: `worktree-t3-rebuild-warmup`（`7edfdef` / `d59d112`）。設計が変わったため本計画が置き換える。**未マージのまま破棄してよい**
- **関連**: `.claude/skills/sandbox-workflow/SKILL.md`（適用必須）、
  `.claude/skills/parquet-data-quality/SKILL.md` §8/§9、`doc/agent_execution_rules.md` §10

## 1. 背景と目的

### 続けて起きた2つの事故

| | 事故 | 原因 | 状態 |
| :--- | :--- | :--- | :--- |
| ① | 2026-08-29: `--rebuild-from T3` が 2024-09〜2025-08 の 772,526行を誤った値で上書き | 再計算の入力が **SQLite の730日窓**で、その先頭では遡り履歴が足りない。`min_periods=1` の指標が「それらしい誤った値」を出す | **本計画で対処** |
| ② | 2026-09-01: 指標マスターが 6,076,932行 → 1,594,632行に切り詰められ 2021〜2024前半が消失。最適化が全滅 | rotate のマージが OOM し、`except` で握りつぶして SQLite の内容だけを書いた | **修正済み**（`28982d8`） |

①の実測（772,526行）:

| カラム | 差分行の割合 | 相対差の中央値 |
| :--- | ---: | ---: |
| `sma_200` | 77.7% | 4.99% |
| `dist_52w_high_pct` | 37.6% | 38.31% |
| `rs_blue_dot_age` | 点灯 4,983件 → **0件** | — |

### 調査で判明した構造

**(a) T3 は銘柄ごとの全行を入力にする**（`t3_indicators.py` L22-24。日付の絞り込みなし）。
つまり**入力に何が入っているかが全て**で、SQLite が切り詰められていればそのまま誤差になる。

**(b) T2 は2系統で取得している**（`config.toml` / `t2_prices.py` L89-90）:

```toml
index_start_date   = "2010-04-01"   # レバレッジ / 市場 / 指標
default_start_date = "2018-04-01"   # 個別 / テーマ / セクタ
```

**(c) ところが復元は単一カットオフで、`index_start_date` を無視している**
（`parquet_cache_manager.py` L525-529）。実測での裏付け:

```
Parquet   prices : 2010-04-01 〜        （49銘柄が pre-2018 を持つ）
復元後 SQLite    : 2018-04-02 〜        ← SPY の 2010〜2018 が入っていない
```

pre-2018 のデータを持つのは **3,236銘柄中 49銘柄のみ**（市場23 / レバレッジ16 / 指標10。
**個別は 0銘柄**）。その行数は **84,918行＝全体の 1.4%** にすぎない。

**(d) したがって影響範囲は当初の想定と違った。**
個別銘柄は Parquet 自体が 2018-04 開始なので、2018-04 起点で復元すれば**全履歴が入り、
再計算しても同じ値が再現される**。①が起きたのは窓が730日だったからで、
`refresh_T3Table.bat` 経由（2018起点）なら個別銘柄は壊れない。
**壊れるのは (c) により切り詰められる 49銘柄だけ**で、そこに SPY が含まれる
（SPY の `sma_200` は MTS の入力）。

### EMA は長いウォームアップを要する

`calculate_ema_tv` は SMA を種にした再帰計算で、初期値の影響は `(1-alpha)^k` で減衰する。
`ema_200`（alpha = 2/201）:

| ウォームアップ | 初期値の残存重み |
| ---: | ---: |
| 125 本 | **28.7%** |
| 252 本 | 8.0% |
| 500 本 | 0.69% |
| 750 本 | 0.06% |

個別銘柄のデータが 2018-04 開始である以上、`optimization_validation` の
`stress_bear` 窓（**2018-10-01 〜 12-31**）は遡り **125本**で評価されている。
`sma_200` / `dist_52w_high_pct` / `is_trend_template` が成立せず、
**この窓の検証結果は現時点で信頼できない**（将来のリスクではなく現状）。

### 目的

1. **T3 の再計算を、切り詰められた入力から行わない構造にする**
2. **個別銘柄にも実際のウォームアップを持たせる**（2017年まで価格を充足する）

## 2. スコープと設計判断

### 2.1 変更すること

- `update_pipeline.py --rebuild-from T3` の入力を **SQLite から Parquet へ**変える
- 復元が `index_start_date` を無視しているバグを直す
- **価格履歴の充足機能**を新設する（追記専用・Parquet 直書き）
- `refresh_T3Table.bat` を薄いラッパーへ縮小

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| 復元範囲を広げて SQLite にウォームアップを載せる（旧案B） | **やらない** | 入力を Parquet にすれば不要。SQLite を膨らませると②の OOM を誘発する |
| `T3_MIN_LOOKBACK_BARS` で不足行を書かない（旧案C） | **やらない** | 入力が常に全履歴になるので不足行が発生しない。加えて、この案は**履歴の短い IPO 銘柄の T3 を丸ごと落としてスクリーナーから消す**副作用があった（ユーザー指摘 2026-09-02） |
| EMA の初期値を Parquet からシードする（旧案③） | **やらない** | 入力を Parquet にすれば再帰を最初から回せるので不要。ローリング窓（`rolling(252).max` 等）は前日値からは復元できず、結局生の価格が要る |
| `min_periods=1` を `min_periods=window` に変える | **やらない** | 全指標の値が変わり、既存のバックテスト結果・最適化 study が全て無効になる。**別issue** |
| `stress_bear` の 2018-10 窓を外す | **やらない**（ユーザー判断 2026-09-03） | 充足機能で 2017 まで遡れば有効になるため、窓は据え置く |
| 全期間再構築（`--re-calculate`。yfinance 取り直し） | **やらない** | 不可逆。2026-08-02 に 17銘柄・8年分を喪失した実績がある。充足は**追記のみ**で行う |
| 既に本番に入っている「履歴先頭の近似値」 | **触らない**（ユーザー判断） | フル履歴計算の結果そのもの |

## 3. 変更内容

### 3.1 `--rebuild-from T3` を Parquet 基点に変える

```
現行: SQLite（切り詰められている可能性あり）を読んで再計算 → rotate
新:   Parquet の prices から全銘柄・全期間を再計算 → 新世代を publish → SQLite を復元
```

`pipeline/parquet_recompute.py::recompute_indicators()` が primitive として既にある
（yfinance に触らない）。ETF は 2010、個別は 2018 と、**各銘柄の全履歴が自動的に入力になる**。

> [!WARNING]
> **チャンク処理が必須。** `recompute_indicators` は銘柄ごとの結果を `out` リストに
> 溜めて最後に `pd.concat` する作りで、**全銘柄(600万行)で呼ぶと②と同じ OOM を踏む**。
> 銘柄を N 件ずつに割り、`ParquetWriter` で逐次書き出す。

`refresh_T3Table.bat` は上記1コマンドを呼ぶだけに縮小する（現行の Step 1「復元」は
入力が Parquet になるので不要）。

### 3.2 復元のカットオフ修正

`restore_sqlite_cache_from_parquet` が `default_start_date` 単独でフィルタしているため、
ETF の 2010〜2018 が SQLite に入らない（§1(c)）。

**カットオフを撤廃して全期間を読む**。コストは +84,918行（**+1.4%**）で無視できる。
カテゴリ判定を入れるより単純で、取りこぼしが起きない。

あわせて docstring の誤りを直す:

| 対象 | 内容 |
| :--- | :--- |
| `parquet_cache_manager.py` L470 | "restoring only the latest 2 years" は誤り。実際は `default_start_date` 以降 |
| `parquet_cache_manager.py` L354-358 | `purge_..._2_years` の "Performs VACUUM" は誤り。VACUUM は週次メンテへ移管済み |

### 3.3 価格履歴の充足機能（新設）

`backend/scripts/backfill_price_history.py`

```
--start-date 2017-01-01
--category 個別,テーマ,セクタ     # ETF は既に 2010 から持っている
--dry-run                         # 対象銘柄数と取得予定範囲だけ出す
--apply
```

処理:

1. Parquet の prices から銘柄ごとの最古日を取得
2. `--start-date` より後に始まる銘柄について、**`[start-date, 最古日-1]` だけ**を yfinance から取得
3. **既存行に一切触れず**、取得分を追記して新世代を publish
4. 取得範囲の実測検証（下記）

> [!IMPORTANT]
> **SQLite ではなく Parquet に直接書く。** 2017年の行はホット期間(730日)の外なので、
> SQLite に入れると `purge_sqlite_cache_older_than_2_years` に削除される。
> 充足と rotate が別実行になると間に purge が入って消えるため、Parquet 直書きにする
> （`backfill_structure_pivot.py` と同じ形）。

> [!WARNING]
> **429 の握り潰しを検証すること。** yfinance はレート制限を
> `possibly delisted; no price data found` として返すため、**部分的にしか取れなくても
> 成功に見える**（`backfill_symbol_history.py` の docstring が警告している既知の罠）。
> 取得後に銘柄ごとに「要求した期間が実際に入ったか」を突合し、不足銘柄をレポートする。

既存の `backfill_symbol_history.py` とは別物（あちらは「壊れた銘柄の全期間を削除して
取り直す」修復ツール）。本スクリプトは**追記専用**で、`symbols.id` にも既存行にも触らない。

### 3.4 実行順序

```
1. backfill_price_history.py --apply     # T2 を 2017 まで充足（Parquet 直書き）
2. update_pipeline.py --rebuild-from T3  # Parquet 基点で T3〜T5 を再計算
3. db_health_check.py --all
```

## 4. ユーザー確認事項

**2026-09-03 に方針確定。**

| # | 確認事項 | 判断 |
| :--- | :--- | :--- |
| 1 | 充足機能を本計画に含めるか | ✅ **含める** |
| 2 | 充足の対象と開始日 | ✅ **個別・テーマ・セクタ / 2017-01-01**（ETF は既に 2010 から） |
| 3 | `stress_bear` の 2018-10 窓 | ✅ **そのまま**（充足すれば有効になるため） |
| 4 | `min_periods=1` の是正 | ✅ **別issue** |
| 5 | 既存の「履歴先頭の近似値」 | ✅ **触らない** |
| 6 | EMA シード（旧案③） | ✅ **不要** |

## 5. 実装順序と進捗チェックリスト

### Phase 1 — 実装（TDD）

- [ ] `test_recompute_chunked.py` — 全銘柄再計算がチャンクで OOM せず、既存 Parquet と一致する
- [ ] `recompute_indicators` のチャンク書き出しラッパー
- [ ] `update_pipeline.py --rebuild-from` を Parquet 基点へ変更
- [ ] 復元のカットオフ撤廃（§3.2）＋ docstring 訂正2件
- [ ] `refresh_T3Table.bat` の縮小
- [ ] `test_backfill_price_history.py` — 既存行を書き換えないこと / 取得不足を検出すること
- [ ] `backfill_price_history.py` 新設
- [ ] backend / frontend の全テスト green

### Phase 2 — Sandbox 検証

- [ ] **730日しか無い SQLite の状態で `--rebuild-from T3` を実行し、マスターが壊れないこと**
      （2026-08-29 の事故シナリオの再現テスト）
- [ ] 再計算結果が現行 Parquet と**差分0**であること（充足前。ETF の 2018-2021 を除く）
- [ ] 充足の dry-run で対象銘柄数・取得範囲が妥当なこと
- [ ] 所要時間の計測（充足の yfinance 取得が支配的な見込み）

### Phase 3 — 本番反映（**ユーザー実施**）

- [ ] merge
- [ ] `backup_production_data.py --apply`（**充足の前に必須**）
- [ ] `backfill_price_history.py --apply`
- [ ] `update_pipeline.py --rebuild-from T3`
- [ ] `db_health_check.py --all`
- [ ] **9 study の再最適化**（`stress_bear` 2018-10 窓が有効になるため、結果が変わる）

## 6. 検証プラン / 結果

### 6.1 事故シナリオの再現（本計画の核心）

730日ぶんだけを持つ sandbox SQLite に対して `--rebuild-from T3` を実行する。

| 検査 | 期待 |
| :--- | :--- |
| `sma_200` / `dist_52w_high_pct` の差分（対現行 Parquet） | **0 行** |
| `rs_blue_dot_age` の点灯件数 | 現行と一致 |
| Parquet の行数 | 減らない |

### 6.2 充足の検証

| 検査 | 期待 |
| :--- | :--- |
| 既存行（2018-04 以降）の値 | **1行も変わらない** |
| 追記後の銘柄ごと最古日 | 2017-01-01 近傍（上場日が後の銘柄は上場日） |
| 取得できなかった銘柄 | レポートに列挙される（429 の握り潰し検出） |
| `stress_bear` 2018-10 時点の遡り | 125本 → **約440本**（`ema_200` の残存 28.7% → 約1.25%） |

### 6.3 回帰

- [ ] 日次キャッチアップ（`--rebuild-from` なし）の挙動が変わらないこと
- [ ] `db_health_check --all` の NG が増えないこと（ベースライン: 要対応 0 / 上流待ち 32）

## 7. 途中発生した課題

（未着手）

## 8. スコープ外・残作業

- **`min_periods=1` の是正** — 全指標の値が変わるため別issue。是正すれば「遡り不足は NaN」
  になり、誤った値が出なくなる代わりに既存の最適化 study が全て無効になる
- **rotate のマージのメモリ効率化** — `28982d8` は「壊れずに止まる」までで、
  `pd.concat` のまま。2026-09-01 の OOM は空きメモリ 28.7GB での 1.77GiB 確保失敗＝
  **連続領域の断片化**が原因で、再現性が読めないため優先度は低い
- **`/api/system/info` の `is_production` がファイル名だけで判定** — `STOCKTOOL_ENV=sandbox`
  でも `True` を返し、フロントの非本番バッジが出ない（`api/routers.py` L39）
- **`test_special_removal.py` の TOML テスト2件が絶対パスをハードコード** — 参照先が
  存在せず main でも失敗する
- **2017年より前への遡り** — 本計画は 2017-01-01 まで。それ以前が必要になったら
  同じ充足機能で `--start-date` を変えて再実行できる
