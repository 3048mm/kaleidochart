# T3 日次計算の増分化（保存済み状態からの継承）計画書

- **ステータス**: 🚧 計画レビュー中
- **実施者**: AI エージェント（Claude Opus 5）
- **開始日**: 2026-09-21 / **完了日**: —
- **作業ブランチ**: 未作成（種別 B のためワークツリー＋`--mode write`）
- **対象 issue / 関連ドキュメント**: 本計画で `doc/issue_list.md` に新規起票（§8）。
  関連: `doc/completed/t3_parquet_rebuild_plan.md`（2026-09-04。**再構築経路のみ** Parquet 化）、
  `doc/completed/t5_parquet_rebuild_plan.md`（② T5 リフレッシュの Parquet 化）、
  `doc/in_progress/min_periods_warmup_plan.md`（① `min_periods` 統一。§2.3 の NULL 原則を共有）

## 1. 背景と目的

### 1.1 発見された不具合（2026-09-20 実測）

本番の `rs_roc_ema_200` と `rs_momentum_e200` が **2026-09-15 以降、全銘柄で NULL** になっていた。

| 日付 | `rs_ratio_e200` | `rs_roc_ema_200` | `rs_momentum_e200` |
|---|---|---|---|
| 〜2026-09-14 | 6% | 9% | 10% |
| **2026-09-15〜09-18** | 6% | **100%** | **100%** |

`rs_momentum_rank_e200` は T4 で NULL が最小値として並ぶため、**最新日の 3,303行すべてが rank 0.0**（カテゴリ「個別」2,991件・「テーマ」272件とも100%）になっていた。

利用箇所は表示系のみ（`chart_router.py` の RRG / `dashboard_router.py` / `panel_builders.py`）で、
`backtest_config.toml` に `e200` の参照は無く、バックテスト・最適化への影響は無い。

> [!IMPORTANT]
> **被害はこの2列に留まらない**（2026-09-22・§5-2c の実測で判明）。
> 「末尾1行を全履歴と一致させるのに必要な履歴本数」を全列で測ったところ、
> **SQLite の保持（504営業日）で足りない列が12個**あった:
>
> `ema_50` / `ema_63` / `ema_150` / `ema_200` / `rs_value_e63` / `rs_value_e200` /
> `rs_ratio_e63` / `rs_ratio_e200` / `rs_roc_ema_63` / `rs_roc_ema_200` /
> `rs_momentum_e63` / `rs_momentum_e200`
>
> 必要本数は `ema_200` = 2,400本、**`rs_momentum_e200` は 2,400本でも未収束**。
> NULL になって顕在化したのは2列だけで、**残り10列は「もっともらしいが違う値」が入っている**（§1.4）。

### 1.2 原因

`t3_indicators.py:22-33` の T3 ワーカーは、**生の価格だけを読んで指標系列を毎回ゼロから再計算する**。

```python
query = "SELECT date, open, high, low, close, volume FROM daily_prices WHERE symbol_id = ? ORDER BY date"
df_price = pd.read_sql_query(query, conn, params=(sid,))
df_ind = calculate_indicators(df_price, spy_df)
delta_df = df_ind[df_ind['date'] > t3_max]   # 新しい行だけ残して捨てる
```

保存済みの指標列（`rs_value_e200` / `rs_roc_ema_200` 等）を**入力に一切使っていない**。
そのため**ウォームアップが毎晩リセットされ**、SQLite の保持期間で計算できる列しか値を持たない。

**本番 SQLite の実測（2026-09-20）**:

```
daily_prices: 2024-09-18 〜 2026-09-18 / 504 営業日 / 3,360銘柄
610本以上の履歴を持つ銘柄: 0
```

**必要遡り本数（本番 Parquet で実測。中央値＝最小値＝最頻値なので銘柄によらない構造的定数）**:

| 列 | 必要本数 | 504本で足りるか |
|---|---|---|
| `rs_trend_s200` | 99 | ○ |
| `rs_ratio_e200` | 298 | ○ |
| **`rs_roc_ema_200`** | **511** | **×（7本不足）** |
| **`rs_momentum_e200`** | **610** | **×（106本不足）** |

閾値が一致する。`rs_roc_ema_200` は **511 対 504 で margin がわずか7本**であり、
730暦日に含まれる営業日数は祝日配置で前後するため、**年によって直ったり壊れたりする**。

### 1.3 同じ病気を3回別々に発見している

| 時期 | 発見 | 対処 |
|---|---|---|
| 2026-09-04 | T3 の**再構築**が SQLite 基点 | 再構築だけ Parquet 化 |
| 2026-09-11（②） | T5 の**リフレッシュ**が SQLite 基点 | リフレッシュだけ Parquet 化 |
| 2026-09-20（本件） | T3 の**日次**が SQLite 基点 | 本計画 |

`t3_parquet_rebuild_plan.md` は §2.2 で旧案C を却下する理由に
「**入力が常に全履歴になるので不足行が発生しない**」と書いているが、
**これが成立するのは再構築経路だけ**だった。さらに §7.3 で
「`ema_200` の差分 2026-08-06〜09-01 = 日次 T3 が730日窓で計算した直近行」と
**観測しておきながら**、「再構築が破損を修復する」と読み、
「日次が壊し続けている」とは読まなかった。チェックリストには
「日次キャッチアップの挙動が**変わらない**こと」が受け入れ条件として置かれていた。

**T5 の日次も同じ病気のまま**である（`t5_signals.py:109-117` が `db.query(DailyPrice)` で読む。
`SPY_LOOKBACK_MIN_BARS` のガードは遡り不足の**警報であって治療ではない**）。

### 1.4 NULL にならない列も静かにずれている

`calculate_ema_tv` は毎回 `SMA200` でシードを作り直すため、504本窓の `ema_200` は
全履歴の `ema_200` と一致しない。**本番 Parquet で実測（個別・active・700本以上の2,676銘柄）**:

| 分位 | 乖離率 |
|---|---|
| 中央値 | 0.111% |
| p90 | 0.413% |
| p99 | **1.662%** |

| 閾値 | 該当 |
|---|---|
| 0.5% 超 | 184銘柄（6.9%） |
| 1.0% 超 | 57銘柄（2.1%） |

**15銘柄に1つが 0.5% 超**で、`close > ema_200` の判定を反転させうる。
（最悪の `NVVE` -63% / `MNTS` -28% / `UAVS` -25% は `issue_list.md` P1 の分割記録不一致銘柄と一致。
極端な裾は EMA のシード誤差だけでなく既知の価格異常が増幅されたもの。）

### 1.5 完了時の状態（成功条件）

1. `rs_roc_ema_200` / `rs_momentum_e200` が日次経路で正常に算出される
2. **1銘柄について、IPO 日から現在までの全列・全日付が、Parquet 全履歴から計算した値と一致する**
   （SQLite の保持期間より長い区間でも一致すること。§6）
3. 指標の正しさが SQLite の保持期間に依存しない
4. T3 の全列が特性タイプの分類レジストリに登録済みで、未登録なら CI が落ちる
5. 計算の実装が1本であること（全期間計算と増分計算が同じ関数）

## 2. スコープと設計判断

### 2.1 変更すること

1. **`calculate_indicators(df, state=None)` に統一する** — `state` を渡すと再帰系がシードを
   作り直さず前日値を継ぐ。渡さなければ現行どおり全期間計算（§3.1）
2. **T3 ワーカーを増分呼び出しにする** — 保存済み T3 行から状態を復元し、必要な遡り行だけ読む（§3.2）
3. **指標の特性タイプを仕様化し、機械チェックする**（§3.3）
4. **特性に応じた更新窓を持たせる** — 両側参照列は既存行も書き直す（§3.4）
5. **状態が無い／壊れている場合のフォールバック**（§3.5）
6. `doc/backend_specification.md` に特性タイプ表を追記

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 |
|---|---|
| **案B: SQLite の保持期間を850営業日へ拡張** | **不採用。** 850営業日＝**1,231暦日（約3.4年）**が必要（営業日/暦日 = 0.6904 実測）。DB が 1.69 GB → 約2.85 GB、日次計算 +16%。かつ**「正しさが保持期間に依存する」結合が残る**ため、同じ事故の芽が残る |
| **案C: Parquet から850本だけ読む** | **不採用。** 結合は切れるが EMA のシードを作り直す点は変わらず、**近似解**（シード残存 0.149%）。案Aが厳密解を出せるなら選ぶ理由が無い |
| **案D: 毎晩 Parquet 全履歴で再計算** | **不採用。** 精度は厳密だが日次 2.6分 → **4.5分**（実測）。案Aは **〜0.5分**で同じ精度を出せる。メモリも親プロセスで +1.29 GB 必要 |
| **案C-1: Parquet を銘柄ごとに `filters` で読む** | **論外。** 1銘柄74ms × 3,360 = **249秒（4.2分）**。採る場合は必ず親で一括読み（1.9秒） |
| **T3 実行前の SPY データチェックを追加する** | **不要。** 実測で「SPY に無い日付を持つ行」は **684万行中4行**のみ（`^VIX`/`S5FI`/`S5TH` の休場日、すべて `category='指標'`）。かつ `t3_indicators.py:36` の `date <= spy_latest_date` により、**SPY 取得失敗時は T3 が何も書かない**。実質的なチェックは既に存在する |
| **フライングデータ対策を T3 に入れる** | **不要。** T3 は1銘柄の価格と SPY しか見ないため、他銘柄のフライングデータの影響を受けない。これは横断計算をする T4 固有の問題で、T4 は既に7日遡りで対処済み |
| **`min_periods` の統一（遡り不足を NULL に）** | **本計画では扱わない。** ①（`min_periods_warmup_plan.md`）の担当。ただし①が定める必要本数を本計画の増分実装が満たす必要があるため、**§4-3 で順序を確認する** |

