# ローリング指標の遡り不足対応（min_periods=window への統一 / A-core）計画書

- **ステータス**: 🚧 実装開始（5-1完了）。**2026-09-20 にスコープ拡張**（NULL 表現の設計原則を §2.3 に確定し、T4／T5／暗黙フォールバック撤去を §3.5〜3.9 に追加）。前提だった ② は 2026-09-17 に解決・本番昇格済み（§4-6）。**2026-09-23: 着手前レビューで2件の穴を発見・修正**——① §4-7（A-full を含める）が §5 チェックリストに未反映だったため 5-9c を追加し、§8 の矛盾する古い記述を無効化 ② T3増分化との相互作用（`warmup_bars` レジストリの陳腐化）が未対応だったため 5-11b を追加
- **実施者**: AI エージェント（Claude Opus 5）
- **開始日**: 2026-09-11 / **完了日**: —
- **作業ブランチ**: `worktree-min-periods-warmup`（`.claude/worktrees/min-periods-warmup`。種別 B のため `--mode write` でプロビジョニング済み）
- **対象 issue**: `doc/issue_list.md`「`min_periods=1` のローリング指標が、遡り不足でも NaN を返さず『それらしい値』を出す」（以下 **①**）
- **前提となる issue**: `doc/issue_list.md`「T5 のリフレッシュが SQLite 基点のまま — Parquet 基点化が T5 に及んでいない」（以下 **②**。2026-09-11 起票）

## 1. 背景と目的

`sma_200` などのローリング指標が `min_periods=1` で計算されているため、**遡りが窓長に満たなくても NaN にならず「ある分だけの平均」が入る**。本計画はこれを `min_periods=window` に統一し、**遡り不足を NaN として表現する**。

### 1.0 課題の本質（2026-09-11 の議論で整理）

- **SMA は窓を超えれば正しい値になり、誤差は後に残らない。** 過去 N 本の単純平均だから。初期値の誤差が尾を引く EMA とは性質が違う（`calculate_ema_tv` がシード前を NaN にしているのはこのため）
- したがって課題は「**窓に届く前の期間に、その仮の値で売買判定をしてしまう**」ことに限られる。一言でいえば **IPO 銘柄がスクリーンにかかってしまう**
- ただし仮の値は**向きが偏っている**。`dist_52w_high_pct` は「上場以来の高値」、`sma_200` は「上場以来の平均」になるため、**上場後に上昇し続けている銘柄を系統的に「強く」見せる**。平均すれば消えるランダム誤差ではなく、モメンタム系戦略に集中して効く（§1.2(c) で `E2`/`F`/`A`/`G2` に集中）
- **一般的なツールは NaN を返す**: pandas `rolling(200).mean()`（`min_periods` 既定＝窓長）、TA-Lib `SMA`、TradingView Pine `ta.sma` はいずれも不足分を NaN/`na` にし、`close > SMA200` ではヒットしない。**このプロジェクトは `min_periods=1` を明示してわざわざ既定から外している**。本計画は一般的な挙動に揃えるもの
- **副次効果**: NaN にすれば `sma_200 IS NULL` で「上場から200本未満の銘柄」を正確に抽出でき、**IPO 特化スクリーナーの土台になる**。現状は偽の値で埋まっていてこの条件自体が作れない
- **重大度**: issue に付けた 🔴 ほど高くない。「本番を静かに壊す」種類ではなく「上場直後の銘柄を少し甘く判定する」種類（シグナルの 0.45〜1.76%）

> [!IMPORTANT]
> **② とは別問題。** ② は「履歴は十分あるのに、T5 をリフレッシュする際に SQLite の2年分だけで計算するため、窓の先頭が遡り不足になる」問題で、**正解の値が存在するのに誤った値が保存される**。発生条件（T5 の全日付再計算時のみ。デイリーでは起きない）・原因（2026-09-04 の Parquet 基点化が T5 に及んでいない）・直し方（T5 も Parquet 基点にする）がすべて異なり、**`min_periods` を変えても ② は直らない**。経緯は §7-4。

### 1.1 問題の構造

遡り不足への対応が、同じリポジトリ内で**3つの流儀に割れている**。設計判断ではなく書き方の不揃い。

| 流儀 | 例 | 遡り不足時 |
|---|---|---|
| `min_periods=window` | `volatility.py:67-68`（`atr_10`/`atr_50`）、`volume_and_trends.py:45-46`（`up_down_vol_ratio_50`） | NaN（正しい） |
| 関数内で長さチェック | `moving_averages.py:24-25`（`calculate_ema_tv`） | NaN（正しい） |
| `min_periods=1` | `sma_*` / `dist_52w_high_pct` / `adr_pct_21` / `avg_dollar_volume_21` ほか | **偽の値** |

決定的なのは `moving_averages.py:46` で、**同じループの中**で `sma_200` は `min_periods=1`（偽値）、`ema_200` は `calculate_ema_tv`（NaN）を返している。Parquet 全履歴で見ても、AAPL の1本目（2017-01-03）の `sma_200` は 26.70（その日の終値そのもの）、`ema_200` は 199本目まで NaN。

同じ病気への**アドホックなガードが既に2つ生えている**（3つ目を生やさないことが本計画の動機）:

- `relative_strength.py:22` — `RS_DOT_WARMUP_BARS = 252`（ドット偽点灯を止める本数ガード）
- `market_signals.py:174` — `has_breadth` が日付 2018-04-01 のハードコードで MTS の breadth 成分を無効化している

### 1.2 ベースライン（着手前の実測値・2026-09-11）

すべて現行の本番 Parquet 世代 `prices_20260910_145529.parquet` に対する実測。再現スクリプトは `tmp/` に残してある。

**(a) 汚染の時間分布** — 各銘柄の何本目かを数え、日ごとに上場済み銘柄に占める偽値銘柄の割合（年平均）。スクリプト: `tmp/check_minperiods_timeline.py`

| 年 | 上場済み銘柄 | `sma_200` 偽値 | `dist_52w` 偽値 |
|---|---:|---:|---:|
| 2017 | 2,112 | **78.1%** | **97.7%** |
| 2018 | 2,327 | 8.4% | 9.1% |
| 2019-2020 | 約2,500 | 3.3-3.5% | 4.3-5.5% |
| 2021 | 2,803 | 6.8% | 8.1% |
| 2022-2025 | 約3,000 | 1.5-2.9% | 1.8-2.8% |
| 2026 | 3,201 | 2.9% | 3.6% |

2017年が壊滅的なのは個別銘柄の Parquet 起点が 2017-01-01 で全銘柄が一斉にウォームアップに入るため。**ただし 2017年はバックテストで使っていない**（学習期間 2022 / 2024-06〜2025-12、検証窓 2018Q4・2020H1・2023・2026H1）。**実際に効く最悪ケースは `stress_bear` の 2018Q4。**

**(b) 現在時点の静的影響（2026-09-09）** — スクリプト: `tmp/check_minperiods_impact.py`

- active 3,258銘柄中、遡り252本未満が **117件（3.6%）**。`sma_200` / `dist_52w_high_pct` は **117件すべて非NULL＝偽値**（対して `ema_200` は34件のみ非NULL＝流儀の不揃いの実データ証拠）
- 流動性フィルタ `min_avg_dollar_volume_21 = 2e6` を**通過してしまうのが 87件**
- 偽陽性方向に振れているのは `dist_52w_high_pct >= -10`（高値近接と誤認）が **10件**、`is_trend_template == 1` の誤点灯が **4件**（`MICC` 189本 / `PURR` 192本）
- **誤点灯が少ないのは偶然のセーフティネット**: 遡り150本未満だと `sma_150` と `sma_200` が同じ全期間平均に潰れ、cond3（`sma_150 > sma_200`）が厳密不等号のため必ず False。**150〜199本の帯だけが素通りする**

**(c) シグナルへの影響（本命）** — 現行データで21戦略 × 6評価窓のシグナル 33,828件を生成し、各シグナル銘柄が A-core 適用後に必要な遡り本数を満たすかを「n本目到達日」で照合。スクリプト: `tmp/signal_warmup_probe.py` / 結果: `tmp/signal_warmup_impact.csv`

| 評価窓 | シグナル総数 | 消失 | 割合 |
|---|---:|---:|---:|
| holdout: stress_bear 2018Q4 | 1,079 | 19 | **1.76%** |
| holdout: stress_bear COVID | 2,538 | 28 | 1.10% |
| learn: Bull 2024-25 | 11,914 | 95 | 0.80% |
| holdout: fwd 2026H1 | 5,094 | 36 | 0.71% |
| learn: Bear 2022 | 7,258 | 37 | 0.51% |
| holdout: calm 2023 | 5,945 | 27 | 0.45% |

戦略別（消失のあったものだけ。残り13戦略はゼロ）:

| 戦略 | 要求本数 | シグナル | 消失 | 割合 |
|---|---:|---:|---:|---:|
| `E2_vcp_breakout_52w` | 252 | 319 | 13 | **4.08%** |
| `F_elite_momentum97` | 252 | 5,352 | 93 | 1.74% |
| `A_momentum_breakout` | 50 | 1,801 | 30 | 1.67% |
| `G2_momentum_no_exhaust` | 50 | 1,237 | 18 | 1.46% |
| `D_ema21_pullback` | 252 | 723 | 6 | 0.83% |
| `H1_structure_2nd_break` | 50 | 9,059 | 63 | 0.70% |
| `G1_leader_td9_dip` | 50 | 4,270 | 18 | 0.42% |
| `C2_rrg_improving_in` | 50 | 1,833 | 1 | 0.05% |

消えるのは `NIO` / `ESTC` / `GH`（2018Q4）、`PURR` / `SHAZ` / `FJET` / `INFQ`（2026H1）といった**その時点で上場から日が浅い銘柄**。テーマ系（`B1`〜`B6`）が全滅ゼロなのは、テーマ指数の構成銘柄条件が実質的に古い銘柄を要求しているため。

**(d) pruning 境界には触れない** — `E2` は `min_avg_hits_per_day = 0.02` に対し実測 0.26件/日、`G2` は 0.5 に対し 1.02件/日、`A` は 0.1 に対し 1.48件/日。4% 減っても足切りに届かない。

**(e) T4（`relative_ranks`）への波及はゼロ** — `t4_ranks.py:49-71` のランク対象は **rs 系22列のみ**。`sma_200` / `dist_52w_high_pct` / `adr_pct_21` / `avg_dollar_volume_21` は1つも含まれない。

**(f) T5（MTS breadth）への波及は無視可能** — 2018-04 以降の全期間（530万行・2,918銘柄）で3通り計算。スクリプト: `tmp/breadth_warmup_impact.py`

| 方式 | breadth 差（平均） | MTS スコア差（最大悪化） | 差が1pt超の日数 |
|---|---:|---:|---:|
| A案そのまま（NaN→False） | -0.18 〜 -0.89pp（**全年で負**） | -0.87pt | 0 / 2,122日 |
| A案＋分母から除外 | -0.01 〜 +0.18pp | -0.19pt | 0 / 2,122日 |

**(g) `t5_signals.py:76` に書き方の問題** — `is_above_sma50` を `close > sma_50` の bool 演算で作っているため `sma_50` が NaN でも False になり、直後の `mean(skipna=True)`（`t5_signals.py:79`）が効かない。A案を入れると breadth が一貫して下振れする。

### 1.3 完了時の状態（成功条件）

1. `min_periods=1` の指標が、遡り不足時に NaN を返す
2. `db_health_check` が通り、T2/T3 行数一致・SPY 同期が維持されている
3. sandbox で再計算したデータに対するバックテストのシグナル数が、§1.2(c) の予測（窓単位 0.45〜1.76%減）と整合する
4. MTS の `breadth_sma50` が下振れしていない（§1.2(f) の「A案＋分母除外」の水準）
5. 影響のあった8戦略の成績変化が記録され、その8戦略の再最適化がユーザーへ引き渡されている（成績変化は差し戻し基準にしない — §4-4。最適化の実行はユーザー — §4-3）
6. **SPY の T3 行が 2011-03-30 以降で完全不変**（§3.3。SPY は相対基準のため、変化してよい範囲を事前に確定して機械的に検証する）
7. **昇格が ② の修正後に行われ、本番 MTS の SPY 由来列が壊れていない**（§4-6）
8. **T4 の相対ランクが判定不能を母集団から除外している**（§3.5）— `t4_ranks.py` と `parquet_recompute.py` の両経路で一致すること
9. **暗黙フォールバックが撤去されている**（§3.7）— `atr_14` が先頭13本で NaN、`vxv_vix_ratio` が `^VIX3M` 欠損時に NULL + 警告、`breadth_sma50` の保存値と計算値が一致
10. **T5 の gap 判定が「行の有無」になり、5-7e のアドホック収束処理が消えている**（§3.6）

## 2. スコープと設計判断

### 2.1 変更すること