### 2.3 実測値の根拠（2026-09-20〜21）

**日次コスト（3,360銘柄・4並列）**

> [!IMPORTANT]
> **当初の見積もり表には2つの誤りがあった**（2026-09-22〜23 に実測で判明）。
> 案の選択に影響したため、訂正した数値をここに残す。
>
> 1. **案A を「〜0.5分」としたのは誤り。実測 1.41分**（現行の1.65倍）。
>    「保存済み値を継ぐから計算量が減る」と考えたが、増分モードでも WINDOW 列は
>    窓全体で rolling を回すため計算量はほとんど減らない。加えて `indicators`
>    テーブル（67列）の読み出しが新たに加わる
> 2. **案D を「4.5分」としたのは算術の誤り**。1プロセスの合計秒数（254秒＝4.2分）を
>    4並列の値と取り違えていた。正しくは **約1.16分**
>
> つまり「案A が案D より4倍速い」という案A採用の論拠は成立しない。
> **案A を採る理由は速度ではなく、Parquet と SQLite の結合を日次経路に
> 持ち込まずに済むこと**（§3.2 の6つの非対称を回避できる）。

**実ワーカーでの end-to-end 実測（2026-09-23・seed 済み sandbox・40銘柄）**

| 経路 | 1銘柄 | 3,360銘柄 | 4並列 |
|---|---|---|---|
| 現行（＝フォールバック経路） | 60.9 ms | 204.7秒 | **0.85分** |
| **案A 増分** | 100.6 ms | 338.0秒 | **1.41分** |

> [!IMPORTANT]
> **上の 1.41分 は 5-6d の前（67列を読んでいた時点）の値である。**
> 読み出しコストを支配していたのは行数ではなく**列数**で、実際に入力として必要な
> T3 列は33列しかなかった（§7 の 5-6d）。絞り込み後の実測:
>
> | 経路 | 1銘柄 | 4並列 |
> |---|---|---|
> | 現行 | 59.0 ms | **0.83分** |
> | 増分（67列・5-6d 前） | 100.6 ms | 1.41分 |
> | **増分（33列・最終）** | **61.9 ms** | **0.87分** |
>
> **最終的に現行比 1.05倍（+2.4秒）で、実質同等。** 当初「案A は現行の1.65倍遅い」と
> 記録したが、それは列を読みすぎていたことによるものだった。

**得るものは12列の正確さ（§1.1・§1.4）で、対価は日次 +2.4秒。**

**参考: 各案の読み出しコスト（3,360銘柄）**

| 方式 | 時間 |
|---|---|
| 現行 SQLite（1銘柄ずつ全行） | 4.0秒 |
| 案A 価格401行＋指標252行×67列 | 22.4秒（うち指標が18.3秒） |
| 案C-1 Parquet を銘柄ごとに `filters` | **249秒（4.2分）** — 採ってはいけない |
| 案C-2 Parquet 一括読み＋親で切り出し | 1.9秒 |

**5-6d 実測（2026-09-23・sandbox SQLite・400銘柄・実際の `ORDER BY date DESC LIMIT K` クエリ）**

上記の見積もり（252行×67列）は5-4e 以前の想定で、実際の `K = max_lookback()` は
5-4e の zone_break lookback 見直しにより **400**（`ZONE_BREAK_LOOKBACK`）になっている。
実クエリ（`t3_indicators.py` と同一の `SELECT ... ORDER BY date DESC LIMIT K`）で
列数だけを変えて計測した結果:

| 列数 | 1銘柄 | 3,360銘柄換算 |
|---|---|---|
| 変更前（67列。5-6時点の実装） | 46.92 ms | **157.7秒（2.6分）** |
| **変更後（33列。5-6d）** | **4.36 ms** | **14.7秒** |

**約90%削減**（157.7秒 → 14.7秒）。行数は67列版と同じ400行のまま列数だけを
33列に絞った結果であり、§2.3 冒頭で述べた「読み出しコストを支配するのは行数ではなく
列数」という実測（252行×67列 vs 252行×6列で5.9倍差）と整合する。
スクリプト: `tmp/bench_indicators_read_5_6d.py`。

**メモリ（マシン 51.5 GB / 空き 33.7 GB）**

| 案 | ワーカー側（×4） | 親プロセス | ピーク |
|---|---|---|---|
| 現行 | 4.0 MB | — | 〜0.1 GB |
| **案A** | **2.0 MB** | — | **〜0.1 GB** |
| 案C/D | 6.8〜33.7 MB | 1.29 GB | 1.3 GB |

> [!NOTE]
> `merge_timeseries_table` の docstring にある OOM 事故（2026-09-01、`Unable to allocate 1.77 GiB`）は
> **rotate 時の `indicators` マージ（3.01 GB・684万行×70列、float64換算で約3.8 GB）**のもので、
> **案の選択と無関係に毎晩通る共通経路**。T3 の入力方式では回避も悪化もしない。

## 3. 変更内容

### 3.1 `calculate_indicators(df, state=None)`

**単一実装・2つの入口**にする。二重管理を作らないための中核。

- `state=None`: 現行どおり全期間計算（`--rebuild-from T3`・検証オラクル・移行時のシード）
- `state` あり: 再帰系が `initial_sma` を作らず、渡された前日値から `_ema_kernel` を継ぐ

`moving_averages.py:6-16` の `_ema_kernel` は**既にステップ実行のループ**なので、
開始状態を差し替えるだけで両方に使える。

### 3.2 T3 ワーカーの増分化

| 種別 | 必要な入力 |
|---|---|
| 再帰（EMA） | 前日の保存値1行 |
| ローリング窓 | 生値／保存済み中間列の直近 200〜252行 |
| 両側参照 | 直近N行（Nは §5-2 で実測） |

**最大252行**で足り、SQLite の504行に収まる。Parquet の読み出しは不要。

### 3.3 特性タイプの仕様化と機械チェック

分類は本計画の発明ではなく、`t3_parquet_rebuild_plan.md` §7.2 が検証ゲートのために
既に EXACT / LEVEL / CENTERED / COUNTER として作っていたものを**仕様に昇格させる**。

| タイプ | 意味 | 例 |
|---|---|---|
| **(a) 再帰** | 前日の出力値が状態 | `ema_*` / `rs_value_e*` / `rs_roc_ema_*` / `td9` / `rs_*_dot_age` |
| **(b) 窓** | 直近N本の入力が必要・状態なし | `sma_*` / `max_252d` / `rs_ratio_e*` / `rs_momentum_e*` |
| **(c) 両側参照** | **未来のバーで値が変わる** | **現時点で該当列なし**（2026-09-21 実測。§5-2・§7-1） |

**テストで固定する2点**:

1. **T3 の全列が分類レジストリに登録済み** — 未分類の列を足したらテストが落ちる
2. **増分1歩の結果 == 全期間再計算の最終行** — 分類を**誤った**場合もここで落ちる

> [!NOTE]
> **`td9` と `rs_dot_age` は追加の状態列が不要**（実装確認済み）。
> `_td9_kernel`（`volatility.py:11-18`）は `res[i-1]` しか参照せず、
> `compute_rs_dot_age` も前日の age 値が状態。**どちらも既に T3 の列として保存されている**。

> [!IMPORTANT]
> **(c) に該当する T3 列は存在しなかった**（2026-09-21 実測・§5-2）。
> `structure_pivot.py:116-120` の後ろ向きループは確かに未来を見るが、
> `_scan_for_length` が**確定したピボットだけ**を使うため、T3 に落ちる出力は揺れない。
> `zone_break` も前方のみだった。詳細と検算の経緯は §7-1。
>
> **`sp_*` / `zb_*` は追加の状態列も不要**（2026-09-22・§5-2c の実測）。
> 状態機械ではあるが、その状態は**生価格 60〜120本から再構築できる**。
> よって RECURSIVE ではなく **WINDOW（lookback 250、実測＋余裕）**に分類した。
>
> **それでも分類レジストリは残す。** 理由: ①今後追加する指標が (c) に該当しない保証は無く、
> 「現在ゼロ」という事実自体をテストで固定する価値がある ②(a)/(b) の区別は増分実装に必須。

### 3.4 更新窓 — 既存行も書き直す

現行は `date > t3_max` で新しい行しか書かない。**過去の誤った行を直す仕組みがどこにも無い**
（ウィークリーも欠損行しか見ない。§8）。

**当初は特性に応じた更新窓（(c) は直近N日を書き直す）を設計する予定だったが、
§5-2 の実測で (c) に該当する列が無いと分かったため、更新は当日のみでよい。**
未知数だった N が消え、実装が単純になった。

- (a) 再帰・(b) 窓 → **当日のみ**更新（前日値・直近N本から正しく出る）
- (c) 両側参照 → **該当列なし**（§7-1）

> [!NOTE]
> ただし「過去の誤った行を直す仕組みが無い」こと自体は解消しない。
> T4 は `t4_ranks.py:20-23` で**常に7日遡って再計算**しているが、T3 には無い。
> 遡及的な価格修正への対処は §8 に課題として起票する。

### 3.5 フォールバック

以下では `state=None`（全期間計算）に落とす:

- 保存済み T3 行が存在しない（新規上場・オンボード直後）
- 状態列が NULL
- T2 の価格が遡及修正された銘柄（§8 のウィークリー課題と連動）

## 4. ユーザー確認事項

**2026-09-21 に判断済み。**

| # | 論点 | **判断** |
|---|---|---|
| **4-1** | 案A〜D のどれで進めるか | **案A（増分）。** 「プロスコンスで明確に差がある案Aで進める」 |
| **4-2** | (5) の検証範囲 | **1銘柄でよい。Parquet を使ってよい**（§6） |
| **4-3** | ①（`min_periods` 統一）との順序 | **本計画が先**（2026-09-21 判断）。理由: ①を先にやると検証した値が翌日から劣化し続ける（日次経路が壊れたままのため）。また①の A-full は必要遡りを最大810本へ伸ばすので、**日次が正しく遡れる状態を先に作る**必要がある。①の昇格時のフル再計算に本計画の移行シードを相乗りさせる |

## 5. 実装順序と進捗チェックリスト

- [ ] **5-1** ワークツリーを作成し `--mode write` でプロビジョニング（種別 B）
- [x] **5-2** **(c) 両側参照列の落ち着き窓を実測** — **完了（2026-09-21）。該当列ゼロ**。
      12銘柄 × 40切断点 = 480回の比較で全列 0。陽性対照で測定側の妥当性も確認済み（§7-1）。
      スクリプト: `tmp/measure_settling_window.py`
- [x] **5-2c** **各列の必要履歴本数を実測**（前方切り詰め）— **完了（2026-09-22）**。
      8銘柄 × 各2,441本。`sp_*`/`zb_*` は 60〜120本で収束（→ WINDOW 分類の根拠）。
      **504本で足りない列が12個**あることが判明（§1.1）。スクリプト: `tmp/measure_lookback.py`
- [x] **5-2b** 上記スクリプト2本を**テストへ昇格**（2026-09-23 完了）—
      `test_calculate_no_future_dependence.py` / `test_incremental_lookback_sufficiency.py`。
      合成データで5テスト・2.3秒。**陽性対照つき**（`pivot_strength_low()` は実際に
      未来のバーで値が変わるので、ハーネスが機能していることを担保できる）。
      なお lookback 側は当初「末尾 lookback 本で再計算して比較」と指示したが、
      **5-3b で `lookback` の意味が変わっており成立しない**（再帰列の lookback は
      前日値を継ぐ前提の追加入力本数）。`max_lookback()` を**マージン無し**で
      検証する形に改めた（既存の等価性テストは MARGIN=10 なので、こちらの方が厳しい）
- [x] **5-3** 特性タイプの分類レジストリを作成し、**全列が登録済みであることを固定するテスト**（red → green）
      → 初版は3点の設計ミスがあり、**5-3b で再設計**（`ColumnSpec` を `prev_self`/`inputs`/`lookback` に
      分解。詳細は §7 および `backend/indicators/incremental_state_registry.py` のモジュール docstring）
- [x] **5-4** `calculate_indicators(df, state=None)` へのシグネチャ拡張（`state=None` の挙動は現行と完全一致）
      — **完了（2026-09-22）**。`calculate_ema_tv`/`calc_moving_averages`/`calc_volatility`
      （ATR は ta ライブラリ依存を排し自前 Wilder 実装に置換、TD9 も対応）/
      `calc_relative_strength`（rs_value_eN・rs_roc_ema_N・rs_macd_signal_21・
      rs_blue_dot_age/red_dot_age）が `state` を受け取れるよう拡張。
      `state=None` は既存テスト1837件（旧1835+新2）全パスでビット単位一致を確認。
      **rs_blue_dot_age/red_dot_age の warmup ガード（想定される難所1点目）は
      `use_state` フラグでバイパスする設計を採用**（`_rs_dot_age_kernel`）。詳細は §7。
- [x] **5-5** **「増分1歩 == 全期間再計算の最終行」の等価テスト**を追加（red → green）
      — **完了（2026-09-22）。ただし4列（`rs_roc_ema_63`/`rs_momentum_e63`/
      `rs_roc_ema_200`/`rs_momentum_e200`）は未解決のまま KNOWN_UNCONVERGED として
      除外**（詳細は §7 の「5-4/5-5: rs_roc_ema_N の多段依存が収束しない」）。
      `backend/tests/indicators/test_calculate_incremental_equivalence.py`
- [x] **5-4b** 5-4/5-5 で残った4列の未収束を解消 — **完了（2026-09-22）**。
      §7「5-4b: 増分呼び出しの入力契約を拡張して4列の未収束を解消」参照。
      §7 の対処案1（保存済み T3 列を df 自体に供給する経路を別途用意する）を採用。
      `KNOWN_UNCONVERGED_COLUMNS` の除外を撤廃し、**67列すべてが rtol=atol=1e-9
      で厳密一致**することを確認（除外ゼロ）。
      変更ファイル: `backend/indicators/incremental_merge.py`（新規）、
      `backend/indicators/moving_averages.py`・`volatility.py`・
      `relative_strength.py`（RECURSIVE型列の前日シードをdf自身の供給済み履歴から
      直接導出し、最終行だけを1歩計算した上で供給済み履歴とマージする設計に変更。
      `calculate_ema_tv`/`_td9_kernel`/`_atr_wilder_kernel`/`_rs_dot_age_kernel`は
      式そのものは変えず「どこからシードするか」「どの行を計算するか」だけを
      パラメータ化）、`backend/indicators/calculate.py`（docstring更新のみ）、
      `backend/indicators/incremental_state_registry.py`（docstring更新のみ）、
      `backend/tests/indicators/test_calculate_incremental_equivalence.py`
      （増分呼び出しの入力契約を「生価格K+1本」から「生価格K+1本＋保存済み
      T3中間列K本」に変更するテストへ全面改訂）。
- [x] **5-4c** `zone_break` 系4列（`zb_ssl`/`zb_bsl`/`is_zone_break_bull`/
      `is_zone_break_weak`）の必要履歴が非有界と判明したことへの対応 — **完了
      （2026-09-22・ユーザー判断: 案A採用）**。詳細は §8「`zone_break` の必要履歴が
      有界でない」参照。lookback を `HOT_WINDOW_BARS`（504）に設定し、等価性テストの
      判定基準をこの4列だけ「504本での全期間計算と一致」に変更（除外はしない）。
      仕様書にも厳密解でないことを明記。
      変更ファイル: `backend/indicators/incremental_state_registry.py`
      （`HOT_WINDOW_BARS` 定数を追加、対象4列の lookback を250→504に変更、
      note に非有界性と実測分布を明記）、
      `backend/tests/indicators/test_incremental_state_registry.py`
      （lookback上限テストを対象4列除外＋専用テストに分離、代表値テストを更新）、
      `backend/tests/indicators/test_calculate_incremental_equivalence.py`
      （63列の厳密一致テストから対象4列を除外し、`TestZoneBreakHotWindowEquivalence`
      で「504本での全期間計算」との一致を別途検証する設計に変更）、
      `doc/backend_specification.md`（3.4節 zone_break の説明に厳密解でない旨と
      実測分布を追記）。
- [x] **5-4d** オーケストレーターの検収で見つかった、増分経路が実データ（SQLite
      経由）で `ZeroDivisionError` になる不具合を修正 — **完了（2026-09-22）**。
      詳細は §7「5-4d: 増分経路が実データで ZeroDivisionError になる不具合」参照。
      変更ファイル: `backend/indicators/incremental_merge.py`
      （`normalize_supplied_dtypes` を新規追加。`INDICATOR_COLUMN_REGISTRY` の
      全列を機械的に走査し `pd.to_numeric(errors='coerce')` でfloat64へ正規化）、
      `backend/indicators/calculate.py`（`calculate_indicators` の入口で
      `state` truthy時のみ `normalize_supplied_dtypes` を呼ぶよう追加。`state=None`
      の分岐には触れていない）、`backend/indicators/volatility.py`
      （`sma50_atr_mult` の除算を `np.where` の評価順に依存しない形に変更。
      `.mask(==0)` で0の分母を事前にNaN化してから除算する。計算結果は不変）、
      `backend/tests/indicators/test_calculate_incremental_equivalence.py`
      （`TestSuppliedHistoryDtypeRegression` を新設。実データに近い条件
      ――`calculate_indicators(state=None)`の戻り値そのもの（object dtype）を
      供給履歴として使い、かつその銘柄自身の窓の先頭（`atr_pct_14`のWilder平滑化
      シードがリテラル0になる先頭13本）を含む――で再現する回帰テスト4件を追加。
      `_build_full_and_incremental`に`state_idx`引数を追加（既定は従来どおり
      末尾settle位置。5-4dのテストのみ`state_idx=0`を明示指定）。
      pytest全件 1843 passed, 1 skipped（旧1839+新規4）。
- [x] **5-4e** `zone_break` 系4列の lookback を見直し、SQLite保持行数に対する
      マージンを確保 — **完了（2026-09-22・ユーザー判断）**。詳細は §7「5-4e:
      zone_break系のlookbackがHOT_WINDOW_BARSちょうどでマージンゼロだった件」参照。
      lookback を `HOT_WINDOW_BARS`(504) から `ZONE_BREAK_LOOKBACK`(400) に変更し、
      `max_lookback()+1`(401) が `HOT_WINDOW_BARS`(504) に対して約103行のマージンを
      持つようにした。
      変更ファイル: `backend/indicators/incremental_state_registry.py`
      （`ZONE_BREAK_LOOKBACK` 定数を新規追加。`HOT_WINDOW_BARS - _ZONE_BREAK_MARGIN_BARS`
      として算出し、マージンが定数の引き算からコード上で読み取れる形にした。
      対象4列の lookback を `HOT_WINDOW_BARS` → `ZONE_BREAK_LOOKBACK` に変更、
      note を更新）、
      `backend/tests/indicators/test_incremental_state_registry.py`
      （代表値固定テストの期待値を `ZONE_BREAK_LOOKBACK` に更新、
      `test_max_lookback_plus_oneがHOT_WINDOW_BARSに対してマージンを持つ` を新設し
      `max_lookback()+1 <= HOT_WINDOW_BARS` を不変条件として固定）、
      `backend/tests/indicators/test_calculate_incremental_equivalence.py`
      （`TestZoneBreakHotWindowEquivalence` の比較窓を `HOT_WINDOW_BARS` →
      `ZONE_BREAK_LOOKBACK` に変更。`test_incremental_does_not_raise_when_supplied_rows_are_fewer_than_max_lookback_plus_one`
      は「SQLiteの保持行数(504)がmax_lookback()+1(505)より1本少ない」という
      5-4d時点の実運用シナリオが前提だったが、マージン確保後は成立しなくなったため、
      `HOT_WINDOW_BARS` への依存を外し `max_lookback()` を直接1本下回る供給行数に
      差し替えて防御的性質のテストとして維持）、
      `doc/backend_specification.md`（3.4節の警告ブロックを400本採用・根拠・
      マージンの説明に更新）。
      pytest全件 1844 passed, 1 skipped（旧1843+新規1: マージン不変条件テスト）。