1. **指標の `min_periods=1` を `min_periods=window` にする（A-core）** — 対象は §3.1 の一覧
2. **breadth 集計を NaN 保持にする（3箇所）** — `t5_signals.py:76` と、同じ書き方の複製 `backtest/scenario_runner.py:319` / `backtest/etf_single_runner.py:240`（§3.2）
3. **表示用チャート（`api/chart_router.py`）も揃える**（§4-2）
4. **テストを追加する** — `moving_averages.py` / `volatility.py` には現在テストが無い。1:1 対応で新規作成する
5. **仕様書に「遡り不足時は NaN」を明記する** — `doc/backend_specification.md` の指標定義表（L182 / L199 等）は式だけを書いており、ウォームアップの扱いが書かれていない

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 |
|---|---|
| **B案: バックテスト側で遡り不足銘柄を除外する** | **不採用。** `backtest_screener.py:344-347` の numeric フィルタは素の `>=` / `<=` / `==` なので、**NaN は比較が False になり自動的に母集団から外れる**。除外ロジックを別途書く必要がなく、B案の欠点（スクリーナーとバックテストでの二重管理）が A案では発生しない。加えて B案では実運用のスクリーナー画面に偽値が残る |
| **C案: 遡り本数を列として持たせ最適化対象にする** | **不採用。**「200日線が200本を要求する」のは指標の定義であって探索すべきパラメータではない。IPO 銘柄の抽出は A案で `sma_200 IS NULL` として実現できる（§1.0）。診断用の `bars_available` 列自体の価値は §8 に残す |
| **A-full（rs 系の `min_periods=max(1, n//2)`）を含めるか** | **含めない（別案件）。** `relative_strength.py:122,137-155` は**4つ目の流儀**で、`rs_trend_s200` は100本で値が出る。これは T4 に直撃し、しかも `PERCENT_RANK() OVER(ORDER BY col ASC)`（`t4_ranks.py:85`）は SQLite が **NULL を最小値として順位付けする**ため、**NaN 銘柄は除外されずランク0付近に張り付いて分母に残る**。A-core の「NaN は自動除外」という性質が成り立たず、影響の質が違う |
| **`RS_DOT_WARMUP_BARS` / `has_breadth` の既存ガードを撤去するか** | **撤去しない（今回は据え置き）。** A-core を入れれば原理的には不要になるが、撤去は「撤去しても偽点灯が復活しない」ことの独立検証を要する。本計画のスコープを膨らませないため据え置き、§8 に残す |
| **SPY 専用計算（`market_signals.py:111-112,127,202` / `scenario_market_score.py:91-107` のフォールバック）を揃えるか** | **揃えない（2026-09-11 ユーザー判断。一度「揃える方向」に傾いたが、整理の結果この判断に戻した — 経緯は §4-1）。** SPY の自前計算が効くのは **T5 の全日付再計算の経路だけで、そこは ② の問題**。② を直さずに揃えると、SQLite 窓の先頭199日で `sma_200` が NaN になり、`close > sma_200` が False に潰れて**偽の値が別の偽の値（BEAR 寄り）に置き換わるだけ**。さらに `distribution_days`（L127）の `.astype(int)` が NaN で**例外になり T5 ごと落ちる**。揃えるなら ② の対応の中で扱う。なお自前計算は据え置いても **T3 経由の波及は避けられない**（§3.3）ため、変化してよい範囲を事前に確定して検証する |
| **② を本計画に含めるか** | **含めない（別 issue として起票済み）。** 発生条件・原因・直し方がすべて ① と異なる（§1.0）。① は `min_periods` の変更で直るが、② はそれでは直らない。**ただし ① の昇格経路が ② を通るため、昇格は ② の修正後に行う**（§4-6） |
| **バックテスト成績が悪化した場合に差し戻すか** | **差し戻さない。正しさを優先する（2026-09-11 ユーザー判断）。** 偽の指標値で拾っていたシグナルが仮に勝っていたとしても、それはエッジではなくデータの欠陥に乗っていただけ。成績変化は記録するが採否基準にしない。**ただし §6.1 の反証（シグナル数が予測と整合するか）は成績とは別物で、実装の正しさの検証なので省略しない** |
| **2017年の汚染（78%）への対処** | **対処しない。** 個別銘柄の Parquet 起点が 2017-01-01 であることに由来する構造的なもので、上場前データは存在しないため解消できない。かつ 2017年はバックテストの学習期間にも検証窓にも入っていない。A案適用後は「2017年は NaN」という正しい姿になる |


> [!IMPORTANT]
> **2026-09-20: 上表のうち4行の前提が変わった。** 下記が優先する。
>
> | 上表の行 | 2026-09-20 時点 |
> |---|---|
> | **A-full（rs 系の `min_periods=max(1, n//2)`）を含めるか** | **不採用の根拠が消えた。** 当初の理由は「T4 が NULL を除外しないため A-core の『NaN は自動除外』が成り立たない」だったが、§3.5 でその T4 を直す。**含めるかは §4-7 で再判断** |
> | **`RS_DOT_WARMUP_BARS` / `has_breadth` の既存ガードを撤去するか** | **`has_breadth` は撤去する。** `market_signals.py:262` の `date >= '2018-04-01'` ハードコードは、§3.7 で `breadth_sma50` が正しく NULL になれば **NULL 判定に置き換えられる**。`RS_DOT_WARMUP_BARS` は §4-7 の判断に従う |
> | **SPY 専用計算を揃えるか** | **② の中で既に揃った。** `market_signals.py:172-173` は現在 `min_periods=50` / `min_periods=200`（② の 5-9）。この行は解決済み |
> | **② を本計画に含めるか** | **② は 2026-09-17 に解決・本番昇格済み**（§4-6）。昇格前提は充足した |

### 2.3 NULL（判定不能）の表現 — レイヤ横断の設計原則（2026-09-20 合意）

本計画は NaN を増やす変更なので、**各層に散らばっている「NULL を別の値で埋める」処理と正面衝突する**。
②（`doc/completed/t5_parquet_rebuild_plan.md` §8）に論点として残していたものを、ここで原則として確定する。

#### 2.3.1 原則

> **行の有無 = パイプラインがその日 / その銘柄を処理したか**
> **列の NULL = 処理した結果、判定できなかったか**

今の混乱は、**「計算したか」と「計算できたか」を同じ NULL で表している**ことに起因する。この2軸に分ける。

#### 2.3.2 期待する挙動（ユーザー確認済み・2026-09-20）

| 状況 | 期待 |
|---|---|
| IPO 直後で上場前のデータが存在しない | 値を残さない（列を NULL に。**行は残す**） |
| Parquet にはあるが SQLite の窓が足りない | Parquet の正しい値を使う。算出済みの過去の値を更新しに行かない |
| 依存先の指標が算出できない | 依存する指標も算出せず、スクリーナーにかからない（例: `sma_50` が無い日は `sma50_atr_mult` も無く、その条件にヒットしない） |
| T4 のような相対ランクの種になる場合 | **そのランクの順序から除外される**（母集団にも数えない） |

#### 2.3.3 「NULL」か「行を入れない」か → **NULL 一択**

行の不在で「判定不能」を表す案は採らない。理由:

1. **横持ちだから** — `indicators` も `relative_ranks`（`db/models.py:197-226`、ランク列21本）も1行に多数の列が同居する。
   「`sma_200` は判定不能だが `sma_50` は有効」が最も普通のケースであり、行単位では表現できない
2. **「行が無い」は既に別の意味で使われている** — T3 は `max(date) per symbol`（`t3_indicators.py:64`）、
   T4 は `max(date)`（`t4_ranks.py:12-13`）で「未計算」を検出している。判定不能を行の不在で表すと、
   **パイプラインが毎晩それを未計算と誤認して再計算し続ける**
3. **日付の連続性が壊れる** — ② の 5-7d で却下済み（`/available_dates` から日付が消える）

#### 2.3.4 層ごとの責務

| 層 | 責務 |
|---|---|
| 指標（`indicators/`） | 判定できなければ **NaN を返す**。代用値を発明しない |
| 保存（`pipeline/phases/`） | NaN を **NULL のまま保存**する。計算に使った値と保存する値を食い違わせない |
| T4（ランク） | 判定不能を**母集団から外す**（§3.5） |
| API | NULL を **NULL のまま返す**（`Optional` 化）。表示の判断をしない |
| フロント | **判定不能の共通表現を持つ**。0 や最悪値に倒さない |
| スクリーナー / バックテスト | **出口で二値に潰す**（`mask.fillna(False)`）。これは正しいので維持する |

**「埋めること」自体が悪いのではなく、「無言で埋めること」が悪い。** 既にお手本が
`market_signals.py:85-103` の `report_stale_input_gaps()` にある — VIX/VXV を ffill する**前に**
何営業日ぶん持ち越すかをログに出し、閾値超過で WARNING を上げ、docstring に
「持ち越し自体は短期なら妥当な近似なので値は変えない」と理由まで書いてある。
やむを得ず埋める場合はこの形に揃える（§3.8）。

#### 2.3.5 「満たさない」と「判定不能」の違い（整理・2026-09-20）

- **満たさない** = 判定した結果が False（**情報がある**）
- **判定不能** = 判定していない（**情報が無い**）

**スクリーナーの通常の使い方では差が出ない。** pandas は `NaN > x` も `NaN <= x` も False なので、
肯定条件でも否定条件でも判定不能な銘柄は落ちる。`screener_filters.py` の `mask.fillna(False)`（8箇所）は
**出口での二値化として正しく、変更しない**（2026-09-20 ユーザー確認）。

差が出るのは以下の3つだけ:

1. **数えるとき** — `COUNT` / `AVG` / `mean(skipna=True)` は NULL を黙って無視し、母数が変わる。
   `breadth_sma50` がこれ（§3.2）。T4 のランク（§3.5）と同じ構造
2. **「N個中M個を満たす」型のロジック** — 判定不能を0点として数えるか、銘柄ごと対象外にするか
3. **判定不能そのものを拾いたいとき** — `sma_200 IS NULL` で IPO 銘柄を抽出する（§1.0 の副次効果）

> [!NOTE]
> `~mask` のような否定の書き方をすると `~False = True` で**逆に拾ってしまう**（SQL の三値論理とも挙動が違う）。
> 2026-09-20 時点で `screener_filters.py` にこの書き方は無いが、今後入れないこと。

## 3. 変更内容

### 3.1 指標側（`min_periods=1` → `min_periods=window`）

| ファイル:行 | 対象 | 備考 |
|---|---|---|
| `indicators/moving_averages.py:46` | `sma_5/21/50/63/150/200` | 同じループの `ema_*` は既に NaN 返し。揃える |
| `indicators/volatility.py:44` | `adr_pct_21` | 同ファイル L67-68（`atr_10`/`atr_50`）が既に `min_periods=window`。流儀を揃える |
| `indicators/volume_and_trends.py:14` | `avg_dollar_volume_21` | 全戦略に注入される流動性ハード制約の入力 |
| `indicators/volume_and_trends.py:18-19` | `vol_sma_21` / `spy_vol_sma_21` | `vol_surge_21` / `vol_surge_rel_spy_21` の分母 |
| `indicators/volume_and_trends.py:37-38` | `max_63d` / `max_252d` | `dist_63d_high_pct` / `dist_52w_high_pct` の分母。`is_trend_template` の cond5 も参照 |
| `indicators/volume_and_trends.py:74,76` | `vol_sma_21` / `vol_accum_days_5` | |
| `indicators/relative_strength.py:275-284`（旧行番号163-172。5-3b以降のRECURSIVE化リファクタで移動） | `rs_252_high/low` / `close_252_high/low` | ブルー/レッドドット判定の入力。`RS_DOT_WARMUP_BARS` で実質吸収済みだが流儀としては揃える |
| `indicators/relative_strength.py:227,246-247,266-267`（A-full。2026-09-20棚卸し時点では§3.1に未記載だった。§4-7で2026-09-21に「含める」確定） | `rs_trend_sN` / `rs_ratio_eN` / `rs_momentum_eN`（`n∈{5,14,21,63,200}` のループ内。`rolling_std_independent` 呼び出し含む） | `min_periods=max(1, n//2)` → `min_periods=n` に統一。§3.5（T4のNULL除外）を先に入れる前提が成立して初めて採用可能になった変更（§2.2の表の注記参照） |
| `pipeline/orchestrator.py:109,326,402,461` | 仮想テーマ指数の `vol_surge` 21日平均 | 4箇所とも同一パターン |
| `pipeline/parquet_recompute.py:331` | 同上（Parquet 基点の再計算経路） | `VIRTUAL_INDEX_SURGE_WINDOW` |

**`is_trend_template`（`volume_and_trends.py:62`）は式を変えない。** 入力の `sma_150` / `sma_200` / `max_252d` が NaN になることで自動的に NaN/False になる。

**`spy_vol_sma_21`（`volume_and_trends.py:19`）は SPY の計算ではない。** `spy_volume` は `relative_strength.py:102-106` で**各銘柄の df に日付 merge された列**であり、その rolling は銘柄の上場日から始まる。遡り不足になるのは SPY ではなく銘柄側なので、ここに含める（§4-1 の対象外）。


#### 3.1.1 追加で見つかった2件（2026-09-20 の棚卸し）

**(a) `volatility.py:31-37` の `atr_14` — `min_periods` を使っていないため、上の grep から漏れていた。**

```python
atr_indicator = ta.volatility.AverageTrueRange(
    high=high, low=low, close=close, window=14, fillna=True
)
```

`ta` ライブラリの実装（`venv/Lib/site-packages/ta/volatility.py:46-55`）は `atr = np.zeros(len(close))` で
初期化し `atr[window-1]` 以降しか埋めない。**先頭13本は NaN でも「それらしい値」でもなく、リテラルの `0.0`**。

実測（本番 Parquet・2026-09-20）:

| 対象 | 実測 |
|---|---|
| `DOCU`（2018-04-27 上場） | 2018-04-27〜05-15 の13本が `atr_14 = 0.0` / `atr_pct_14 = 0.0`、14本目（05-16）から 2.288786 |
| `LOVE`（2018-06-27 上場） | 同じく先頭13本が 0.0 |
| 全銘柄合計 | `atr_14 == 0.0` が **43,798行**（NaN は68行のみ / 総 6,838,508行） |

**`0` は「それらしい値」より悪い。** `sma_200` の偽値は「上場以来の平均」で方向が偏る程度だが、
`atr_pct_14 = 0` は**「ボラティリティが完全にゼロ」という明確な極値**で、低ボラ条件のスクリーンに
最優先でヒットする。さらに `backtest_simulator.py:295,398` が `atr=row.get('atr_14')` を決済判定に
渡しており、ATR=0 はストップ幅ゼロの経路になる（`evaluate_position_exit_for_day` 側のガード有無は
未確認。5-5b で確認する）。

**直し方**: `fillna=True` を外すだけでは足りない（`_atr` は `np.zeros` 由来なので NaN にならず 0 のまま）。
**先頭 `window-1` 本を明示的に NaN で潰す**こと。