- [x] **5-6** T3 ワーカーを増分呼び出しへ変更（§3.2）＋フォールバック（§3.5）
      — **完了（2026-09-22）**。詳細は §7「5-6: T3 ワーカーの増分化」参照。
- [x] **5-6b** オーケストレーターの検収で判明した「フォールバックが無言」問題への対処
      — **完了（2026-09-22）**。詳細は §7「5-6b: フォールバックの可視化」参照。
- [x] **5-6c** **ウォームアップ本数による NULL 検査**（ユーザー提案）— **実装済み・閾値は未確定**。
      「演算上必要な日数がある銘柄なら、その列は NULL にならない」という検査。
      `ColumnSpec.warmup_bars` と `db_health_check --check-warmup-nulls`（opt-in）を追加。
      本番120銘柄の実測で67列中64列が構造的な定数（`sp_pivot`/`sp_hl`/`sp_counter` は
      イベント駆動のため例外）。**全3,362銘柄に当てたところ偽陽性ゼロにならず、閾値調整が残る**（§7-5）
- [x] **5-6d** T3 ワーカーの読み出しコスト削減（`indicators` から読む列を67列→33列に）
      — **完了（2026-09-23）**。詳細は §7「5-6d: 読み出しコストは行数より列数が支配的」参照。
- [ ] **5-7** 更新窓の実装（§3.4。(c) は 5-2 で決めた N 日を書き直す）
- [ ] **5-8** pytest 全件パス
- [ ] **5-9** **§6 の受け入れ検証**（1銘柄・Parquet 全履歴と全列全日付一致）
- [ ] **5-10** sandbox で日次を通し、`db_health_check.py --all --check-nulls` を通す
- [ ] **5-11** 日次の所要時間を実測し §2.3 の予測（〜0.5分）と照合
- [ ] **5-12** `doc/backend_specification.md` に特性タイプ表を追記
- [ ] **5-13** `doc/architecture.md` に「計算は全履歴基点、SQLite は API 用の派生物」を明記
      （3回繰り返している再発見を止めるため）
- [ ] **5-14** `doc/issue_list.md` へ §8 の2件を起票
- [ ] **5-15** `/code-review` をブランチ単位で1回通す（種別 B）
- [x] **5-15b** `/code-review` 1回目の指摘4件に対応（2026-09-23）— 欠陥/ウォームアップの分類、
      SPY の構造的 NULL 除外を health check と共通化、ATR シードの NaN 挙動、
      `prev_self_seed` の契約。**ただし分類の判定方式に回帰を作り込んだ（5-15c で修正）**
- [x] **5-15c** `/code-review` 2回目の指摘3件に対応（2026-09-23）— §7-6 参照
- [ ] **5-16** merge →（移行シードとして）T3 フル再計算 → 昇格。
      **フル再計算で状態を seed してからでないと増分経路は発動しない**（検収で実証済み。
      §7「5-6b」参照）。現行の本番 SQLite は `rs_roc_ema_200` 等12列が既に不正確な値
      （NULLまたは遡り不足）を持っており、増分条件（直近K行のRECURSIVE列にNaNが無い）を
      満たせないため、5-16 のフル再計算を経ずに日次だけ流しても増分経路には決して
      入らない（フォールバックし続ける）。順序は必ず「フル再計算 → 以降の日次で増分」。
- [ ] **5-17** 計画書を `doc/completed/` へ移動

### 作業中メモ

未着手。着手時の注意:

- **5-2 が設計の前提**。(c) の窓が決まらないと §3.4 が書けない
- **§4-3（①との順序）が未確認**。移行時のフル再計算を①と束ねられるなら、再構築が1回で済む
- 本番の `rs_roc_ema_200` / `rs_momentum_e200` は 2026-09-15 以降 NULL のまま。
  **本計画の昇格（フル再計算）で初めて修復される**

## 6. 検証プラン / 結果

### 受け入れ検証（§1.5-2）

**1銘柄を選び、IPO 日から現在までの全列・全日付**について、以下の2つを突き合わせて一致を確認する。

1. **正（オラクル）**: Parquet の全履歴から `calculate_indicators(df, state=None)` で計算した値
2. **被験**: 増分経路が実際に SQLite へ書いた値

**SQLite の保持期間（504営業日）より長い区間でも一致すること**が要件。
`rs_momentum_e200`（要 610本）が一致することが本計画の眼目。

銘柄の選定条件: 履歴が 810本以上（A-full 後の最大ウォームアップを満たす）、
`category='個別'`、分割補正の履歴が無いもの。

### 検証結果（2026-09-23・sandbox）

**手順**: sandbox で `--rebuild-from T3 --skip-fetch` を完走させて状態を seed し
（5-16 昇格手順のリハーサルを兼ねる）、その状態から `_calculate_t3_worker` を呼んで
Parquet 全履歴計算と突き合わせた。

**(1) seed の効果** — 本番で 2026-09-15〜18 が全銘柄 NULL だった2列が解消した:

| 列 | 再計算前 | 再計算後 |
|---|---|---|
| `rs_roc_ema_200` | 3,331/3,331（100%） | 438/3,331（13%） |
| `rs_momentum_e200` | 3,331/3,331（100%） | 471/3,331（14%） |

残る13〜14%は履歴が511/610本に満たない銘柄の正当な NULL で、
壊れる前の水準（約10%）と整合する。

**(2) 増分経路の発動と一致** — seed 後の状態で個別25銘柄を検証:

```
増分経路: 25 / フォールバック: 0
Parquet 全履歴と比較: 25銘柄 / 全列の不一致: 0
```

**これが本計画で初めての「実運用条件で増分計算が正しく動く」証拠**である。
それ以前の検証は合成データと手組みの DataFrame にとどまっていた。

**(3) seed 前は増分が一度も発動しない**ことも確認済み（本番相当のデータでは
`rs_roc_ema_200` が NULL で §3.5 の条件を満たさないため）。
昇格時はフル再計算が先であることが必須（§5-16）。

### 6.1 反証 — この結論が誤りだとしたら、何が観測されるはずか

- **この結論が誤りだとしたら観測されるはず**:
  増分経路の値が全期間計算と一致しない。特に **(b) 窓系で必要行数を読み誤っていれば
  先頭付近が NaN になる**か、**(c) 両側参照で更新窓が短ければ末尾N本が古い値のまま残る**。
  また、状態の持ち越しを誤ると **EMA 系が半減期約70営業日で緩やかにずれる**（1日では見えない）。
- **独立経路での確認**: **未実施（計画段階）**。実施時は以下による:
  - オラクルは **Parquet（コールドマスタ）**、被験は **SQLite（ホットキャッシュ）**で、
    データ源が別（同一情報源に依存しない）
  - EMA の緩やかなずれは1日では見えないため、**全日付での一致**を要件にする（最終行だけ見ない）
  - 加えて、`ema_200` の乖離率を §1.4 と同じ方法で再測し、**中央値 0.111% → 0%** になることを確認する

### 6.2 転記の完全性

- **転記元**: 2026-09-20〜21 の会話（案A〜Dの比較・実測）
- **元の件数**: ユーザー提示の案A方針 5項目 ＋ 私の補強 4項目 ＝ 9項目
- **本計画書の件数**: 9項目（方針5 = §2.1-1〜4・§3.4／補強4 = §3.3のテスト2点・§3.1・§3.5）
- **差分の説明**: なし（全項目を転記済み）

## 7. 途中発生した課題

### 5-3 → 5-3b: 分類レジストリの再設計（2026-09-22）

5-3 の初版実装は、レビューで以下3点の誤りが指摘され、実測に基づいて再設計した。

1. `atr_14` は `ta` ライブラリの Wilder 再帰平滑化であり、当初「単純窓ではないので要確認」と
   していたのは誤り。**RECURSIVE で正しい**
2. `sp_*` / `zb_*` は状態機械だが、**前方切り詰め実測（8銘柄・rtol=1e-9）で直近60〜120本の
   生価格があれば全履歴と一致する**ことが分かったため、**WINDOW に再分類**（RECURSIVE ではない）
3. RECURSIVE 列にも非再帰の入力（当日〜数日分）が要る（例: `atr_14` の TR 算出に前日 close が
   要る、`rs_roc_ema_N` の ROC 算出に14日前の `rs_ratio_e{N}` が要る）ため、
   `state_columns`（継ぐ列名のみ）を廃止し、`prev_self`（bool）＋`inputs`（参照列）＋
   `lookback`（inputs から何行必要か）の3フィールドに分解した

lookback の単位も「0=履歴不要」から**「行数（当日のみなら1）」**に統一した。
また旧版の `rs_trend_s200`(99) / `rs_ratio_e200`(298) / `rs_roc_ema_200`(511) /
`rs_momentum_e200`(610) という実測値は「**ゼロから再計算する場合**」の値であり、
RECURSIVE 列（`rs_value_eN`・`rs_roc_ema_N`）を保存済み前日値から継ぐ5-3b設計では
使わない（継いだ結果、実際の lookback は N や 15 など大幅に小さくなる）。
全67列で lookback が確定し、WINDOW 型の最大値は252（SQLiteの504行以内）。
詳細・実測の根拠は `backend/indicators/incremental_state_registry.py` のモジュール docstring。

### 5-4/5-5: rs_roc_ema_N の多段依存が収束しない（2026-09-22・要オーケストレーター判断）

**実装依頼プロンプトが「想定される難所2点目」として挙げていた懸念が、実測で確認された
（設計判断が必要な未解決事項）。**

`rs_roc_ema_63`/`rs_roc_ema_200`（および下流の `rs_momentum_e63`/`rs_momentum_e200`）は、
レジストリが宣言する lookback（15 / 200）だけを満たす増分ウィンドウ（`K = max_lookback()
+ 50 = 302`）では、増分1歩の結果が全期間再計算に収束しない。

**原因**: `rs_roc_ema_N` は「RECURSIVE(EMA) の入力が WINDOW(`rs_ratio_eN`) で、その WINDOW
の入力がさらに RECURSIVE(`rs_value_eN`)」という二重の入れ子。`rs_value_eN` 自体は state
から1歩で厳密に継続できる（実測: df 全体で誤差0）が、`rs_ratio_eN` は直近N行の
`rs_value_eN` を要する WINDOW 型のため、state 境界の直後（最大 N-1 行）は df 内で
「窓」が育つまで不正確な値になる。この不正確な `rs_ratio_eN` が `rs_roc_ema_N` の
再帰ステップに（14日ROC経由で）混入すると、α=2/(N+1) が小さい（N=200で約0.01）ため
半減期が長く、K=252+余裕では数千行分の「汚染」を解消しきれない
（実測: K=2000でも絶対誤差~5e-8残る。K=302では `rs_momentum_e200` の誤差が絶対値1.93、
符号反転）。

`min_periods=n`（フルウィンドウ必須化）で「汚染」を「フリーズ」に置き換える対策も
試したが、フリーズも同様に減衰が遅く改善しなかった（実測: K=700で `rs_momentum_e200`
誤差0.019、対策なしのK=700での誤差0.012と同程度）。**revert 済み**（差分に残っていない）。

**対処には以下のいずれかが要る（本タスクの範囲外・設計判断が必要）**:
1. `rs_ratio_eN`/`rs_roc_ema_N` の「直近N行」を生価格からの再構築ではなく、保存済み T3 列
   （`rs_value_eN`/`rs_ratio_eN` の履歴）から読む経路を別途用意する
   （実装依頼プロンプトが「difficulty 2」として想定していた論点そのもの）
2. `rs_value_eN` の state 継続元の日付を `rs_roc_ema_N` とは別に（N日分先行させて）持つ、
   列ごとに異なる「as of 日付」を許容する多段 state 設計にする
   （`state` を「単一の前日」から「列ごとの基準日」に拡張する必要があり、
   T3 ワーカー（5-6）が「昨日の保存行1行」ではなく複数の履歴日を読む必要が出る）
3. `rs_momentum_e200` 自体を退役・再設計する（§8 に既存候補あり。表示専用で
   戦略未使用のため実害は小さい）

**5-5 のテストでの扱い**: 上記4列を `KNOWN_UNCONVERGED_COLUMNS` として明示的に除外し、
「非NaNの値が出る（配線・分類自体は壊れていない）」ことのみ確認。残り63列は
厳密一致（rtol=atol=1e-9）を固定。**「全テストがパスすること」の制約を満たしつつ、
未解決の事実を隠さない**ための設計（詳細はテストファイルのモジュール docstring）。

**5-6（T3 ワーカー実装）着手前に、上記1〜3のいずれで進めるかオーケストレーターの
判断が必要。** 現状のまま 5-6 に進むと、`rs_roc_ema_63/200`・`rs_momentum_e63/200`
（表示専用・戦略未使用）が増分経路で不正確な値のまま日次更新され続ける
（現状の「日次で毎回SMA再シード」より改善するとは限らない）。

### 5-4b: 増分呼び出しの入力契約を拡張して4列の未収束を解消（2026-09-22）

**オーケストレーター判断: 上記の対処案1（保存済み T3 列を df 自体に供給する経路を
別途用意する）を採用。**

**根本原因の再診断**: 5-4/5-5 の実装は「`state` をスカラー辞書とし、増分ウィンドウ
K本の先頭で再帰系をシードして窓全体（K本）を再帰的に歩き直す」設計だった。この
設計だと `rs_ratio_eN`（WINDOW型。`rs_value_eN` の直近N本のZ-score）が増分
ウィンドウ内でしか rolling 窓を作れず、窓がN本育つまでの区間（最大N-1行）では、
本来より小さい窓で計算された不正確な値になる。この不正確な `rs_ratio_eN` が
`roc`（14日ROC）経由で `rs_roc_ema_N` の再帰ステップに混入すると、α が小さい列
（N=200で約0.01）では半減期が長く、増分ウィンドウをいくら伸ばしても（K=2000でも）
汚染が解消しきらなかった。**「K を増やしても解決しない」ことは前任者がK=2000で
確認済みであり、これは正しい診断だった**（問題は窓の長さではなく、
「履歴の中間値を再計算していること」自体にあった）。

**採用した設計**: 増分呼び出しの入力を「生価格 K+1 本」から「生価格 K+1 本 ＋
保存済み T3 中間列 K 本」に拡張した。呼び出し元は、直近 K+1 行の DataFrame
（`df`）を渡す。生の価格列（open/high/low/close/volume）は全行に値があるが、
**T3 の計算列（`indicators` テーブル相当の列）は行 0..K-1 に保存済みの実値が
入っており、最終行（K）だけが NaN**（＝これから計算する日）という契約にした。

この契約のもとで:

- **RECURSIVE 型列**（`ema_*`・`rs_value_eN`・`rs_roc_ema_N`・`td9`・`atr_14`・
  `rs_macd_signal_21`・`rs_blue/red_dot_age`）は、供給された行 K-1（既に実値が
  入っている）をシードにし、**最終行 K だけを1歩計算**する。計算式自体は
  `state=None` と同じ関数（`_ema_kernel`/`_td9_kernel`/`_atr_wilder_kernel`/
  `_rs_dot_age_kernel`）を使い、「どこからシードするか」（0行目 or K-1行目）と
  「どこまで計算するか」（全行 or 最終行のみ）だけをパラメータ化した
  （単一実装を維持）。計算結果を供給済みの履歴（行0..K-1）とマージする処理は
  `backend/indicators/incremental_merge.py` の `finalize_incremental_column`
  に切り出し、全RECURSIVE型列で共通利用する。
- **WINDOW 型列**（`rs_ratio_eN`・`rs_momentum_eN`・`rs_trend_sN`・`sma_*` 等）は
  **変更不要**。RECURSIVE型列側でマージ済み（行0..K-1が供給済みの実値、行Kが
  新規計算値）になった入力に対して、従来どおりの rolling 計算を適用するだけで
  正しい値になる（「窓が増分ウィンドウ内でしか育たない」問題は、RECURSIVE型列を
  毎回再構築するのをやめたことで構造的に消える）。

この設計変更により、`rs_ratio_eN` の rolling 窓は常に「保存済みの実値」から
組み立てられるため、`roc` に汚染された値が混入することがなくなり、収束を待つ
必要がなくなった（K はレジストリの宣言する lookback を満たすだけで厳密一致する。
テストでは `K = max_lookback() + margin(10)` を使用。理論上は margin=0 でも
足りるはずだが、境界の丸め誤差を避けるための保守的な余裕）。

**結果**: `KNOWN_UNCONVERGED_COLUMNS` による4列の除外を撤廃し、
**67列すべてが rtol=atol=1e-9 で厳密一致**することを実測で確認した
（`test_incremental_matches_full_recompute_for_all_columns`）。

**5-6（T3 ワーカー実装）への申し送り**: 5-6 では、T3 ワーカーが SQLite の
`indicators` テーブルから直近 `max_lookback()` 本の保存済み行（全列）を読み、
生価格の新規行と結合した DataFrame を `calculate_indicators(df, spy_df,
state=True)` に渡す実装にする必要がある（単に「前日の行」だけを渡す旧設計では
不十分）。`recursive_column_names()`（レジストリ）で「df に供給すべき列」を
機械的に列挙できる。

### 5-4d: 増分経路が実データで ZeroDivisionError になる不具合（2026-09-22）

オーケストレーターの検収（実データ・SQLite sandbox 経由）で、多数の銘柄で
`ZeroDivisionError: float division by zero`（発生箇所: `volatility.py`
`sma50_atr_mult` の計算）が見つかった。5-5 の等価性テストは合成データのみで
検出できていなかった。

**原因は2つの重なり**:

1. `calculate_indicators` の戻り値は、末尾（`calculate.py` の
   `df.replace({np.nan: None})`）で NaN を None に変換している。これは
   **同じ DataFrame 内の他の列に NaN があれば、当該列自体に NaN が無くても
   ほぼ全列が object dtype になる**という pandas の仕様（実測: 67列中58列。
   `open`/`high`/`low`/`close`/`volume` のような生価格列すら object になる）。
   増分呼び出しの入力契約（5-4b）はこの戻り値をそのまま「供給済み履歴」として
   使うため、object dtype がそのまま増分計算の入力に混入する。
2. `atr_14`（Wilder 再帰平滑化）は先頭 `window-1`（=13）本がリテラルな 0.0
   （`_atr_wilder_kernel` の `np.zeros` 初期化）になる。SQLite は504行しか
   保持しないため、増分呼び出しが「利用可能な行すべて」を供給する限り、
   その窓は必ずその銘柄自身の最古の行（＝この 0.0 が並ぶ区間）を含む。