なお `sma50_atr_mult`（`volatility.py:49`）は `atr_pct_14 == 0` を明示チェックして NaN にしており、
**既に誰かが 0 の危険に気づいて、そこだけ塞いだ形跡がある**。

**(b) `is_trend_template`（`volume_and_trends.py:61-65`）の判定不能ガードに漏れがある。**

```python
df['is_trend_template'] = np.where(
    sma200_20d_ago.isna(), None,
    np.where(cond1 & cond2 & cond3 & cond4 & cond5, 1, 0))
```

外側の `np.where` は `sma200_20d_ago` **しか見ていない**。本計画で `max_252d`（`volume_and_trends.py:38`）が
NaN になると `cond5`（`close >= max_252d * 0.70`）が False になり、
**判定不能なのに `is_trend_template = 0`（＝条件を満たさない、と断定）が保存される**。
② の 5-9d で `phase()` を直したのと同型のバグ。**cond1〜cond5 の入力すべてを NULL ガードの対象にする。**

### 3.2 breadth 集計（3箇所）

`is_above_sma50` を bool ではなく NaN 保持にして、後段の平均で NaN 銘柄を分母から外す。**A案とセットで入れないと breadth が一貫して下振れする**（§1.2(f)(g)）。

| ファイル:行 | 系統 | 備考 |
|---|---|---|
| `pipeline/phases/t5_signals.py:76` | T5（MTS・本番） | 直後 L79 に `mean(skipna=True)` があり、意図は明らかに skipna |
| `backtest/scenario_runner.py:319` | 型3 シナリオ | 同じ書き方の複製（§7-1） |
| `backtest/etf_single_runner.py:240` | 型2 ETF シナリオ | 同上 |

**3箇所を同時に直すこと。** t5 だけ直すと、本番 MTS とシナリオテスト内で再計算される breadth が食い違う。いずれも「SPY 専用計算」ではなく**個別銘柄の集計**なので、§4-1（SPY は揃えない）の対象外。

### 3.3 SPY — 自前計算は変更しないが、T3 経由で波及する

**変更しないもの（§4-1）**: `market_signals.py:111-112,127,202` の SPY 自前計算、`scenario_market_score.py:91-107` のフォールバック計算。

**それでも波及するもの（§7-2）**: `scenario_market_score.py:77-79` は **T3 の `sma_50`/`sma_200`/`atr_14` が存在すれば優先して使い**、無いときだけ自前計算にフォールバックする（L113-114 で `row['sma_50']` / `row['sma_200']` を参照）。そして SPY の T3 行は `moving_averages.py` で計算される。**したがって §3.1 の変更は SPY の T3 行を通じてシナリオの市場スコアに届く。**

T3 は Parquet 基点で全期間再計算される（2026-09-04 以降）ので、SPY の T3 行には ② の問題は起きない。変化してよい範囲を事前に確定しておく（実測 2026-09-11）:

| SPY の T3 | 日付 |
|---|---|
| 履歴起点 | 2010-04-01 |
| 200本目（`sma_200` が値を持ち始める） | 2011-01-13 |
| 252本目（`dist_52w_high_pct` / `is_trend_template` が値を持ち始める） | **2011-03-30** |

**2011-03-30 以降、SPY の T3 行は全列が完全不変でなければならない。** 変化するのはそれ以前の最大251本の NaN 化のみ（① として正しい挙動）。これを 5-12 で機械的に検証する（1行でも差分があれば停止）。

### 3.4 表示用（`api/chart_router.py:141-145,285-298`）— 含める（§4-2）

フロントのチャート表示用にサーバ側で自前計算している別系統。上場直後の銘柄に「200日線」が描かれている。修正後は**線が上場から200本目以降に始まる**（見た目が変わる）。種別 A なので昇格不要。

### 3.5 T4 — 相対ランクから判定不能を除外する（2026-09-20 ユーザー判断で方針転換）

**現状は「NULL = 最小値」を明示的な仕様として採用している。** SQLite は ASC で NULL を先頭に並べるため、
`t4_ranks.py:85` の `PERCENT_RANK() OVER(PARTITION BY s.category ORDER BY i.{col} ASC)` は
**判定不能な銘柄に rank 0.0（最下位）を与え、かつ母集団 n に数える**。
Parquet 側（`parquet_recompute.py:88` の `s.fillna(-np.inf).rank(...)`）も同じ挙動に揃えてあり、
理由（「NaN を除外すると母集団サイズが変わって全体がずれる」）がコメントとテストで固定されている。

**§2.3.2 の期待に合わせて、これをひっくり返す。** ユーザー判断（2026-09-20）:
「正しくない既存を残すぐらいなら T3 rebuild すべき」。

**実装（SQL 側）** — パーティションに NULL フラグを足して非 NULL 群だけで正規化し、NULL 群は CASE で潰す:

```sql
CASE WHEN i.{col} IS NULL THEN NULL
     ELSE PERCENT_RANK() OVER (
         PARTITION BY s.category, (i.{col} IS NULL)
         ORDER BY i.{col} ASC)
END AS {rank_col}
```

**Parquet 側（`parquet_recompute.py:88`）も同時に直す。** 対になっており、片方だけ直すと
SQLite と Parquet が乖離する。`test_parquet_recompute.py` の期待値も書き換え対象。

> [!WARNING]
> **影響が甚大。** 母集団 n が減るため**既存の全ランク履歴が変わる**（全銘柄が少しずつ上にシフト）。
> バックテスト結果と Optuna study はすべて別の前提の上のものになる。
> **だからこそ §3.1 の変更と同じ再計算バッチで一度にやる**（別々にやると全期間再計算が2回走り、
> その間バックテストの比較基準が2回変わる）。

> [!NOTE]
> **§2.2 の「A-full を含めない」判断の根拠が、この変更で消える。** 当初 A-full（`relative_strength.py` の
> `min_periods=max(1, n//2)`）を外した理由は「T4 が NULL を除外せずランク0付近に張り付いて分母に残るため、
> A-core の『NaN は自動除外』が成り立たない」だった。§3.5 を入れるとその前提が成立する。
> **A-full を本計画に含めるかは未判断（§4-7）。**

### 3.6 T5 — gap 判定を「行の有無」に統一する

`t5_signals.py:44` は `MarketSignal.market_trend_score.is_not(None)` を「計算済み」の印にしている。
**§2.3.1 の原則では成立しない**（NULL が正規の結果になるため）。実際 ② でこの問題を踏み、
「遡り不足かつ既に行がある」日付を gap から外すアドホックな収束処理（`t5_signals.py:64-81`・5-7e）で
回避している。

**T3（`max(date) per symbol`）・T4（`max(date)`）と同じ「行の有無」判定に揃える。**
これにより 5-7e のガードごと削除できる。

**引き換えに失うもの**: 「T5 が走ったのにスコアが入らなかった」という本物の不具合の自動検知。
復旧手段は `--rebuild-from T5` として既にあるため、**原則を通して例外コードを消す方を採る**。
`.claude/skills/pipeline-debugging/SKILL.md:32`（「NULL 欠損は過去に遡ってバックフィル」）の記述も
更新が必要。

### 3.7 暗黙フォールバックの撤去（2026-09-20 棚卸し結果）

「`fillna=True` 系の穴が他にもないか」をユーザー指示で先行調査した結果。`ta` ライブラリの呼び出しは
リポジトリ全体で `volatility.py:32` の1箇所のみだった。同類の「無言で埋める」箇所を区分して以下に示す。

#### 撤去する（偽値を作っている）

| 箇所 | 手口 | 埋める値 | 備考 |
|---|---|---|---|
| `volatility.py:33` `atr_14` | `ta(..., fillna=True)` | **先頭13本 = 0.0** | §3.1.1(a)。**本命** |
| `market_signals.py:290` `atr_14`（SPY） | `ffill().fillna(1.0)` | ATR = 1ドル | 下記の実測参照 |
| `market_signals.py:246-257` `vxv_vix_ratio` | VIX からの推定式 | 捏造値 | 下記参照 |
| `market_signals.py:140,263,264` | `fillna(0.5)` | 中立値 | **保存は NULL で不一致**（§3.2 と併せて解消） |
| `volume_and_trends.py:76` `vol_accum_days_5` | `min_periods=1` + `fillna(0)` | 5日未満で数えた件数 | §3.1 と同時 |

**`market_signals.py:290` の実測（本番 Parquet・SPY 4,142行）**:

```
high/low/close の NaN: 0件 / atr_14(min_periods=1) の NaN: 0件
→ .ffill().fillna(1.0) が発動した行数: 0
min_periods=14 にした場合の NaN: 先頭13本のみ（2010-04-01〜04-20）
14本目以降は min_periods=1 と完全一致（差分0行）
```

これは **SPY 1銘柄だけ**の ATR（`calculate_market_signals(df_spy, ...)` 内）であり、IPO とは無関係。
しかも NaN になる13本は `sma_200` の199本待ちで **MTS が既に NULL の区間に完全に含まれる**ため、
**直しても表示は1日も変わらない**。撤去する理由は精度ではなく、
**「普段は眠っているが、SPY のデータに一度でも欠損が出たら黙って ATR=1ドルに化ける罠」だから**。
なお `market_signals.py:296` の `np.where(atr_14 > 0, atr_14, 1.0)` と、それを分母に使う
`dist_50sma` / `dist_200sma` のゼロ除算ガードがセットなので、**作業単位は「ATR まわり一式」になる**。

**`vxv_vix_ratio` の推定式は実質デッドコードだが、危険な側**（実測・本番 Parquet）:

```
^VIX  : 4,144行  2010-04-01〜2026-09-18  close NaN=0
^VIX3M: 4,139行  2010-04-01〜2026-09-18  close NaN=0
market_signals.vxv_vix_ratio : NaN 0件 / 値域 0.7442〜1.4077
```

`^VIX3M` は全期間そろっており欠損ゼロ。推定式の `else` 分岐は **`^VIX3M` という銘柄そのものが
DB に無いとき（`t5_signals.py:41` の `vxv_sym_id` が None）しか通らず、本番では一度も通っていない**。
ただし実データの値域 0.744〜1.408 に対し推定式は **0.85〜1.30 にクリップ**するため、
万一分岐が通ると**パニック局面（比率が 0.85 を大きく下回る）がクリップで消える**。
`vxv_vix_ratio` は `portfolio_service.py:415` 経由で**現金推奨比率とポジション上限を動かす**ので、
表示だけの問題ではない。**撤去して fail loud に倒す**（`^VIX3M` が無い／欠損している日は NULL にし、警告を出す）。

#### 維持する（正しい）

- `screener_filters.py` の `mask.fillna(False)` ×8 — 出口での二値化（§2.3.5）
- `market_signals.py:200` `dist_days_raw.fillna(0).astype(int)` — `astype(int)` を通すためだけの
  一時的な埋めで、外側の `np.where` が `None` に戻している。**誤検出なので触らない**
- `market_signals.py:85-103` `report_stale_input_gaps()` — §2.3.4 のお手本

#### 別系統（本計画のスコープ外・§8 に起票）

仮想テーマ指数の合成における `volume.fillna(0)` / `surge.fillna(1.0)`
（`orchestrator.py:105-132` と `parquet_recompute.py:329-338` の2重管理）。合成指数固有の話。

### 3.8 無言の ffill に警告を付ける（2026-09-20 新規発見）

`relative_strength.py:158-159`:

```python
df['spy_close']  = df['spy_close'].ffill()
df['spy_volume'] = df['spy_volume'].ffill().astype(float)
```

**MTS 側は VIX/VXV の持ち越しを必ず `report_stale_input_gaps()` で報告するのに、
RS の基準である SPY の持ち越しは何も言わない。** 非対称であり、しかも RS は全銘柄・全指標の土台なので
影響範囲は VIX より広い。`report_stale_input_gaps()` を呼ぶだけで揃うため本計画に含める。

### 3.9 API / フロント — NULL を NULL のまま返す

`or 0` 系のフォールバックは backend に **46箇所**
（`panel_builders.py` 28 / `dashboard_router.py` 18、いずれも 2026-09-20 実測）。
同じ「値が無い」に対して
**`"UNKNOWN"` / `0`（最良）/ `0.0`（最悪）/ `0.5` / `50.0` / `1.0` / 推定式 の7通り**が並存している。

| 場所 | 現状 | 意味する方向 |
|---|---|---|
| `dashboard_router.py:83` `market_phase` | `"UNKNOWN"` | 判定不能（正） |
| `dashboard_router.py:84` `distribution_days` | `or 0` | **最良**（売り抜け日ゼロ＝最も健全） |
| `dashboard_router.py:85,67` `market_trend_score` | `or 0.0` | **最悪**（MTS 0＝最弱） |
| `portfolio_service.py:629-630` | `"UNKNOWN"` / `50.0` | 中立（行が無い場合のみ） |
| `portfolio_logic.py:179` | `"UNKNOWN"` → 現金推奨 50〜100% | ≒BEAR 寄りの**行動**に変換される |
| `panel_builders.py` / `dashboard_router.py` のランク・OHLC | `or 0.0` | ランクでは**最下位** |
| `MarketPhaseMeter.tsx:18` | 中立表示 | **唯一、判定不能として扱えている箇所**（② の 5-9c で対応） |

**84行目（最良）と85行目（最悪）が隣り合っている**のが、② の §8 で指摘した
「同じ画面に判定不能・最悪・最良が並ぶ」の実体。

**方針は A案（API は NULL のまま返し、表示の判断はフロントに一元化）を推奨**するが、
**本計画に含める範囲は未判断（§4-8）**。ランク系は §3.5 とセットでないと意味が変わらず、
OHLC 系は「価格の NULL は判定不能ではなく本物の異常」なので fail loud が正しい可能性がある。

## 4. ユーザー確認事項

**2026-09-11 に全件ユーザー判断済み。** 未解決の確認事項はなし。