object dtype の列を `np.where` の分岐に渡すと、**`np.where` は全分岐を評価する
ため、ガードで弾かれるはずの0除算が Python のスカラー演算として実行され
`ZeroDivisionError` になる**（numpy 配列同士の演算なら0除算は inf/nan になる
だけで例外にならない）。5-5 の等価性テストは `state_idx > 500`（十分に settle
した位置）からしか供給履歴を切り出しておらず、この「窓の先頭」を含んでいな
かったため検出できなかった。

**対処**:

1. `incremental_merge.py` に `normalize_supplied_dtypes` を追加し、
   `calculate_indicators` の入口（`state` truthy 時のみ）で
   `INDICATOR_COLUMN_REGISTRY` の全列を機械的に float64 へ正規化する。
   `is_zone_break_bull`/`is_zone_break_weak`（DB上はBoolean）も対象に含めるが、
   これら2列は `calculate_indicators` 内で生価格のみから毎回無条件に
   上書きされる（どの計算の入力にもならない）ため、floatへ丸めても計算結果に
   影響しない。
2. `volatility.py` の `sma50_atr_mult` 計算を、`np.where` の評価順に依存しない
   形に変更した。除算の前に `.mask(denominator == 0)` で0の分母をNaN化して
   から割る（マスクで弾かれる行は元々結果を使わないため、計算結果自体は不変）。
   これは dtype 正規化（1）が将来再び破られた場合の防御でもある。

**回帰テスト**（`test_calculate_incremental_equivalence.py`
`TestSuppliedHistoryDtypeRegression`）は、合成データを組み立てるのではなく
**`calculate_indicators(state=None)` の戻り値そのもの**（object dtype に
なる実際の挙動）を供給履歴として使い、かつ `state_idx=0`
（`_build_full_and_incremental` に新設した引数）でその銘柄自身の窓の先頭
（`atr_pct_14` のリテラル0区間）を含めることで、実データと同じ条件を
再現している。修正前のコードに対してこの4テストのうち2件が
`ZeroDivisionError` で red になることを確認済み（fix適用前後で目視確認）。

**K が SQLite の保持行数（504）を超える点の確認（5-6 への申し送り）**:
`max_lookback()` は `HOT_WINDOW_BARS`（=504。zone_break系4列の lookback）と
同値のため、増分呼び出しが理論上要求する供給行数 `K+1=505` は SQLite の保持
上限504を1本超える。実測で確認した結果、`calculate_indicators` は供給行数が
`max_lookback()+1` に満たなくてもクラッシュしない（WINDOW型列は
`min_periods` により行数不足でも例外にならず単に精度が落ちるだけ、
RECURSIVE型列は `prev_self_seed` が `len(df) >= 2` しか要求しないため）。
504行・20行・5行・2行での実行を個別に確認済み（いずれも例外なし）。
504行ちょうどのケースは
`test_incremental_does_not_raise_when_supplied_rows_are_fewer_than_max_lookback_plus_one`
として回帰テスト化した。**コード側の対処は不要と判断**——5-6 の T3 ワーカーは
計画書の記述どおり「K+1行ちょうど」ではなく「利用可能な行すべて（最大
K+1行）」を読む実装にすればよい（`calculate_indicators` 側はその行数を
そのまま受け入れる）。

### 5-4e: zone_break系のlookbackがHOT_WINDOW_BARSちょうどでマージンゼロだった件（2026-09-22）

5-4c で `zb_ssl`/`zb_bsl`/`is_zone_break_bull`/`is_zone_break_weak` の lookback に
`HOT_WINDOW_BARS`（504。SQLiteの保持行数の実測値）をそのまま設定していたが、
これは §7「5-4d」で申し送った通り `max_lookback() + 1 = 505` が保持行数504を
**1行超える**構成であり、**マージンがゼロ**だった。5-4d 時点では
「`calculate_indicators` 側は供給行数不足でもクラッシュしない」ことを確認して
コード側の対処を見送ったが、これは「クラッシュしない」ことの確認であって
「正しい値になる」ことの確認ではない。730暦日に含まれる営業日数は祝日配置で
年によって500〜505程度に揺れるため、保持行数が504を下回る年には
`zb_*` 系の lookback 要求（505本）を満たせない状態が発生しうる。

**ユーザー判断（2026-09-22）**: 「zb_* の lookback は減らしてもいい。ちゃんと
SQLite の保有数からマージンをつけて」。

**対処**: 300銘柄実測（5-4cで既出、離散点 120/250/400/600/900/1300/1800/2400
でのみ測定）を再検討したところ、**400と600〜2,400の間で精度が変わらない**
——400本で全履歴と一致しなかった銘柄（`zb_ssl`2/300・`zb_bsl`3/300・
`is_zone_break_weak`1/300）は2,400本まで遡っても一致しなかった（測定点の間に
該当銘柄が無く、収束していない）。したがって400本より大きくしても得るものが
無く、読み出し量が増えるだけと判断し、**lookbackを400に変更**した。

`ZONE_BREAK_LOOKBACK = HOT_WINDOW_BARS - _ZONE_BREAK_MARGIN_BARS`
（504 - 104 = 400）という形で定義し、マージンがコード上の引き算から読み取れる
ようにした。結果、`max_lookback() + 1`（401）は `HOT_WINDOW_BARS`（504）に対して
**103行のマージン**を持つ。

**テストでの不変条件化**: `test_incremental_state_registry.py` に
`test_max_lookback_plus_oneがHOT_WINDOW_BARSに対してマージンを持つ` を新設し、
`max_lookback() + 1 <= HOT_WINDOW_BARS` を固定した。将来どれかの列の lookback を
引き上げてこの不変条件が壊れたら、このテストが検出する。

**5-6（T3ワーカー実装）への申し送りの更新**: §7「5-4d」の申し送りにあった
「SQLiteの保持行数504では`K+1=505`本に1本足りないため、ワーカーは『利用可能な
行すべて（最大K+1行）』を読む設計にする」という前提は、5-4e の見直しにより
**基本的には発生しなくなった**（`K+1=401` に対しSQLiteは504本保持しており
約100行の余裕がある）。ただし新規上場銘柄など保有履歴がそもそも少ないケースは
引き続き起こりうるため、「利用可能な行すべてを読む」設計自体は変更不要
（`calculate_indicators` は行数不足でもクラッシュしないことは5-4dで確認済み、
5-4eでもこの防御的性質のテストは維持している）。

### 5-6: T3 ワーカーの増分化（2026-09-22）

`t3_indicators.py` の `_calculate_t3_worker` を、5-4b で確定した入力契約
（生価格 K+1 本 ＋ 保存済み T3 中間列 K 本、K=`max_lookback()`）どおりに
増分呼び出しを組み立てる実装に変更した。

**分岐条件**（§3.5 のフォールバックをそのまま実装）:

1. `t3_max is None`（保存済み T3 行が無い）→ 全期間計算（`df_price` 全体を渡す。
   現行の全行読み込みがそのままフォールバック経路になる）
2. `daily_prices` の `date > t3_max` の行数が **ちょうど1でない**（0または2以上）
   → 全期間計算
3. `indicators` から `date <= t3_max` を新しい順に `LIMIT K` 件読んだ結果が
   **K件に満たない** → 全期間計算
4. RECURSIVE型列（`recursive_column_names()`。前日値を継ぐ列。`ema_*` /
   `rs_value_eN` / `rs_roc_ema_N` / `td9` / `atr_14` / `rs_macd_signal_21` /
   `rs_blue_dot_age` / `rs_red_dot_age`）の供給履歴K行のいずれかに NaN がある
   → 全期間計算

**WINDOW型列は NULL チェック対象外にした理由**: `calc_moving_averages` /
`structure_pivot_series` / `zone_break_series` 等、WINDOW型列を計算する箇所は
いずれも `state` を受け取らず、常に生価格（または同一呼び出し内で先に
計算し直された他のWINDOW/RECURSIVE列）から**無条件に上書き計算**する
（`calculate_indicators` 内で毎回全行再計算）。そのため df に供給した
WINDOW型列の値そのものは一切読まれず、NULL であっても実害が無い
（`sp_pivot`/`sp_counter` は排他関係のため、供給履歴のどの行でも
必ずどちらかが NULL — これを NULL チェック対象に含めると増分経路が
常にフォールバックしてしまう）。RECURSIVE型列だけが `finalize_incremental_column`
経由で供給履歴をそのまま（行0..K-1）使い、それが WINDOW型列（`rs_ratio_eN`等）
のローリング計算の入力になるため、NULL チェックは RECURSIVE型列に限定した。

上記のいずれにも該当しない場合、`indicators` から読んだ K 行（日付昇順に戻す）を
`daily_prices` から読んだ同じ K 日分の生価格と `date` で内部結合し、新規1日分の
生価格行（`daily_prices` のみ、T3列は無し＝concat後に NaN）と連結して
`calculate_indicators(df_inc, spy_df, state=True)` を呼ぶ。生価格は指示どおり
必ず `daily_prices` から読み（`indicators` 側の生価格相当列は使わない）、
供給する T3 列は `INDICATOR_COLUMN_REGISTRY` から機械的に導出した
（手書きリストなし）。`spy_df` は従来どおり親プロセスが読んだ全履歴をそのまま
渡している（`calc_relative_strength` は日付で left-merge するため、
渡す範囲が増分窓より広くても問題ない）。SPY 取得失敗時に書き込みを止める
`date <= spy_latest_date` のガードは変更していない。

**テスト**: `backend/tests/pipeline/test_t3_indicators.py`（新規）。
`_calculate_t3_worker` を multiprocessing.Pool を介さず直接呼び出し、
`indicators.calculate.calculate_indicators` をスパイに差し替えて実際に
渡された `state` 引数を記録する方式で分岐を検証した。

- `test_normal_daily_uses_incremental_path` — 保存済み行がK本以上・新規1日
  ぶんの通常の日次で `state=True` が使われることを固定