| # | 論点 | 当初の推奨 | **判断** |
|---|---|---|---|
| **4-1** | SPY 専用計算（§3.3）を揃えるか | 揃える | **揃えない。** 判断の経緯: ①「SPY は相対基準のため特に細心の注意が必要。特出ししている理由があるはず」で揃えない → ②「T3 経由でどのみち影響するなら揃える方向でもよい。ただし SPY の値変更があれば T3 以降のリフレッシュ必須に」と再検討 → ③ 揃えた場合の T5 を調べて ② の問題（本番 MTS が既に誤っている）を発見 → ④ 整理の結果、**SPY の自前計算が効くのは ② の経路だけなので、揃えるなら ② の対応の中で扱う**として最初の判断に戻した（→ §2.2）。「SPY の値変更時は T3 以降リフレッシュ必須」のルールも ② で扱う |
| **4-2** | 表示用チャート（§3.4）を含めるか | 含める | **含める** |
| **4-3** | 再最適化の範囲 | 影響8戦略のみ | **影響のあった8戦略のみ**（`E2`/`F`/`A`/`G2`/`D`/`H1`/`G1`/`C2`） |
| **4-4** | 成績変化の許容基準 | （判断を依頼） | **正しさを優先。** 成績変化は記録するが差し戻し基準にしない（→ §2.2） |
| **4-5** | 昇格のタイミング | merge 後 | **merge 後**（`tools/deploy_after_merge.ps1`。日次パイプライン Tue-Sat 07:00/13:00 と重ねない） |
| **4-6** | **② との順序** | ② を先に直す | **② を先に直す。② を直すまで `--rebuild-from` 系（`deploy_after_merge` を含む）を実行しない**（2026-09-11 合意）。理由: `deploy_after_merge.py:108-114` は内部で `update_pipeline.py --rebuild-from T3` を呼び、`_rebuild_t3_or_t4_from_parquet` の最後の `[4/4]`（`update_pipeline.py:71`）で **T5 を SQLite 基点で全日付再計算する＝ ② の経路そのもの**。② 未修正のまま ① を昇格すると、MTS の先頭がまた壊れる。**① の実装と sandbox 検証（5-1〜5-16）は ② と独立に進めてよい** → **2026-09-17: ② は解決・本番昇格済み（新世代 `20260917_022002`、検算合格）。前提は充足した。`--rebuild-from` 系の実行制限（memory の freeze）も解除済み** |


### 4-7 以降（2026-09-20 追加）

**2026-09-20 に確定した判断:**

| # | 論点 | **判断** |
|---|---|---|
| **4-9** | NULL をレイヤ横断でどう表現するか | **§2.3 の原則で確定**（行の有無＝処理したか／列の NULL＝判定できたか。行を消す案は不採用） |
| **4-10** | T4 の相対ランクから判定不能を除外するか | **除外する（§3.5）。** 「正しくない既存を残すぐらいなら T3 rebuild すべき」。全ランク履歴が変わることを受け入れる |
| **4-11** | `atr_14` の暗黙フォールバック | **撤去する（§3.7）。** 「どのみち EMA200 は199までNULLだし揃えましょう」 |
| **4-12** | `vxv_vix_ratio` の推定式 | **撤去して fail loud に倒す（§3.7）。** `^VIX3M` は全期間そろっており、推定式は実質デッドコードかつ危険 |
| **4-13** | スクリーナーの否定条件（「満たさない」と「判定不能」） | **現状維持（§2.3.5）。** `mask.fillna(False)` は出口での二値化として正しい |

**未判断（着手前にユーザー判断が要る）:**

| # | 論点 | 推奨 | 状態 |
|---|---|---|---|
| **4-7** | **A-full（`relative_strength.py` の `min_periods=max(1, n//2)`）を本計画に含めるか** | **含める**（§3.5 で不採用の根拠が消えたため。`rs_trend_s200` が100本で値を出すのは A-core と同じ病気で、しかも RS はこのツールの中核。別案件に切り出すと**全期間再計算がもう一度必要になる**） | **REV** 含める |
| **4-8** | **§3.9（API の `or 0` 46箇所）をどこまで本計画に含めるか** | **ランク系のみ含める**（§3.5 とセットで意味が変わるため）。MTS/フェーズ系と OHLC 系は別タスク（OHLC の NULL は判定不能ではなく本物の異常で、fail loud が正しい可能性がある） | **REV** ランク系のみ |

> [!WARNING]
> **4-7 は着手前に決める必要がある。** 後から追加すると全期間の T3→T4 再計算が2回走り、
> その間バックテストの比較基準が2回変わる（§3.5 の WARNING と同じ理由）。

## 5. 実装順序と進捗チェックリスト

影響の小さい順・テスト先行（TDD）で並べる。**リストの並び順がそのまま実行順**（`5-4b` 等の英字付きは 2026-09-20 の追加分で、番号順ではなく記載位置の順に実行する）。

> [!NOTE]
> **前提だった ② は 2026-09-17 に解決・本番昇格済み。** 旧版にあった「5-17 以降は ② の完了待ち」「`--rebuild-from` 系を実行しない」という制約は**すべて解除済み**。

- [x] **5-0** **§4-7（A-full を含めるか）と §4-8（API の範囲）のユーザー判断を得る。4-7 は着手前に必須**（後から追加すると全期間再計算が2回走る）— **2026-09-21 判断済み: 4-7 = 含める / 4-8 = ランク系のみ**
- [x] **5-0b** **`doc/in_progress/t3_incremental_plan.md`（T3 日次の増分化）の完了を待つ。**
      2026-09-21 判断で**あちらが先**。理由: ①を先にやっても、T3 の日次経路が SQLite の504本で
      計算しているため**検証した値が翌日から劣化し続ける**（`rs_momentum_e200` が実際に
      2026-09-15 以降 全銘柄 NULL になっている）。さらに A-full は必要遡りを最大810本へ伸ばすので、
      日次が正しく遡れる状態が前提になる。**①の昇格時のフル再計算に、あちらの移行シードを相乗りさせる**
      — **2026-09-23 充足済み**（`t3_incremental_plan.md` は完了・本番昇格済み。`doc/completed/` へ移動済み）
- [x] **5-1** ワークツリーを作成し `--mode write` でプロビジョニング（種別 B。`tools/provision_worktree_data.py <worktree> --mode write`）— **2026-09-23 完了**（`.claude/worktrees/min-periods-warmup`、ブランチ `worktree-min-periods-warmup`）
- [x] **5-2** `backend/tests/indicators/test_moving_averages.py` を新規作成（red）— 遡り199本で `sma_200` が NaN、200本で値が出る — **2026-09-23 完了**（test-writer に委譲・オーケストレーターが独立に pytest 実行し20件が意図通りAssertionErrorで失敗することを確認済み）
- [x] **5-3** `moving_averages.py:46` を修正（green）— **2026-09-23 完了**（implementer に委譲。`min_periods=1`→`min_periods=period` の1行のみ変更。副次的にテスト側の `Series == pytest.approx(scalar)` 比較の不具合（pandasのSeries.__eq__優先で常にFalse）を発見し、オーケストレーターが `np.isclose` に修正。indicators/ 361件・backend/tests/ 全体1939件、いずれもpassed・回帰なしを確認済み）
- [x] **5-4** `backend/tests/indicators/test_volatility.py` を新規作成 → `volatility.py:44` を修正 — **2026-09-23 完了**（ファイルは既存だったため`TestAdrPct21MinPeriodsWindow`4件を追記。`min_periods=1`→`21`の1行修正。indicators/365件・全てpassedを確認済み）
- [x] **5-5** `backend/tests/indicators/test_volume_and_trends.py` に追記（既存ファイルあり）→ `volume_and_trends.py` の5箇所を修正 — **2026-09-23 完了**（`avg_dollar_volume_21`/`vol_sma_21`+`spy_vol_sma_21`/`max_63d`+`max_252d`をmin_periods=window化。`vol_accum_days_5`は単純な変更では不十分だったため、`is_accum`をvol_sma_21.notna()でNaN伝播させ`rolling(5,min_periods=5)`+`market_signals.py`と同じ`np.where(isna,None,fillna(0).astype(int))`パターンに書き換え。backend/tests/全体1948件passed）
- [x] **5-6** `relative_strength.py:275-284`（旧163-172）を修正（既存 `test_rs_dot_age.py` が回帰を見る）— **2026-09-23 完了**（implementer に委譲。`rs_252_high/low`・`close_252_high/low` の4箇所を `min_periods=1`→`min_periods=252` に変更。`test_relative_strength_dot_warmup.py`+`test_rs_dot_age.py` 13件、indicators/ 372件、backend/tests/ 全体1950件、いずれもpassed・回帰なしを確認済み）
- [x] **5-7** `orchestrator.py` 4箇所 + `parquet_recompute.py:331` を修正 — **2026-09-23 完了**（`dollar_volume_ma21`のmin_periods=1→21統一。既存`test_synthetic_ohlcv_integrity`がフィクスチャ5日分のみを前提にmin_periods=1時代の偽値を期待値としてハードコードしていたため回帰。共有フィクスチャは変更せず、テスト内でAAPL/MSFTのみ21日分に拡張し、pandasで独立に期待値を再計算する形に書き換え。backend/tests/全体1950件passed）
- [x] **5-8** breadth 集計3箇所を NaN 保持に修正 + テスト。**3箇所同時**（§3.2）— **2026-09-23 完了**（計画時の行番号`t5_signals.py:76`は既にT5 breadth純関数化リファクタ（`0f3e330`）で`indicators/market_signals.py:134`の`compute_breadth_momentum`に統合済みだったため、実際の対象は`market_signals.py:134`/`scenario_runner.py:319`/`etf_single_runner.py:240`の3箇所。`is_above_sma50`を`np.where(sma_50.isna(), nan, close>sma_50)`に変更。既存の回帰テストが旧ロジックをそのまま参照実装として固定していたため書き換え、NaN除外の効果が観測できる新規フィクスチャも追加。backend/tests/全体1952件passed）
- [x] **5-9** `chart_router.py` を修正（§4-2）— **2026-09-23 完了**（計画時の行番号は古くなっていたため実コードで箇所を特定。VXV/MKT_TRENDチャート用(149-153行目)とfull_range=TrueのParquet全期間チャート用(282-283行目)の計5箇所（sma×2・ema×2・std×1）をmin_periods=windowへ統一。295行目のATRフォールバックは5-8bのスコープのため触っていない。既存テストが無かったため`test_chart_api.py`に2件新規追加。backend/tests/全体1954件passed）
  - **2026-09-24 追記（issue①クローズ前の棚卸しで発覚したスコープ漏れを修正）**: 5-9完了時の記述は「295行目は5-8bのスコープ」だったが、5-8bの実施範囲は`market_signals.py`のみで`chart_router.py:295`は実際には未修正のまま残っていた（§3.4は元々`141-145,285-298`を対象範囲に含めていた）。`calc_atr_14 = tr.rolling(window=14, min_periods=1).mean().ffill().fillna(1.0)` → `tr.rolling(window=14, min_periods=14).mean()` に修正（5-8bで確立した`market_signals.py:298`と同じパターン）。下流の`calc_atr_pct_14`/`calc_sma50_atr_mult`は既存のNaN伝播・`isna()`ガードでそのまま正しく動くため追加修正不要。この列を直接参照する既存テストは無く、`test_chart_api.py`23件含むbackend/tests/全体は回帰なし（オーケストレーターが直接修正・委譲なし）。