- `test_incremental_result_matches_full_recompute` — 増分経路で書かれた行が
  全期間計算（オラクル）の同じ日付の行と rtol=atol=1e-9 で一致することを固定
  （zone_break系4列は非有界性が既知のため比較対象から除外。§8参照。
  それ以外の全列で一致を確認）
- `test_no_saved_t3_rows_falls_back` / `test_insufficient_saved_rows_falls_back`
  / `test_multi_day_gap_falls_back` / `test_null_state_column_falls_back` —
  上記フォールバック条件1〜4それぞれで `state=None` に落ちることを固定

pytest全件 1850 passed, 1 skipped（旧1844+新規6）。

**5-7（更新窓の実装）への申し送り**: §3.4 により (c) TWO_SIDED 型列が
存在しないと確定済みのため、5-7 は「当日のみ更新」で完了するはずで、
5-6 の実装（新規1行のみ `delta_df` として返す）は既にこの要件を満たしている
可能性が高い。5-7 で追加実装が必要か、5-6 の実装で既に充足しているかの
確認をオーケストレーターに委ねる。

### 5-6b: フォールバックの可視化（2026-09-22）

**オーケストレーターの検収で判明した設計上の穴への対処。** 5-6 の実装は正しく動作するが、
実データ（本番相当の SQLite）で検証したところ以下が判明した:

- 12銘柄すべてがフォールバック（増分経路が一度も発動しない）
- 理由: 増分条件「直近K行のRECURSIVE列にNaNが無い」を `rs_roc_ema_200` のNULLが
  満たさない（3,360銘柄中3,338銘柄が該当。§1.1 の不具合そのもの）
- フォールバックは現行の504本再計算なので、12列に不正確な値
  （`rs_roc_ema_200` はNULL）を書く
- 結果、**一度フォールバックすると翌日以降も増分が使えない**（フォールバックの入力自体が
  不正確なため、増分条件を再び満たせない）

**ユーザー判断（2026-09-22）**: 「フォールバックが発生したらT3リフレッシュすべき」が
本計画の前提であり、自動復帰は目的ではない。フォールバックが書いた値は12列が不正確なので、
復帰するかどうかに関係なくリフレッシュ対象。**したがって「一度落ちると復帰しない」は
仕様として正しい。危険なのは復帰しないことではなく、誰も気づかないこと**
（本番で4日間気づかれなかった）。フォールバック経路をParquet基点にする等の
「自動的に正しくする」実装は採用しない。

**対処（4点）**:

1. **`_calculate_t3_worker` の戻り値を `(ticker, sid, records, fallback_reason)` の
   4要素タプルに拡張**（`backend/pipeline/phases/t3_indicators.py`）。§3.5 の4条件に
   対応する識別子 `FALLBACK_REASON_NO_SAVED_ROWS` /
   `FALLBACK_REASON_INSUFFICIENT_ROWS` / `FALLBACK_REASON_MULTI_DAY_GAP` /
   `FALLBACK_REASON_NULL_RECURSIVE_COLUMN`（NULL列名を `"識別子:列名,列名"` の形式で
   付与）を新設。増分経路が成立した場合は `fallback_reason=None`
   （不変条件: `df_inc is not None <=> fallback_reason is None`）。
2. **`sync_phase_t3_indicators` がフェーズ終了時に集計してログ出力**
   （`_log_fallback_summary` 新設）。0件のときは INFO
   「全銘柄が増分経路で計算されました」に留め、1件以上のときは WARNING で
   件数と理由内訳（列名等のdetailは落としカテゴリだけで集計）・
   `--rebuild-from T3` の推奨を出す。
3. **`tools/db_health_check.py` に `--check-recursive-state` フラグを追加**。
   `recursive_column_names()`（レジストリ）から対象列を機械的に取得し、
   直近 `max_lookback()` 行にNULLがある銘柄をNGとして検出する。SPYの `rs_` 系列
   （構造的にNULLが正常）のみ除外し、他の閾値によるノイズ抑制は行わない
   （現在の本番データは3,338/3,360銘柄が該当し大量検出するのが正しい挙動）。
4. **`doc/backend_specification.md` §3.4 に増分計算とフォールバックの関係を明記**
   （フォールバックは例外状態・書かれた値は不正確・T3リフレッシュが必要である旨）。
   計画書 §5-16（昇格手順）に「フル再計算で状態をseedしてからでないと増分経路は
   発動しない」ことを明記。

**テスト**: `backend/tests/pipeline/test_t3_indicators.py` に
`TestLogFallbackSummary`（2件: 0件時はINFOのみ・1件以上時はWARNING+内訳）を追加し、
既存のフォールバック系4テストに `fallback_reason` の値そのものの検証を追加した。
`backend/tests/tools/test_db_health_check.py` に
`test_recursive_null_detected_when_flag_enabled` /
`test_recursive_null_not_checked_by_default` /
`test_recursive_null_excludes_spy_rs_columns` /
`test_recursive_null_absent_when_all_present` の4件を追加
（`hc.recursive_column_names`/`hc.max_lookback` を小さい固定値へ差し替えて
本番レジストリ全列に依存しない構成にした）。

pytest全件 1856 passed, 1 skipped（旧1850+新規6）。

### 5-6d: 読み出しコストは行数より列数が支配的（2026-09-23）

**オーケストレーターの実測に基づく読み出しコスト削減。** 5-6 の実装は増分呼び出しの
都度、`indicators` テーブルからレジストリの全67列（`sorted(INDICATOR_COLUMN_REGISTRY.keys())`）を
読んでいたが、オーケストレーターの実測（3,360銘柄・sandbox SQLite）で
**読み出しコストを支配するのは行数ではなく列数**と判明した:

```
indicators 252行 × 67列   5.60 ms/銘柄 -> 18.8 秒
indicators 252行 ×  6列   0.96 ms/銘柄 ->  3.2 秒   （5.9倍速い）
```

一方、実際に増分計算の**入力として参照される** T3 列は67列中33列のみで、
残り34列は計算結果として書かれるだけの出力専用列（`calculate_indicators` が
毎回生価格から無条件に再計算するWINDOW型列）だった。

**実装**: `incremental_state_registry.py` に `supplied_column_names()` を追加した。
導出ロジックはレジストリから機械的に求める（手書きリスト禁止）:

1. RECURSIVE型列自身（`recursive_column_names()`。前日の自列値を
   `prev_self_seed`/`finalize_incremental_column` が直接参照するため必須。21列）
2. 加えて、他の列の `inputs` として参照される T3 列（生価格・SPY列を除く。12列。
   `sma_50`/`sma_150`/`sma_200`/`atr_pct_14`/`rs_value`/`rs_ratio_e5〜200`/
   `rs_macd_line_21`/`vol_surge_21`）— これらは厳密には WINDOW型列であり
   `calculate_indicators` が毎回全行を生価格から再計算し直すため自列の履歴は
   理論上不要だが、レジストリが `inputs` として宣言している T3 列は安全側で
   すべて供給するという保守的な基準を採用した

合計 **21 + 12 = 33列**。`t3_indicators.py` の SQL 読み出し列をこの関数の戻り値に
差し替えた（`ind_cols = list(supplied_column_names())`）。

**テスト**: `test_incremental_state_registry.py` に `TestSuppliedColumnNames` を新設し、
件数（33）・RECURSIVE型が全て含まれること・戻り値がソート済みタプルで重複が無いこと・
全てレジストリに登録済みの列名であることを固定した。将来 lookback/inputs の変更で
件数が増減したら気づける。

**等価性の確認（実データ・sandbox SQLite・25銘柄）**: seed済みsandboxに対して
`_calculate_t3_worker` を直接呼び、Parquet 全履歴計算（オラクル）と突き合わせた
（スクリプト: `tmp/verify_worker_incremental_5_6d.py`。5-6検収時の
`tmp/verify_worker_incremental.py` を、戻り値が4要素タプルに変わった点を反映して改訂）。

```
増分経路が使われた: 25 / フォールバック: 0
Parquet 全履歴と比較した銘柄: 25 / 値が不一致だった銘柄: 0 / 不一致列の合計: 0
```

供給しなくなった34列は増分呼び出し時に df に存在しないことになるが、
`normalize_supplied_dtypes`（df に存在する列だけを対象。5-4d）・
`finalize_incremental_column`/`prev_self_seed`（`col not in df.columns` を
既にガード済み。5-4b）はいずれも影響を受けない設計だったため、コード変更は
`incremental_state_registry.py` への関数追加と `t3_indicators.py` の1行差し替えのみで済んだ。

**読み出し時間の実測（`tmp/bench_indicators_read_5_6d.py`。sandbox SQLite・
400銘柄・実際の `SELECT ... ORDER BY date DESC LIMIT K` クエリで列数のみ変えて比較）**:

`K = max_lookback()` は5-4e（zone_break lookback見直し）により実際には **400**
（計画時点の見積もり「252行」より大きい）。実クエリでの計測結果:

| 列数 | 1銘柄 | 3,360銘柄換算 |
|---|---|---|
| 変更前（67列） | 46.92 ms | 157.7秒（2.6分） |
| 変更後（33列） | 4.36 ms | **14.7秒** |

**約90%削減。** 見積もり時点（252行想定）の「18.8秒→10.3秒（45%削減）」より
実際の削減幅が大きいのは、K が400に伸びたことで列数の影響がさらに大きくなった
ため（行数が多いほど「列数を削る」効果が増幅される）。

### 7-5. 5-6c: ウォームアップ検査の閾値が未確定（2026-09-23）

ユーザー提案による検査。今回の `rs_momentum_e200` 全損は、これがあれば
**初日に3,056銘柄で検出できた**（610本以上の履歴を持つ銘柄すべてが NULL だったため）。

実装は完了したが、**閾値は確定していない**。本番の全3,362銘柄に当てたところ
偽陽性ゼロにならず、内訳は3種類だった:

1. **構造的に NULL（除外が必要）** — SPY の `rs_*` 全列（自身との RS は計算しない）、
   `^VIX`/`^VIX3M`/SPY の出来高由来列（`vol_surge_21` / `vol_surge_rel_spy_21` /
   `up_down_vol_ratio_50`）。`--check-recursive-state` は SPY の `rs_*` を既に除外しており、
   本フラグにも同種の除外が要る
2. **閾値の誤り** — `atr_14`/`atr_pct_14` を 0 としたが、価格が12〜18行しかない
   新規上場7銘柄で NULL。Wilder 実装は14本未満で例外を投げて NaN になる。
   **標本を「2,400本以上の履歴を持つ120銘柄」に限ったことによる見落とし**
3. **本物の異常（検出されるべきもの）** — `rs_roc_ema_200` が NULL の40銘柄
   （個別25・テーマ12・指標2・市場1）。511本以上の履歴があるのに値が無い

**3 は偽陽性ではなく、この検査が掘り当てた実在の異常**である。したがって
「偽陽性ゼロ」を完了条件にしたのは設定が厳しすぎた。

ユーザー判断（2026-09-23）により、opt-in フラグのまま**コミットして別タスクへ切り出す**。
中核（5-9 受け入れ検証・5-11 性能実測）を先に片付けるため。残作業は §8 に起票する。

> [!NOTE]
> 実装したサブエージェントが週次上限で中断したため、レジストリの docstring に
> 「全銘柄で偽陽性ゼロを確認済み」という**検証前に書かれた誤った記述**が残っていた。
> オーケストレーターが実測結果に訂正してからコミットしている（`0abd3bc`）。

### 7-6. code-review 2巡と、検収で繰り返した同型の失敗（2026-09-23）

`/code-review` を2回通し、計7件の指摘に対応した。**うち1件は1回目の修正(5-15b)が
作り込んだ回帰**で、オーケストレーターの検収がそれを見逃した。

#### 指摘1（2回目・最重要）: 欠陥判定が `rs_roc_ema_200` に到達不能だった

5-15b は「その行の位置が `warmup_bars` を超えているのに NULL か」で
欠陥と正当なウォームアップ中を分けた。しかし:

```
ホットキャッシュの最大行数: 504 -> position の最大値: 503
warmup_bars['rs_roc_ema_200'] = 511
```

`position > 511` は**到達不能**。しかもこれは**本不具合の当事者そのものの列**であり、
本番の実際の欠陥が `warmup_in_progress`（正当）に分類され、INFO で
「全期間計算そのものの結果は正確です」と報告される状態だった。
**5-6b が「危険なのは誰も気づかないこと」として塞いだ穴を開け直していた。**

**修正**: 銘柄の真の履歴長はホットキャッシュから分からないため、
**履歴長を知らずに判定できる性質**に切り替えた:

| 観測 | 分類 |
|---|---|
| **単調性の破れ**（古い行に値があり、新しい行が NULL） | **欠陥**（正常系では起こり得ない） |
| 窓全体が NULL かつ `warmup_bars < K` | 欠陥 |
| 窓全体が NULL かつ `warmup_bars >= K` | **判別不能**（専用の識別子。黙らせない） |
| NULL → 非NULL へ単調に遷移 | 正当なウォームアップ中 |

**本番の実際の不具合は「単調性の破れ」の形**（2026-09-14 以前は値あり、09-15 以降が NULL）。
`columns_with_undeterminable_warmup()` で判別不能な列を機械的に列挙し、
**現状1列（`rs_roc_ema_200`）であることをテストで固定**した。

**発火の実証**: seed 済み sandbox のコピーに本番と同じ壊れ方（直近4営業日の
`rs_roc_ema_200` を NULL 化）を注入したところ、健全時に増分経路へ乗っていた10銘柄が
**全件 `null_recursive_column`（欠陥）に分類**された。

#### 指摘2: ギャップ判定の窓が、実際に書く窓と違った

`new_dates` は `date > t3_max` のみだが、実際に書く `delta_df` は
`date <= spy_latest_date` で絞られる。T2 が SPY より進んだ銘柄で
`MULTI_DAY_GAP` の誤判定が起きうる（現状0件・潜在的）。判定窓を揃えた。

#### 指摘3: 供給33列のうち12列は読み捨てだった

RECURSIVE 21列以外の12列は、読まれる前に無条件で上書きされていた。
**21列でも実データ25銘柄で不一致ゼロ**を確認し、`supplied_column_names()` を
RECURSIVE 列のみに変更。読み出しは **14.7秒 → 10.3秒**（67列時点からは 27.6秒 → 10.3秒）。

> [!IMPORTANT]
> この指摘は 5-3b の時点で実装者が「理論上は RECURSIVE 21列だけで足りる可能性がある」と
> 提案していたが、オーケストレーターが**測らずに推論で却下**していた
> （「`rs_ratio_e200` のように保存済み中間列を入力にするものがある」）。
> 実際には `rs_ratio_e200` は供給値を読む前に上書きされており、必要なのは
> 元になる `rs_value_e200`（再帰列）だけだった。

#### 検収で繰り返した同型の失敗（再発防止のため記録する）

**「0件」という結果が、正常だからなのか測れていないからなのかを確かめていなかった**ケースが2回あった。

1. **5-2 の落ち着き窓**: 陽性対照に `ema_200` を選んだが、先頭からの切り出しでは
   シード位置が動かず**差分が出なくて当然**だった。測定ハーネスが壊れていても
   気づかない状態で「全列ゼロ」と報告した（後に `pivot_strength_low` で取り直し）
2. **5-15b の欠陥分類**: `position > warmup_bars` が到達不能な分岐を測って
   「欠陥0件」と報告した。健全だから0ではなく、**発火しえないから0**だった

**対策**: 「検出されるはずのものを注入して、実際に検出されること」を確認してから
0件を報告する。5-15c では本番と同じ壊れ方を注入して発火を実証した。

## 8. スコープ外・残作業（issue_list へ起票する）

- 🟠 **遡及的な価格修正（株式統合・分割補正）の後に T3 が再計算されない** —
  `adjust_symbol_split.py` で過去の close を補正しても、T3 の過去行は古いまま残る。
  **ウィークリーメンテナンスで該当銘柄の T3 を再計算する**仕組みが要る。
  **さらに、現在のウィークリー修復自体が同じ欠陥を持つ**: `weekly_maintenance.py:403` は
  `db.query(DailyPrice)`（SQLite の504本）から再計算するため、
  「修復」として遡り不足の行を書き込む。また `:365` は `Indicator.id.is_(None)`、
  すなわち**行が存在しないケースしか拾わず、値が誤っている行は対象外**。
  今回の `rs_momentum_e200` も行はあって値が NULL のため**ウィークリーでは直らない**
- 🟠 **T5 の日次も SQLite 基点のまま** — `t5_signals.py:109-117`。`SPY_LOOKBACK_MIN_BARS` は
  警報であって治療ではない。本計画と同型の対処が要る（§1.3）
- **`rs_momentum_e200` の積み方が過剰かもしれない** — 200期間の処理を4段積んでおり、
  必要遡りが 610本（A-full 後は810本 ≒ 3年3ヶ月）。表示専用で戦略からは使われていない。
  退役または再設計の候補（①の §4-7 の議論から分離）
- 🟠 **`zone_break` の必要履歴が有界でない — ホットウィンドウ計算では約1%の銘柄が不正確**
  （5-4c・ユーザー判断 2026-09-22）。`is_zone_break_bull`/`zb_ssl`/`zb_bsl`/
  `is_zone_break_weak` は `zone_break` の内部状態が確定した反転（BOS）ごとにリセットされる
  一方、トレンドレッグの長さに上限が無い（移植元 Pine Script の性質）ため、必要履歴本数が
  原理的に非有界。300銘柄実測（2026-09-22）:

  | 列 | 中央値 | p90 | p99 | 最大 | >250本 |
  |---|---|---|---|---|---|
  | `sp_pivot`/`sp_hl`/`sp_counter` | 120 | 120 | 120 | 120 | 0/300 |
  | `is_zone_break_bull` | 120 | 120 | 250 | 250 | 0/300 |
  | `zb_ssl` | 120 | 120 | 250 | 2,400 | 2/300 |
  | `zb_bsl` | 120 | 120 | 251 | 2,400 | 3/300 |
  | `is_zone_break_weak` | 120 | 120 | 250 | 400 | 1/300 |

  ユーザー判断（案A採用）に基づき、`incremental_state_registry.py` の `zb_ssl`/`zb_bsl`/
  `is_zone_break_bull`/`is_zone_break_weak` の lookback を設定し、「厳密解ではない」ことを
  `doc/backend_specification.md` に明記した。**これは増分化が新たに生じさせた問題ではない**
  — 増分化以前の日次実装も SQLite の504行だけで毎回ゼロから再計算していたため、同じ約1%の
  銘柄は既に本番で不正確な値を持っている（構造上同一の制約）。真の恒久対応（例: Parquet
  全履歴から直近リセット位置を特定してから増分計算する、など）は別途検討が必要で、本計画の
  スコープ外。`doc/issue_list.md` への転記はオーケストレーターが行う（§4-3 に準じた運用）。

  > [!NOTE]
  > 5-4c 版は lookback を `HOT_WINDOW_BARS`（504。SQLiteホットウィンドウ全体）に設定して
  > いたが、これは `max_lookback()+1=505` が SQLite の保持行数504を1行超えマージンが
  > ゼロだった。5-4e（2026-09-22・ユーザー判断）で `ZONE_BREAK_LOOKBACK`（400）に見直し、
  > `HOT_WINDOW_BARS` に対して約103行のマージンを確保した。400と600〜2,400の間で精度が
  > 変わらないと実測で確認済みのため、この見直しによる精度低下は無い。詳細は §7「5-4e」。