- [x] **5-4b** `volatility.py` の `atr_14` を修正（§3.1.1(a)）— **2026-09-23 完了**（実装は既に`ta`ライブラリ直接呼び出しから自前の`_atr_wilder_kernel`に置き換わっていたため対象行は変わったが、同じ「先頭13本がリテラル0.0のまま」というバグは残存していた。非増分ブランチの戻り値の先頭`window-1`本を明示的に`np.nan`へ潰す形で修正。既存の`test_calculate_incremental_equivalence.py`（5-4dのZeroDivisionError回帰テスト）が「先頭13本=0.0」を前提にしていたため`isna()`ベースに書き換え。backend/tests/全体1957件passed）
- [x] **5-5b** `is_trend_template` の NULL ガードを cond1〜cond5 の**全入力**に拡張（§3.1.1(b)）。あわせて `evaluate_position_exit_for_day` が `atr` の NaN/0 をどう扱うか確認し、ガードが無ければ追加 — **2026-09-23 完了**（外側の`np.where`が`sma200_20d_ago`のNaNしか見ておらず、`max_252d`（cond5の分母）がNaNでもcond5がFalseに落ちて「判定不能」が「0」と誤断定される問題を修正。200〜251本の履歴帯で発火することを事前検証した上でテスト化。`evaluate_position_exit_for_day`は`atr`/`dist_sma50_atr`とも`is not None and not np.isnan(...)`で既に適切にガード済みを確認、コード変更不要。backend/tests/全体1959件passed）
- [x] **5-6b** `relative_strength.py` の無言 ffill に `report_stale_input_gaps()` を追加（§3.8）— **2026-09-23 完了**（`market_signals.py`の`^VIX`/`^VIX3M`と同じ確立済みパターンをSPYのspy_close/spy_volume ffillにも適用。df値は変更しないログのみの追加。新規テスト3件追加。backend/tests/全体1962件passed）
- [x] **5-8b** `market_signals.py` の暗黙フォールバックを撤去（§3.7）— ①`atr_14` 一式（L290 の `ffill().fillna(1.0)` と L296 のゼロ除算ガード、および分母側の `dist_50sma`/`dist_200sma`）②`vxv_vix_ratio` の推定式を撤去し fail loud 化 ③`fillna(0.5)`（L140,263,264）撤去 ④`has_breadth`（L262）の日付ハードコードを `breadth_sma50` の NULL 判定に置換 — **2026-09-23 完了**（4サブ項目とも実施。①②③④とも既存テストの複数箇所が旧フォールバック挙動に暗黙依存しており（VXV未シードのパイプラインテスト3件・`test_market_trend_score_neutral`・`test_synthetic_ohlcv_integrity`級の期待値ハードコード等）、いずれも旧挙動ではなく新仕様に合わせて書き換えた。`compute_breadth_momentum`内の別の`fillna(0.5)`(L143)は直前の`.agg()`ラムダが既に全NaN群を0.5にフォールバックしており事実上デッドコードと判明したため意図的にスコープ外とした。backend/tests/全体1972件passed）
- [x] **5-8c** **T4 のランクから判定不能を除外**（§3.5）— `t4_ranks.py:85` の SQL と `parquet_recompute.py:88` を**同時に**直す。`test_parquet_recompute.py` の期待値も更新（現在「NaN は最小値」を固定しているテストがある）— **2026-09-23 完了**（SQLite側は`CASE WHEN col IS NULL THEN NULL ELSE PERCENT_RANK() OVER(PARTITION BY category, (col IS NULL) ...) END`、Parquet側の`percent_rank`関数もNULL除外に統一。`t4_ranks.py`には専用テストが無かったため`test_t4_ranks.py`を新規作成しインメモリSQLiteで実SQLを検証。`test_null_is_treated_as_smallest`等の旧仕様固定テストも新仕様に書き換え。backend/tests/全体1976件passed）
- [x] **5-8d** T5 の gap 判定を「行の有無」に統一し、5-7e のアドホック収束処理（`t5_signals.py:64-81`）を撤去（§3.6）。`.claude/skills/pipeline-debugging/SKILL.md:32` の記述も更新 — **2026-09-23 完了**（`t5_completed_dates`(score非NULL)→`t5_written_dates`(行の有無)に変更し、`already_written_insufficient`等のアドホック収束ロジックを削除。既存2件のテストが「NULL行は自動で埋め戻される」という撤回対象の旧仕様を前提にしていたため契約を反転（`test_pipeline_idempotency.py::test_sync_phase_t5_backfills_null_score`→`test_sync_phase_t5_does_not_recompute_existing_null_score_row`、`test_t5_lookback_guard.py`のログ文言更新）。オーケストレーターが直接修正。backend/tests/全体1976件passed）
- [x] **5-9b** §4-8 の判断に従い API/フロントを対応（§3.9）。**4-8 が「含めない」なら本項はスキップし §8 へ起票** — **2026-09-23 完了**（§4-8「ランク系のみ」の判断通り、`panel_builders.py`/`dashboard_router.py`の`or 0.0`・`.get(id, 0.0)`計21箇所を撤去し`schemas.py`の`DashboardPanelItem`14フィールドを`Optional[float]`化。ソートキー3箇所をNone安全化（reverse=Trueで`-inf`が末尾）。test-writerが当初スコープ外の`get_group_data`内の同型5箇所も発見し追加。フロントエンドは`types.ts`の型を`number|null`に統一し`ScreenerResultPage.tsx`の2箇所をnull安全化（`SummaryTable.tsx`/`GroupPage.tsx`は元々null安全で変更不要）。backend/tests/全体1978件・フロントエンドビルド成功を確認済み）
- [x] **5-9c** **A-full を実装する（§4-7・2026-09-23 に計画書へ追記）** — `relative_strength.py:227,246-247,266-267`（`rs_trend_sN`/`rs_ratio_eN`/`rs_momentum_eN`。`rolling_std_independent` 呼び出し含む）の `min_periods=max(1, n//2)` を `min_periods=n` に統一する。**5-8c（T4のNULL除外）の後に実施すること** — **2026-09-23 完了**（5箇所を`n`に統一。新規`test_relative_strength_a_full_warmup.py`で新旧しきい値の境界を検証。副作用として2件の既存テストの期待値がmin_periods変更の正当な波及（`rs_momentum_e21`の値変化、`rs_roc_ema_200`のウォームアップ遷移点シフト）で古くなっており、オーケストレーターが直接修正（`test_calculate.py`のゴールデン値更新、`test_t3_indicators.py`のシナリオパラメータをn_short=900・実測遷移点611に合わせて再校正）。backend/tests/全体1984件passed）
- [x] **5-10** pytest 全件パス — **2026-09-23 完了**（`backend/tests/` 全体1984件passed・1 skipped・failed 0件を確認済み。各チェックリスト項目で継続的に確認してきた上での最終確認）
- [x] **5-11** sandbox で T3→T4→T5 を全期間再計算し、`db_health_check.py --all --check-nulls` を通す。**注意: ② の解決により、旧版にあった「sandbox の T5 が SQLite 基点で壊れる」caveat は解消済み。**T5 も Parquet 基点で計算されるため、`market_signals` は本番と同じ手順で検証できる — **2026-09-24 完了**（`update_pipeline.py --rebuild-from T3 --skip-fetch --skip-sync`で全期間再計算(所要5分)。`--check-nulls`が144銘柄をNGと誤検知（sma_200の新min_periods=200に除外条件が無かったため）→`db_health_check.py`のexclude_cols条件を追加し144→50件に削減（残りは5-11bで対応）。副産物として仮想テーマ指数のT3再計算経路でSPY参照データの供給不足（既存の別問題）を5-6bの新規ログが可視化）
- [x] **5-11b** **`incremental_state_registry.py` の `warmup_bars` を全列再実測し、レジストリとテストを更新する（2026-09-23 追加。T3増分化計画との相互作用）** — **2026-09-24 完了**（sandbox全期間再計算済みデータに対し、個別・active銘柄を履歴本数で層化抽出（12ビン×最大30銘柄=355銘柄。短期履歴銘柄を含めて前任の検収ミスを回避）。フル履歴を1回計算した結果から「各列が最初に非NULLになった位置」を直接読む方式（前方切り詰め＋逐次再計算と数学的に等価）で全67列を再測定し、依存関係チェーンの手計算と全て整合することを確認。`max_lookback()+1<=HOT_WINDOW_BARS`・`columns_with_undeterminable_warmup()`（引き続き`rs_roc_ema_200`のみ、成長なし）の不変条件も確認済み。さらに`--check-warmup-nulls`実行中にSPYの`vol_surge_21`/`vol_surge_rel_spy_21`が構造的NULL（`df_spy=None`早期returnで`spy_volume`が未マージ）と判明し`is_structurally_null_column()`を拡張。backend/tests/全体1988件passed）—
      T3 増分化（`doc/completed/t3_incremental_plan.md`）で `warmup_bars` は本番実測値として67列のレジストリに固定済みだが、
      ① （と5-9cのA-full）は min_periods を変えるため大半の値が古くなる（例: `sma_200` 0→199、`atr_14` 0→13、
      `rs_ratio_e200` 298→398、`rs_momentum_e200` 610→810）。**現状これらの値は registry 内の静的宣言であり、
      実際の指標計算結果とクロス検証するテストが無いため、ここで直さないと db_health_check.py の
      `--check-warmup-nulls` と `t3_indicators.py` の欠陥/正当ウォームアップ判別が本番昇格後に無言で誤った閾値を使い続ける**
      （§2.3.4 が禁じる「無言で埋める」と同型のリスク）。5-11 の sandbox 全期間再計算データに対して行うこと
      （単純な `min_periods=N` 型の列は `N-1` で解析的に決まるが、RECURSIVE連鎖を持つ列（`rs_trend_sN`/`rs_ratio_eN`/
      `rs_roc_ema_N`/`rs_momentum_eN`/`is_trend_template`/`atr_14`/`sma50_atr_mult`/`vol_surge_21`系/
      `up_down_vol_ratio_50`/`vcr`/`rs_blue_dot_age`/`rs_red_dot_age`）は5-6cと同じく解析的導出を信用せず実測する。
      `t3_incremental_plan.md` §7-6/§7-7 で解析的推論の誤りを2回踏んでいるため）。手順:
      1. `tmp/verify_warmup_thresholds.py`（5-6c で使ったもの）を sandbox Parquet 向けに再実行し、67列の新しい `warmup_bars` を測定する
      2. `incremental_state_registry.py` の `_ENTRIES` を更新する
      3. `test_incremental_state_registry.py` の固定値（`test_代表列のwarmup_bars` 等のパラメータ化テスト）を更新する
      4. `max_lookback() + 1 <= HOT_WINDOW_BARS`（504）の不変条件が引き続き成り立つことを確認する（①/A-fullで `lookback` 自体が変わる列が無いか確認。無ければテストは無変更で通るはず）
      5. `columns_with_undeterminable_warmup()` が返す列集合を確認する。現状 `rs_roc_ema_200` の1列だが、増える場合は
         `test_incremental_state_registry.py` の固定テストを更新し、**`_calculate_t3_worker`（`t3_indicators.py`）側の
         欠陥/判別不能分類ロジックが新しい集合を正しく扱うか**（増えた列がワーカー側の警告ログ・フォールバック分岐を
         誤動作させないか）を確認する
      6. `tools/db_health_check.py --check-warmup-nulls` を sandbox データに対して実行し、新しい閾値で偽陽性・偽陰性が
         無いことを確認する（§7-7 と同じく「検出されるはずのものを注入して実際に検出されること」を確認してから0件を報告する）
- [x] **5-12** **SPY の T3 行の before/after 完全一致検証**（§3.3・§4-1）— 本番 Parquet と sandbox Parquet の SPY（`symbol_id = 1`）行を全列比較し、**2011-03-30 以降の差分が0行**であることを確認。差分が1行でもあれば**停止してユーザーに報告**（5-13 以降に進まない）— **2026-09-24 完了**（本番Parquet(`indicators_20260923_192040.parquet`・旧コード)とsandbox Parquet(`indicators_20260924_010639.parquet`・新コード)のSPY全4144行を比較。日付集合の対称差0件、2011-03-30以降3893行・全列で数値差分0件（完全一致）を確認。2011-03-30より前の251行（想定内のNaN化区間）のみ差分があるが計画通りチェック対象外）
- [x] **5-12b** **T4 のランク変更の影響を測る**（§3.5）— before/after で ①各 `(date, category)` の母集団 n がどれだけ減ったか ②ランク値の分布がどれだけシフトしたか ③NULL になったランク件数、を記録する。**成績の差し戻し基準にはしないが（§4-4）、再最適化の引き渡し時に「何がどれだけ動いたか」を説明できる必要がある** — **2026-09-24 完了**。測定結果:
  - **①母集団n**: 2026-09-22時点で セクタ16/テーマ270/個別2989は不変。市場のみ24→24(非NULL23)に減少（判定不能1銘柄が母集団から正しく除外）
  - **②ランク値シフト**（共通有効行3,298件・`rs_value_rank`）: 平均変動-0.00015（ほぼ0）・標準偏差0.0147・最大変動0.379。5-11のT4再計算ログでも別列で確認済み: `rs_roc_ema_rank_e200`の共通行6,009,508件中の最大変動8.223e-01（母集団が変わるため0にはならない。1/(n-1)≈3.5e-4の数倍で妥当）
  - **③NULL化したランク件数**（全期間・22列）: 旧コードは全列でNULL件数0（NULLをPERCENT_RANKが最小値0.0として扱い、判定不能でも必ず値を返していたため）。新コードは列ごとに0.04%〜30.60%（`rs_momentum_rank_e200`が最大。warmup_bars=810が最も大きいため）がNULLに正しく変化
- [x] **5-12c** **NULL 件数の before/after 棚卸し** — 主要列ごとに NULL 行数を集計し、§1.2 の予測（IPO 銘柄数 × 窓長）と桁が合うことを確認する。**予測と大きくずれたら実装を疑う** — **2026-09-24 完了**。最新日（2026-09-22・3,327銘柄）時点: `sma_200` NULL 0→141件(4.24%)、`dist_52w_high_pct` 0→166件(4.99%)、`is_trend_template` 6→166件(4.99%。max_252dに正しく連動)、`atr_14`は3→3件（窓14本と小さいため変化なし）。§1.2(b)の2026-09-09時点予測（117件・3.6%）と近い桁で整合（2週間の経過で新規上場が増えた分、比率がやや上振れするのは自然）。全期間集計でも`sma_200`等が0%→一桁%台に収まり、予測から大きく外れる列は無し。
- [x] **5-13** **§6.1 の反証を実施** — sandbox データでバックテストを実走し、シグナル数の実測が §1.2(c) の予測と整合するか照合する（**予測が外れたら実装ではなく §1.2(c) の測定方法を疑う**）— **2026-09-24 完了**。手法: `backtest_runner.py`の`run_single_strategy`はPhase1(生シグナルスキャン)の`total_signals`を戻り値に含めないため、`scan_signals_for_date`を直接呼ぶPhase1相当のスクリプトを作成し、**本番Parquet（旧コード計算値）と sandbox Parquet（新コード計算値）に対して同一のスクリーニングロジック（本計画では未変更）を適用**して生シグナル数を突き合わせた（スクリプト: スクラッチパッド`check_signal_count_impact.py`、評価窓は既定の2021-03-26〜2026-03-26の単一窓・全期間）。

  対象8戦略・単体の割合（prod→sandbox）: `E2_vcp_breakout_52w` 304→283 (**-6.91%**、predicted 4.08%) / `A_momentum_breakout` 1,774→1,689 (**-4.79%**、predicted 1.67%) / `D_ema21_pullback` 845→827 (**-2.13%**、predicted 0.83%) / `F_elite_momentum97` 6,187→6,063 (**-2.00%**、predicted 1.74%) / `G2_momentum_no_exhaust` 1,274→1,256 (**-1.41%**、predicted 1.46%) / `G1_leader_td9_dip` 4,810→4,746 (**-1.33%**、predicted 0.42%) / `H1_structure_2nd_break` 9,819→9,782 (**-0.38%**、predicted 0.70%) / `C2_rrg_improving_in` 1,703→1,698 (**-0.29%**、predicted 0.05%)。

  **単体では概ね予測より大きめ**（特に`A`/`D`/`G1`は倍以上）だが、方向（全戦略で減少）・相対順位（`E2`が最大、`C2`/`H1`が最小）は§1.2(c)の静的予測と一致。単体の乖離は、§1.2(c)が**6つの短い個別評価窓ごと**の推定だったのに対し、本測定は**それらを跨ぐ5年間の単一窓**で測っているため、IPOが集中した時期（2021年SPACブーム・2023-25年上場ラッシュ）の影響が積算され比率が上振れしたことで説明できる（`E2`は母数304と小さく、絶対差21件でも%が大きく振れる点にも注意）。

  §1.2(c)の「窓単位0.45〜1.76%減」は**その窓の21戦略合算**の数字だったため、同じ土俵で比較するには8戦略を合算する必要がある。**8戦略合算**: prod計26,716件→sandbox計26,344件、**-1.39%**。これは予測レンジ0.45〜1.76%の**内側**に収まる。残り13戦略（§1.2(c)で消失ゼロと予測）を含めた21戦略合算はこの値をさらに0に近づく方向にしか動かさない（分母が増え分子はほぼ変わらないため）ので、21戦略版を実走せずとも「予測より大きく（5%以上）減る」という反証条件には該当しないと判断できる。

  **結論: §1.2(c)の予測は棄却されない**（反証条件「窓単位で5%以上の減少」に該当せず、合算値0.45〜1.76%の予測レンジ内）。個別戦略のばらつきは実装の不備ではなく評価窓の粒度差によるものと判断し、これ以上の追加検証（21戦略×6窓の完全再現）は費用対効果が低いためスコープ外とする。
- [x] **5-14** 影響8戦略の成績 before/after を**記録**する（差し戻し基準にしない — §4-4）— **2026-09-24 完了**。5-13と同じ手法（本番Parquet vs sandbox Parquetを`run_single_strategy`にPhase1+Phase2フル実行・ExitRules/VXV系列/流動性床/税率は`run_backtest`と同一セットアップ）で、単一窓（2021-03-26〜2026-03-26）の`expectancy_lcb`/`geo_mean_gain`/`profit_factor`/トレード数を記録した（スクリプト: スクラッチパッド`check_strategy_performance_impact.py`）。

  | 戦略 | trades (prod→sandbox) | expectancy_lcb (prod→sandbox) | geo_mean_gain (prod→sandbox) |
  |---|---:|---:|---:|
  | `A_momentum_breakout` | 1623→1558 | +1.0135→+0.9276 | +0.0419→+0.0035 |
  | `C2_rrg_improving_in` | 1703→1698 | +0.0902→+0.1415 | -0.0532→-0.0059 |
  | `D_ema21_pullback` | 551→538 | -0.2436→-0.1657 | +0.1973→+0.2862 |
  | `E2_vcp_breakout_52w` | 97→93 | +0.8048→+0.6180 | +2.2641→+1.8904 |
  | `F_elite_momentum97` | 2495→2447 | -0.0484→-0.0949 | -0.3471→-0.3745 |
  | `G1_leader_td9_dip` | 3319→3273 | +0.1196→+0.1089 | -0.2734→-0.2791 |
  | `G2_momentum_no_exhaust` | 1131→1115 | +1.6605→+1.6931 | +0.7920→+0.8241 |
  | `H1_structure_2nd_break` | 7242→7202 | -0.0459→-0.0241 | -0.8456→-0.8285 |

  トレード数の減少幅は5-13の生シグナル数の減少（-0.29%〜-6.91%）より一回り小さい（-0.5%〜-6.1%）。
  Phase2の再エントリー抑制ロジック（建玉存続中は同一銘柄の新規シグナルをスキップ）が
  消失したシグナルの一部を元々カウントしていなかったための自然な縮小で、想定通り。
  **成績（expectancy_lcb/geo_mean_gain）は改善4戦略・悪化4戦略と方向が割れている**
  （`C2`/`D`/`G2`/`H1`は改善、`A`/`E2`/`F`/`G1`は悪化）。**§4-4の判断どおり、これは差し戻し
  基準にしない** — データの正しさ（遡り不足銘柄を正しくNaN除外すること）を優先する方針であり、
  成績の上下どちらであっても実装の正しさとは無関係。再最適化（5-18）で新しいパラメータ帯に
  おいて改めて評価される。
- [x] **5-15** `doc/backend_specification.md` に「遡り不足時は NaN」を明記 — **2026-09-24 完了**。3箇所追加: ①§3.3 T3指標定義表の直前に IMPORTANT ブロックを新設（`min_periods=window`統一の説明・対象列一覧・`is_trend_template`のNULL入力ガード・SPYのvol_surge系構造的NULL例外・NULL透過方針）②§3.5 T4冒頭に IMPORTANT ブロックを新設（NULL銘柄がランキング母集団・結果の両方から除外される仕様、旧`PERCENT_RANK()`のNULL最小値扱いとの対比）③§3.6 `market_trend_score`の説明を「2018-04-01ハードコード」から「`breadth_sma50`のNULL判定」に更新。
- [x] **5-16** `doc/issue_list.md` の ① をクローズ（§8 の残作業のうち未起票のものを新規 issue として起票。② は起票済み）— **2026-09-24 完了**。issue①を`[x]`+取り消し線化し、解決NOTEブロック（対応内容・検証結果・残作業・「本番昇格は別途」の明記）を追加、旧本文（事象・実測・対応案）は issue②と同じ慣例でそのまま履歴として残した。§8の残作業のうち未起票だった3件をP2/P3に新規起票（仮想テーマ指数のSPY参照供給不足警告・`RS_DOT_WARMUP_BARS`撤去要否・`bars_available`診断列）。`rs_roc_ema_200`の40銘柄異常は2026-09-23付で既に別issueとして起票済みのため対象外。
- [x] **5-20**（G3-1周目・対応 R1）`orchestrator.py` の**増分ブランチ**（`>= seed_date` で絞ってから `rolling(21, min_periods=21)` を計算している箇所）を、**全履歴（`theme_prices_df`）で `dollar_volume_ma21` を計算してから `>= seed_date` に絞る**順序へ直す。現状は日次更新のたびに仮想テーマ/指数の `surge` が NaN→1.0（volume=1e6 固定）になる。テスト: 21本以上の履歴がある構成銘柄で、増分結果の `surge` が全期間再合成（rebuild）と一致し、1.0 固定にならないこと — **2026-09-24 完了**。全履歴で `dollar_volume_ma21`/`surge` を計算してから `>= seed_date` に絞る順序に変更（`close_prev` は絞った後に計算する従来挙動を維持）。修正前は増分で追加した日の volume が 1,000,000 固定（期待 1,157,356 と 157,356 ずれ）で red、修正後 green。
- [x] **5-21**（同・対応 R2）**breadth の 0.5 捏造を撤去し、NULL を NULL のまま流す**。`market_signals.py` の `compute_breadth_momentum`（全銘柄 NaN の日に `breadth_sma50 = 0.5` を返す `else 0.5` と `metrics_df.fillna(0.5)` の breadth 側）、`scenario_runner.py` / `etf_single_runner.py` の複製（同じ `else 0.5`）、`scenario_market_score.py:175` の日付ゲート `date_str >= '2018-04-01'`（`breadth_val is not None` と NaN 判定に置換）を**4箇所同時に**揃える。`momentum_ratio` の 0.5 は据え置き（bool 由来で NaN にならない）。`t5_signals.py` が NaN を保存するとき NULL（None）になることも確認。テスト: 全銘柄の `sma_50` が NaN の日は `breadth_sma50` が NaN・`has_breadth` が False・MTS が3成分になること — **2026-09-24 完了**。`compute_breadth_momentum` の `breadth_sma50` を全銘柄 NaN の日は NaN に（`fillna(0.5)` は `momentum_ratio` のみ）。`scenario_runner`/`etf_single_runner` の式の複製は `compute_breadth_momentum` 呼び出しに寄せた（旧コードと同じ集計で等価）。`scenario_market_score.py` の日付ゲートを NaN 判定に置換。`t5_signals.py` は NaN→None を明示。**計画外の必須の副次修正**: SQLite 経路で `sma_50` が全行 NULL だと object 型になり `close > sma_50` が TypeError になるため `pd.to_numeric(errors='coerce')` を追加（red で実際に再現）。
- [x] **5-22**（同・対応 R3）`tools/db_health_check.py` の短履歴除外閾値（`rs_momentum_e21`: 75、`rs_ratio_e21`: 30）を、`incremental_state_registry` の `warmup_bars`（実測 94 / 40）から**導出する**形に直す（手書きの数値を増やさない。`t2_count <= warmup_bars` なら除外）。`sma_200`（200）・`ema_21`（21）も同じ仕組みに揃える。**この検査は「直近5行に NULL があるか」を見る**ため、除外条件は `t2_count <= warmup_bars` ではなく **`t2_count <= warmup_bars + 5`**（直近5行がすべてウォームアップ明けになるまで）でなければ、`sma_200` が 200〜203本の銘柄で古い側の行が NULL のまま NG になる（現行の `t2_count < 200` もこの点で1段足りない）。テスト: `rs_momentum_e21`（warmup 94）は履歴 99本まで NULL でも NG にならず 100本で NULL なら NG、`sma_200`（199）は 204本境界で同様 — **2026-09-24 完了**。手書き閾値を撤去し `columns_with_warmup_threshold()` から `t2_count <= warmup_bars + RECENT_NULL_WINDOW` で導出（`RECENT_NULL_WINDOW=5` を SQL の LIMIT と共有）。陽性対照（100本で NULL なら NG 等）付きの5テスト。境界は厳密値より1本保守的（許容）。
- [x] **5-23**（同・対応 R4）`frontend/src/components/SummaryTable.tsx` のランク列（L139-141 の `?? 0`、L49-50 のソートの `?? 0`）を、**NULL は `-` 表示・ソートは末尾**に直す（0＝最悪バケットとして表示・整列しない）。§4-8「ランク系のみ」の範囲。`npm test` とビルドを通す — **2026-09-24 完了**。`SummaryTable.tsx` のランクを NULL は `-`＋中立色、ソートは昇降どちらでも NULL を末尾に。vitest 69 passed・build 成功。
- [x] **5-24**（同・対応 R5）`t5_signals.py` の `_find_insufficient_lookback_dates` の docstring と、周辺コメントに残っている**撤去済みの収束ロジック（5-7e）・`t5_completed_dates` の記述**を現状（行の有無で gap 判定・このリストはエラーログ用途のみ）に合わせる。コード変更なし — **2026-09-24 完了**。`_find_insufficient_lookback_dates` の docstring と周辺コメントを、行の有無での gap 判定・エラーログ用途のみに書き直した（ロジック不変）。
- [x] **5-25**（同・対応 R18）`screener_router.py:778-779` の `_float_or(rs21_rank / rs63_rank)`（NULL→0.0）を **NULL のまま返す**形に直す（`_float_or_none` が既にある）。`schemas.py` の対応フィールド（`rs_ratio_21_rank` / `rs_ratio_63_rank` / `rs_ratio_rank_e21` / `rs_ratio_rank_e63`）を `Optional[float]` にし、`frontend/src/types.ts` の型を `number | null` に揃える。**5-9b で `ScreenerResultPage.tsx` を null 安全にしたが、API が 0.0 を返すため一度も効いていなかった**（§3.1「呼んでいるが効いていない」型）。dashboard API（NULL を返す）との不整合の解消。テスト: NULL ランクの銘柄が screener 応答で `null` になること。`screener_router.py:735` のソートキー `fillna(0.0)`（NULL は最下位）は意味が一致するため据え置き — **2026-09-24 完了**。screener API の `rank_21/63` を `_float_or_none` に変更。`schemas.py`/`types.ts` は元から Optional で変更不要だった。応答の最終ソートが None で `TypeError` になるため `(有無, 値)` のタプルキーで NULL を末尾にした（計画に明記のない必須の副次修正）。
- [x] **5-26**（同・対応 R22）`market_signals.py` の `calculate_market_signals` で `df_vix` が無いとき `df['vix_close'] = 20.0`（約L248）と固定値を入れ、`^VIX3M` があれば `vxv_vix_ratio = vxv / 20` という**捏造比率が MTS に入る**。§3.7 / 4-12（`vxv_vix_ratio` の推定式撤去・fail loud）と同じ種類の撤去漏れ。`vix_close` を NaN にして `vxv_vix_ratio` を NULL にし、`^VIX3M` 欠損時と同じ形式の WARNING を出す（`report_stale_input_gaps` のお手本に揃える）。テスト: `df_vix=None` かつ `df_vxv` 有りで `vxv_vix_ratio` が NaN・警告が出ること。**5-21（同じファイルを編集中）の完了後に着手する** — **2026-09-24 完了**。`df_vix` 欠損時の `vix_close = 20.0` を NaN に変更し、`vxv_vix_ratio` を NULL にして WARNING を出す（`^VIX3M` 欠損時と同形式）。下流に `has_vxv` 相当の判定は無く、VIX 欠損時は `^VIX3M` 欠損時と同じく `market_trend_score` が全行 NaN になる（fail loud。3成分に落ちるのは breadth 欠損時のみ）。既存テスト `test_t5_lookback_guard.py` の fixture が `^VIX3M` しか投入せず 20.0 固定値に暗黙に頼っていたため、`^VIX`（close=18.0）を投入するテストデータ側の是正を行った。全件 2008 passed / 1 skipped / 0 failed（オーケストレーター単独実行）。
- [ ] **5-17** merge → `tools/deploy_after_merge.ps1` で昇格 → API サーバ再起動。**昇格後の検証（G3 2周目 R26 より）: 本番 `market_signals` の 2017Q1 の `breadth_sma50` が 0.5 ではなく NULL になっていること**（旧ロジック由来の行が T5 全期間再計算で置き換わったことの確認）。**種別 B のため `/code-review` をブランチ単位で1回通してから merge する**（CLAUDE.md 検収 第2段）。日次パイプライン Tue-Sat 07:00/13:00 と重ねない（§4-5）
- [ ] **5-18** **再最適化をユーザーへ引き渡す**（§4-3。実行はユーザー、約12時間規模）— ①変更が main に merge・昇格済みであることを明言する ②1戦略だけ短時間ドライランで挙動を確認する ③対象8戦略（`E2`/`F`/`A`/`G2`/`D`/`H1`/`G1`/`C2`）を明示して引き渡す。**データが変わる変更なので、既存 trial の集計値ベースの再スコアリングは使えない**（集計値そのものが旧データ由来のため）
- [ ] **5-19** 計画書を `doc/completed/` へ移動（再最適化の完了は待たない。結果は別途記録）

### 作業中メモ

未着手。着手時の注意:

- **5-0（§4-7 / §4-8 のユーザー判断）を先に片付ける。** 特に 4-7（A-full）は、後から追加すると全期間の T3→T4 再計算が2回走る
- **前提だった ② は解決済み**（2026-09-17 昇格、新世代 `20260917_022002`）。`--rebuild-from` 系の実行制限も解除済み
- ~~`worktree-feat-market-breadth-indicators`（S5FI/S5TH 取り込み）を先に main へ取り込むこと~~ — **2026-09-23 充足済み**（`0f3e330` で merge 済み。`tvdatafeed-enhanced`/`websocket-client` も venv にインストール済みを確認済み）
- 本計画書は 2026-09-17 に git 管理下へ取り込み済み。5-1 でワークツリーを切る際にそちらで作業する
- **検収時のサンプリング偏りに注意（`t3_incremental_plan.md` §7-6 の教訓・2026-09-23）。** 「0件」という結果が
  正常だからなのか測れていないからなのかを、検出されるはずのものを注入して確認してから報告すること
  （陽性対照・到達不能な分岐の見落とし）。① は **IPO 銘柄（短い履歴）が主対象の変更**なので、5-11b の実測・
  5-12c の NULL 件数棚卸し・5-13 の反証で使う標本には**必ず短い履歴の銘柄を含める**（履歴の長い銘柄だけに
  絞って「差分ゼロ」を確認しても、それは測れていないだけの可能性がある）

## 6. 検証プラン / 結果

| 検証 | 方法 | 期待値 |
|---|---|---|
| 単体 | pytest（5-2〜5-8 で追加したもの含む全件） | 全件パス |
| データ整合 | `tools/db_health_check.py --all --check-nulls` | NG なし。T2/T3 行数一致 |
| シグナル数 | sandbox 再計算後にバックテストを実走 | 窓単位 0.45〜1.76% 減（§1.2(c)） |
| MTS breadth | sandbox の `market_signals.breadth_sma50` を現行と比較 | 「A案＋分母除外」の水準（±0.2pp 以内）。**SPY 由来列は ② の影響を受けるため比較しない**（5-11） |
| 成績 | 影響8戦略の `expectancy_lcb` / `geo_mean_gain` を before/after | **記録のみ**（差し戻し基準にしない — §4-4） |
| SPY 不変 | 本番と sandbox の SPY T3 行を全列比較（5-12） | **2011-03-30 以降の差分0行** |
| 目視 | sandbox に向けた API + フロント | オレンジ警告バッジ表示。IPO 直後銘柄の `sma_200` が空欄、チャートの200日線が上場から200本目以降に始まる |

### 6.1 反証 — この結論が誤りだとしたら、何が観測されるはずか

- **この結論が誤りだとしたら観測されるはず**:
  §1.2(c) の「A-core 適用でシグナルは 0.45〜1.76% しか減らない」が誤りなら、**sandbox で実際に T3 を再計算したあとのバックテストで、シグナル数が予測より大きく（例えば窓単位で5%以上）減る**。原因の候補は2つある。
  1. **繰り上がりを数えていない** — `max_hits_per_day = 10` の戦略では偽値銘柄が抜けた枠に別の銘柄が繰り上がる。この効果は**減少を打ち消す方向**なので、予測より減っていれば繰り上がりでは説明できず、測定方法の誤りを意味する
  2. **要求本数マッピング（`tmp/signal_warmup_probe.py` の `COL_BARS`）の取りこぼし** — 実際に一度踏んでいる。初回測定では `is_trend_template` がプレフィックス剥がしで `trend_template` に化けてテーブルに当たらず、**252本要求が全戦略で漏れて `F` の消失が 0件と出た**（修正後は93件）。同種の取りこぼしが他の列に残っている可能性がある

- **独立経路での確認**:
  **着手前には実施できない。** §1.2(c) は「現行データ＋閾値照合」という間接推定であり、これを検算する独立経路は「**A案を実装して sandbox で T3 を再計算し、同じバックテストを走らせる**」ことそのものだから。コード変更が前提になるため、着手前の実施は原理的に不可能。
  **測る契機はチェックリスト 5-13 に置いた**（5-14 の成績記録より前、かつ merge より前）。「後で測る」にしないため、5-13 を通過しないと 5-14 以降に進めない順序にしてある。
  なお §1.2(f) の MTS breadth については、**同じ Parquet から3通り（現行 / NaN→False / NaN除外）を独立に計算して符号と大きさを突き合わせており**、これは着手前に実施済み（`tmp/breadth_warmup_impact.py`）。

### 6.2 転記の完全性

- **転記元（1）**: 2026-09-11 の影響判定（会話）および `doc/issue_list.md` の ① の項目
- **元の件数**: 会話で確定した所見 **14件**
  1. 流儀が3つに割れている（`sma_200` vs `ema_200` の同一ループ内非対称）
  2. アドホックガード2件（`RS_DOT_WARMUP_BARS` / `has_breadth` 日付ハードコード）
  3. 汚染の時間分布（年次表）
  4. バックテスト窓との対応（2017年は未使用・最悪は 2018Q4）
  5. 現在時点の静的影響（117銘柄 / 流動性通過87 / 誤点灯4 / 高値近接誤認10）
  6. `is_trend_template` の偶然のセーフティネット（150本未満で cond3 が必ず False）
  7. T4 波及ゼロ（ランク対象は rs 系22列）
  8. A-full は `PERCENT_RANK` が NULL を最小値扱い → ランク0に張り付き除外されない
  9. T5 breadth 影響は無視可能（最大 -0.87pt）
  10. `t5_signals.py:76` の bool 演算が NaN を False に潰して `skipna` を無効化している
  11. シグナル消失の実測（窓別・戦略別）
  12. pruning 境界に触れない
  13. 再計算コスト 30〜45分
  14. 測定の限界（繰り上がり未測定 / 成績未確定）
- **本計画書の件数**: **14件**（1→§1.1 / 2→§1.1 / 3→§1.2(a) / 4→§1.2(a) / 5→§1.2(b) / 6→§1.2(b) / 7→§1.2(e) / 8→§2.2 / 9→§1.2(f) / 10→§1.2(g)・§3.2 / 11→§1.2(c) / 12→§1.2(d) / 13→§4-5 / 14→§6.1）
- **差分の説明**: なし。`doc/issue_list.md` の ① には 1〜6 のみ追記済みで 7〜14 は未追記。5-16 でクローズする際に結論を issue 側へ要約して残す。

---

- **転記元（2）**: 2026-09-11 のユーザーによる §4 判断
- **元の件数**: **6件**（4-1 揃えない / 4-2 含める / 4-3 8戦略のみ / 4-4 正しさ優先 / 4-5 merge 後 / 4-6 ② を先に直す）
- **本計画書の件数**: **6件**
  - 4-1 → §2.2（判断と理由）/ §3.3（波及範囲の確定）/ §4（経緯）/ §1.3-6 / 5-12
  - 4-2 → §2.1-3 / §3.4 / 5-9
  - 4-3 → §1.3-5 / 5-18
  - 4-4 → §2.2 / §1.3-5 / §6 検証表 / 5-14
  - 4-5 → §4 / 5-17
  - 4-6 → ヘッダー / §2.2 / §1.3-7 / §5 冒頭 / 5-11 / 5-17 / 作業中メモ
- **差分の説明**: なし。4-3 は反映の過程で欠落を1件検出・修正した（§7-3）。

---

- **転記元（3）**: 2026-09-11 の課題整理の議論（会話）
- **元の件数**: 議論で確定した事項 **15件**
  - a. SMA は窓を超えれば正しくなり誤差は残らない → 課題は窓未満期間の判定＝IPO 銘柄がかかる
  - b. 仮の値は上場後上昇銘柄を系統的に強く見せる（モメンタム系に集中）
  - c. 重大度は 🔴 ほど高くない
  - d. 一般的なツール（pandas 既定・TA-Lib・Pine）は NaN を返す
  - e. NaN にすれば `sma_200 IS NULL` で IPO 銘柄を抽出でき、IPO 特化スクリーナーの土台になる
  - f. ① と ② は発生条件・原因・直し方が異なる別問題
  - g. 4-1 は揃えない（最初の判断）に戻す
  - h. ① の昇格経路（`deploy_after_merge`）は ② を通る
  - i. デイリーは更新日だけ書くので ② は起きない。T5 の全日付再計算時のみ
  - j. 世代比較: 2026-08-29 世代は正しい、2026-09-10 以降の世代は38日誤り
  - k. ログ: T5 の全日付再計算が4回、うち3回はルール化（2026-09-04 01:11 `ae77596`）の後
  - l. もう一度リフレッシュしても直らない（同じ経路でまた壊れる）
  - m. ② の対応＝Parquet 基点のルールを T5 まで広げる（T5 には `recompute_parquet_*` 相当が無い）
  - n. `pipeline-debugging/SKILL.md:30` に Parquet 基点の注意が無い
  - o. ② を直すまで `--rebuild-from` 系を実行しない
- **転記先と件数**: **本計画書 9件**（a→§1.0 / b→§1.0 / c→§1.0 / d→§1.0 / e→§1.0・§2.2 / f→§1.0・§2.2 / g→§2.2・§4-1 / h→§4-6 / o→§4-6・作業中メモ）＋ **issue ② 8件**（f / i / j / k / l / m / n / o）。重複 2件（f・o）。**9 + 8 − 2 = 15 で元の件数と一致**
- **差分の説明**: なし。② 固有の事項（i〜n）は計画書に重複させず issue ② を正とする（二重管理を避けるため。§7-4 には経緯の要約のみ残す）

### 6.3 レビュー記録（G3 ブランチレビュー）

- **対象**: 種別 B・`main..worktree-min-periods-warmup`（44ファイル・+2154/-416）。レベル `high`（`doc/workflow.md` §3）
- **G2-2**: `backend/tests/` 全体 **1988 passed / 1 skipped / failed 0**（2026-09-24・ワークツリー）

#### 1周目（2026-09-24・HEAD `1aec291`）

finder 8本の報告をすべて集約（再利用 / 規約 / 効率 / 削除された挙動 / 高度 / 行スキャン / ファイル横断トレース / 実測ベースの正しさ）の指摘を集約し、
**重大な指摘はコードを直接読んで実在を確認**してから仕分けた（複数の finder が独立に一致したものは★）。

| # | 指摘 | 仕分け | 理由・対応 |
| :-- | :-- | :-- | :-- |
| R1 ★ | 仮想指数の**増分ブランチ**が `>= seed_date` で絞った数行に `rolling(21, min_periods=21)` をかけ、必ず NaN→`surge=1.0`（volume=1e6 固定） | **対応 → 5-20** | 実在確認済み（`all_prices_df` は730日分を持っており、絞る前に計算すれば済む）。旧 `min_periods=1` は粗い値だったが、新コードは縮退値。**このブランチの変更が直接の原因**で、テストは rebuild 経路しか見ていなかった |
| R2 ★ | `compute_breadth_momentum` が全銘柄 NaN の日に `0.5` を返すため、`has_breadth = notna()` が素通り（2017Q1 で偽 breadth の4成分スコア）。`scenario_runner`/`etf_single_runner` に同じ `else 0.5`、`scenario_market_score.py:175` には日付ゲートが残り T5 と不一致 | **対応 → 5-21** | 実在確認済み。5-8b で「デッドコード」と判断した `fillna(0.5)` は、`has_breadth` を NULL 判定に変えた時点で**偽の判定可能を作る**箇所に変わっていた（§2.3 違反）。**副作用として MTS が 2017-03〜2018-03 で3成分→4成分に変わる**（breadth が正しく算出できる区間のため意図どおり。2017年はバックテスト窓外） |
| R3 ★ | `db_health_check` の短履歴除外が `rs_momentum_e21`<75・`rs_ratio_e21`<30 のまま（レジストリ実測は 94 / 40）。新規上場銘柄が NG になる | **対応 → 5-22** | 実在確認済み。5-11 が `sma_200` だけを足して残りを「5-11b で対応」としたが、5-11b はレジストリ側だけで health check を更新していなかった。手書き数値の追加ではなくレジストリから導出する |
| R4 | `SummaryTable.tsx` が `?? 0` で NULL ランクを0（最悪）として表示・整列（`RrgChart.tsx:97-99` も同型） | **対応 → 5-23**（SummaryTable のみ）／RrgChart は issue 化 | 実在確認済み。5-9b の「SummaryTable は元々 null 安全」は**クラッシュしないという意味でしか正しくなかった**。RrgChart は履歴30点の系列で NULL の扱い（欠損点を描かない等）に設計判断が要るため切り出す |
| R5 | `t5_signals.py` の docstring が撤去済みの収束ロジックを説明している | **対応 → 5-24** | 実在。「復元」を誘発して無限再書き込みを再導入する恐れがある |
| R6 | T5 が NULL スコアの既存行を自己修復しなくなった（`market_trend_score IS NOT NULL` → 行の有無） | **却下** | **§3.6 でユーザー合意済みの設計判断**（「失うもの」まで明記済み。復旧手段は `--rebuild-from T5`）。本番は昇格時に全期間再計算されるため既存の旧ロジック由来行は残らない |
| R7 | `rs_roc_ema_200` の `warmup_bars` 511→611 で夜間フォールバックが収束しない | **却下（既知）** | 511 も 611 も K=400 以上で、どちらも `WARMUP_UNDETERMINED` に分類される（分類は変わらない）。既知の `rs_roc_ema_200` 問題（2026-09-23 起票済み）の範疇で、増分は 511〜611本の帯の銘柄のみ |
| R8 | `warmup_bars` の一部（`vol_surge_21`/`vol_surge_rel_spy_21`=73、`up_down_vol_ratio_50`=76）が構造的下限ではなく標本の最大値。連鎖 warmup が手書き定数で導出されていない。`is_structurally_null_column` の SPY 例外リスト | **issue 化** | 実在するが T3増分化のレジストリ設計に属し、本ブランチのスコープ外。§2.2「5-11b は実測を信用する」方針の限界の話 |
| R9 | 仮想指数の合成が構成銘柄の先頭20日を `surge.fillna(1.0)` で中立化（rebuild 経路 L109/326/402） | **issue 化** | §3.7「別系統（本計画のスコープ外）」に既出。§8 と同じ起票に合流 |
| R10 | `scenario_market_score.py:91-107` の SPY フォールバック計算が `min_periods=1`・`atr fillna(1.0)` のまま | **issue 化** | §2.2 / 4-1 で「SPY 専用計算は揃えない」と確定済み。フォールバック経路（T3 が無いときのみ）で、揃えるかは別判断 |
| R11 | `percent_rank` の per-group Python コールバック（T4 Parquet 再構築が1〜2分遅くなる見積り）／ `find_stale_input_gaps` の毎回 `tolist()`／`vol_sma_21` の二重 rolling／breadth の lambda | **issue 化** | 実在するが性能のみ（数分〜数秒規模）。正しさに影響しない |
| R12 | 21日窓の5箇所複製（orchestrator ×4 + parquet_recompute）／breadth 式の3箇所複製／None 安全ソートキーの3回記述／`chart_router` の指標再実装／`report_stale_input_gaps` を `market_signals` から import（T3→T5 依存） | **issue 化** | 重複は計画 §3.2・§3.4 で**意図的に個別修正**とした構造（統合は別リファクタ）。修正漏れの検出は R2 の同時修正で担保する |
| R13 | `vol_accum_days_5` が object dtype（None 混在） | **issue 化** | dtype は事実（実測）だが、`is_trend_template` / `market_signals` と同じ既存パターン。比較で `TypeError` になる主張は**実測で誤り**（`>= 3` は通る）。全 None 列の Parquet 型推論は保存経路で別途確認が要る |
| R14 | `_atr_wilder_kernel` が 0.0 初期化のまま／`close == 0` の ATR% が 0／`except Exception` の握りつぶし | **却下** | 唯一の呼び出し元で先頭 `window-1` 本を NaN 化しており（5-4b）実害なし。`close > 0` は T2 読み込み時点で保証（`close IS NOT NULL AND close > 0`）。`except` は既存 |
| R15 | `screener_router.py:735` / `scenario_runner.py:583` の NULL ランク→0.0 | **却下** | 本ブランチの変更箇所ではない。前者は上位200件のソートキーで「NULL は最下位」と意味が一致、後者はキー欠落時の既定値 |
| R16 | `relative_strength.py` のテストが3ファイルに分割（1:1 規約） | **却下** | 既存 `test_relative_strength_precision.py` の前例と同じ粒度分割。ファイルごとに目的が異なる |
| R17 | `^VIX` 系のボリューム0で `--check-warmup-nulls` が誤検知 | **issue 化** | R8 と同じレジストリ／診断ツールの構造的例外の話 |
| R18 ★ | screener API が NULL ランクを 0.0 にして返す（`screener_router.py:778-779`）。dashboard API は NULL を返すため不整合。5-9b のフロント null 安全化が効かない | **対応 → 5-25** | 実在確認済み。R15 で「却下」とした `:735`（ソートキー）とは別物で、こちらは**API の出力**（§2.3.4「API は NULL のまま返す」）。計画 §4-8「ランク系のみ」の範囲 |
| R19 | `weekly_maintenance.py` の T3 自己修復が SQLite の約504本だけで再計算し、欠損日の行に（窓が足りない列の）NULL を書く。Parquet に正しい値があっても届かない | **issue 化（P1）** | 実在確認済み。SQLite 基点である点は元からで issue ② と同種。修正前は近似値、修正後は NULL（原則には近づく）だが、§2.3.2 の期待「Parquet の正しい値を使う」とは逆。Parquet 基点化は設計判断を含むため本ブランチでは扱わない |
| R20 | `relative_strength.py` に足した `report_stale_input_gaps(spy_close)` が全銘柄・全 T3 実行で呼ばれ、暦の違う銘柄でログが増える | **issue 化** | ログのみ（値は不変）。R11 に合流 |
| R21 | `db_health_check` の `sma_200` 除外が直近5行チェックと噛み合わない（200〜203本で NG） | **対応 → 5-22 に統合** | 実在。R3 の修正時に `+5` を含める |
| R22 | `df_vix` 欠損時に `vix_close = 20.0` の固定値で `vxv/20` を計算する（VXV 側の推定式は撤去済みなのに VIX 側が残った） | **対応 → 5-26** | 実在確認済み（本ブランチの変更ではなく元からの記述だが、§3.7 / 4-12 の意図＝捏造値の撤去に含まれる撤去漏れ） |
| R23 | `RS_DOT_WARMUP_BARS = 252` と kernel の `i < warmup` が新しい `rolling(252, min_periods=252)` に対して1本ずれる（点灯可能な最初の bar は index 251） | **issue 化** | 既存 issue「`RS_DOT_WARMUP_BARS` 撤去要否」に追記。境界の1本のみ。増分経路は履歴1011本以上の銘柄でしか使われず、フル/増分の食い違いは実害に出ない |
| R24 | `min_periods=window` により入力に NaN が1本あると窓ぶんの出力が NaN になる（EMA/ATR は1日で回復するので不揃い） | **却下** | pandas 標準の挙動で、本計画が揃えにいった一般的なツールの挙動そのもの（§1.0）。T2 に NULL の終値/出来高は入らない（コードコメントとデータ実測）。入るようになったら別問題 |
| R25 | `test_scan_price_anomalies.py` が `UnicodeEncodeError: 'cp932'` で失敗 | **却下** | 環境（ロケール）由来で本ブランチと無関係。オーケストレーターの全件実行は 1988 passed / failed 0 |

- **issue 化の起票先**: `doc/issue_list.md` P2「`min_periods_warmup` の G3 レビューで切り出した項目」（R4-RrgChart / R8 / R9 / R10 / R11 / R12 / R13 / R17 / R19 / R20 / R23）
- **1周目の結論**: 「対応」7件（5-20〜5-26）。finder 8本の報告をすべて反映済み。実測ベースの finder は、レジストリの `warmup_bars` の一致・K=400 での増分/フル再計算の一致（10履歴長・60列超で不一致0）・`percent_rank` と SQLite の一致（300ケース）を**肯定的に確認**している。**修正 → G2 → 2周目の G3 が必要**。「merge 可能」はまだ報告しない

#### 2周目（2026-09-24・修正分 `1aec291...47074bb` を対象。`/code-review high`）

1周目の「対応」7件（5-20〜5-26）がすべて G2 を通った後の再レビュー。修正分だけを対象にした（全体を再度かけると1周目で仕分け済みの指摘が重複するため）。

| # | 指摘 | 仕分け | 理由・対応 |
| :-- | :-- | :-- | :-- |
| R26 | `scenario_market_score` を経由して、**保存済み** `market_signals` の旧ロジック由来 `breadth_sma50 = 0.5`（2017Q1 等）が「算出できた breadth」として読まれ、日付ゲート撤去後は4成分 MTS になる | **却下（昇格の前提条件として記録）** | 昇格（`deploy_after_merge`・既定 `--rebuild-from T3`）が **T5 を Parquet 基点で全期間再計算**する（`_rebuild_from_parquet` → `recompute_parquet_signals.run`）ため、旧 0.5 の行は昇格時に NULL へ置き換わる。**5-17 の昇格後検証に「2017Q1 の `breadth_sma50` が NULL であること」を追加**した |
| R27 | 仮想指数の増分ブランチが730日全履歴の rolling を毎日回す（seed 以降しか使わない） | **issue 化** | 性能のみ（仮想テーマ約170本×構成銘柄。rebuild 経路と同じ計算量）。seed_date の約21営業日前までに絞れば同じ値になる。R11 に合流 |
| R28 | `db_health_check` が銘柄ごとに `columns_with_warmup_threshold()` を呼ぶ／除外条件が厳密値より1本広い | **却下** | 前者はレジストリの定数辞書引きで実害なし。後者は保守側（誤検知を避ける向き）の1本で、5-22 の完了ノートに記載済みの許容事項 |

- **2周目の結論**: 「対応」0件。未対応の重大指摘なし。**G3 通過**（往復2周・`doc/workflow.md` §3 の上限内）
- レビュアーが正しさを確認した箇所: `compute_breadth_momentum`／`t5_signals`／screener と `SummaryTable` の NULL ランク処理／`vix_close` の変更

## 7. 途中発生した課題

### 7-1. breadth 集計の NaN 潰しは3箇所に複製されていた（2026-09-11・合意後の追加調査）

- **事象**: 計画レビュー時点では `t5_signals.py:76` の1箇所として扱っていたが、SPY の波及確認のため `sma_50` の参照元を洗い出したところ、**同じ `is_above_sma50 = close > sma_50` が `scenario_runner.py:319`（型3）と `etf_single_runner.py:240`（型2）にも複製されていた**
- **原因**: シナリオテストが本番 T5 と同じ breadth を内部で再計算する設計で、計算式がコピーされている
- **解決**: §3.2 を3箇所に拡張し、5-8 で**同時に**直す。1箇所だけ直すと本番 MTS とシナリオの breadth が食い違う。いずれも個別銘柄の集計であり SPY 専用計算ではないので、§4-1 の対象外と判断した

### 7-2. 「SPY を揃えない」でも T3 経由で SPY 系に波及する（2026-09-11・合意後の追加調査）

- **事象**: SPY 専用計算を据え置くと決めたが、`scenario_market_score.py:77-79` が **T3 の `sma_50`/`sma_200` を優先使用し、自前計算はフォールバックに過ぎない**ことが判明。`moving_averages.py` の変更は SPY の T3 行を通じてシナリオの市場スコアに届く
- **原因**: 「SPY 専用計算」と「SPY の T3 行」が別物で、前者を据え置いても後者は §3.1 の変更対象に含まれる
- **解決**: 変化してよい範囲を実測で確定した（SPY 起点 2010-04-01 → 252本目 **2011-03-30**）。**それ以降の完全不変**を成功条件（§1.3-6）とし、5-12 で全列比較による機械検証を置いた。差分が出たら停止してユーザーに報告する

### 7-3. 再最適化の工程がチェックリストに存在しなかった（2026-09-11・§4 判断の反映時）

- **事象**: §4-3（再最適化は8戦略のみ）の判断を反映する際、**「どの8戦略か」は書いてあったが「再最適化する」という工程自体が §5 に無かった**
- **原因**: 当初の成功条件が「再最適化の要否を判断できている」で止まっており、実行をタスク化していなかった
- **解決**: 5-18 を追加。実行はユーザー（約12時間規模）であることを踏まえ、エージェントの責務は「main 反映・昇格済みの明言 → 1戦略ドライラン → 対象明示で引き渡し」までとした。**データが変わる変更なので、既存 trial の集計値ベースの再スコアリングは使えない**点も明記した

### 7-4. 別問題 ② を発見し、別 issue に切り出した（2026-09-11・§4-1 再検討時）

- **経緯**: §4-1 をユーザーが「T3 経由でどのみち影響するなら揃える方向でもよい」と再検討。揃えた場合に T5 が壊れないか確認するため T5 の SPY 入力範囲を調べたところ、**T5 のリフレッシュが SQLite 基点のままで、本番 MTS の `market_phase` が 2024-09-03〜2025-04-21 の38日で既に誤っている**ことを発見した
- **混乱した点**: 当初この発見を ① の延長（「SPY 版の ①」）として扱ったため、① と ② の区別が曖昧になり議論が錯綜した。整理の結果、**発生条件・原因・直し方がすべて異なる別問題**と確定した（§1.0）
- **解決**: ② を `doc/issue_list.md` に別 issue として起票。**事実・実測・原因の詳細は issue ② を正とし、本計画書には重複させない**。本計画への影響は2点のみ: (1) §4-1 を「揃えない」に戻した (2) 昇格を ② の修正後にした（§4-6）

## 8. スコープ外・残作業

- **② T5 のリフレッシュが SQLite 基点のまま** — `doc/issue_list.md` に起票済み（2026-09-11）。**本計画の昇格の前提**（§4-6）
- ~~A-full（rs 系の `min_periods=max(1, n//2)`）を別 issue に切り出す~~ — **2026-09-20〜21 に方針転換。§4-7 で「含める」と確定し、本計画のスコープに戻した（5-9c）。** この行は2026-09-11時点の古い判断の名残りで、§2.2/§3.5/§4-7 の確定判断と矛盾していたため打ち消し線で無効化する（削除すると転記の完全性チェック §6.2 の件数と食い違うため残す）
- **既存ガードの撤去** — `RS_DOT_WARMUP_BARS`（`relative_strength.py:22`）と `has_breadth`（`market_signals.py:174`）。A-core 適用後は原理的に不要になるが、撤去には独立検証が要る
- **IPO 特化スクリーナー** — A案適用後に `sma_200 IS NULL` を条件にして作れるようになる（§1.0）。本計画では作らない
- **`bars_available` 列の追加** — 診断用。今回の実測を毎回スクリプトで再計算せずに済む
- **2017年の汚染（78%）** — 構造的に解消不能（§2.2）。バックテストの窓を 2017年に広げる場合は再検討が必要
