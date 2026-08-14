# スクリーンパラメータ一元化（フィルタ仕様レジストリ → パリティ検証 → 単一エンジン）計画書

- **ステータス**: 🚧 進行中（**2026-08-10 に計画レビュー完了・合意。Phase 0 着手**）
- **実施者**: Claude Code（`claude-opus-5`、オーケストレーター）／実装は implementer・test-writer へ委譲
- **開始日**: 2026-07-29（**2026-08-10 にソース全体を再調査して全面改訂**） / **完了日**: —
- **作業ブランチ**: Phase 0 は本体チェックアウト（読み取り専用の計測のみ）。Phase 1 以降はワークツリーを切る
- **対象 issue / 関連ドキュメント**:
  - `doc/issue_list.md` P3「スクリーナーの完全 DataFrame 化（D-2 の最終形）」（本計画の Phase 3 に相当）
  - `doc/issue_list.md` 完了済み「`category='テーマ'` が買い候補として混入していた」（2026-07-29〜08-05。**本計画の直接の動機**）
  - `doc/issue_list.md` 完了済み「流動性フィルタ `min_avg_dollar_volume_21` の2段階の取りこぼし」
  - `doc/completed/screener_refactoring_d1_d2.md`（D-2 = 特殊フィルタ共通化。本計画はその続き）
  - `doc/backend_specification.md` §5・§6.1・§6.7

> [!NOTE]
> **2026-08-10 改訂の要点**: 2026-07-29〜08-09 の間にソースが大きく更新されたため、
> 全参照箇所を再取得し直した。とくに以下2点が計画の前提を強化・変更している。
> 1. **テーマ混入バグが「片側だけ修正 → 1週間後に本番でユーザーが発見」という経過をたどった**（§1.2 F2）。
>    本計画が狙う障害型の、これ以上ない実例が新たに1件増えた
> 2. **プロジェクトが既に fail-loud を部分採用した**（`report_strategy_scan_coverage` 等、2026-08-04）。
>    本計画は新しい規律の導入ではなく、**既に採用済みの規律をフィルタ層へ一般化する**位置づけになった（§1.3）

---

## 1. 背景と目的

### 1.1 現状（2026-08-10 時点の実測）

スクリーン条件の評価経路は3つあるが、実態は **「2エンジン × 3データ供給」**である。
（ETFシナリオテスト＝型2 は SMA200 ベースのポジション制御戦略でスクリーンフィルタを使わないため対象外。
`etf_single_runner.py` が `screener_filters` を import していないことを確認済み。）

| 経路 | フィルタエンジン | データ供給 |
| :--- | :--- | :--- |
| ① フロントのスクリーナー | **SQLAlchemy**（`api/screener_router.py::_apply_filter`）＋ 特殊フィルタのみ pandas | SQLite 1日分 |
| ② パラメータ最適化バックテスト（型1） | **pandas**（`backtest/backtest_screener.py::apply_filters_to_df`） | Parquet キャッシュ |
| ③ 個別銘柄シナリオテスト（型3） | **pandas（②と同一関数）** | Parquet キャッシュ（前処理は `scenario_runner.py` 独自） |

D-2（2026-07-04）で特殊ブールフィルタ14種は `indicators/screener_filters.py` に一本化済み。
**残っている二重実装は数値 `min_/max_` 系・テーマ系・`is_close_gt_*`・`expression`・カラム供給・
共通制約の注入**であり、フィルタキーの解釈ロジックは以下 **4箇所**に独立して存在する。

| # | 場所 | 役割 | 行数 |
| :--- | :--- | :--- | ---: |
| 1 | `api/screener_router.py::_apply_filter`（L204-351） | 適用（SQL） | 148 |
| 2 | `api/screener_router.py::_build_preset_query` の `is_known` 判定（L639-676） | 検証 | 38 |
| 3 | `backtest/backtest_screener.py::apply_filters_to_df`（L139-591） | 適用（pandas） | 453 |
| 4 | `backtest/backtest_runner.py::validate_strategies_config`（L540-675） | 検証 | 136 |

「フィルタが必要とするカラム」の宣言も **4箇所**に手書きで分散している。

| # | 場所 | 形態 |
| :--- | :--- | :--- |
| 1 | `api/screener_cross_section.py` の `_IND_COLS` / `_PREV_COLS` / `_RANK_COL_MAP`（L38-61） | 定数リスト |
| 2 | `backtest_screener.py` の `needs_rs14/21/63`（L182-224）＋ deny-by-default の重複 or 連鎖（L299-321）＋ `prev_merge_cols`（L341） | or 連鎖 |
| 3 | `scenario_runner.py` のランク事前マージ（L508-531） | ループのコピー |
| 4 | `backtest_runner.py::preload_data` のキャッシュ列リスト（`backend_specification.md` §6.7） | 定数リスト |

全戦略共通のハード制約（`min_avg_dollar_volume_21`）の注入も **4モジュール5箇所**（`screener_router.py:76`、
`backtest_runner.py:504`、`scenario_runner.py:86`、`optimization_runner.py:363,443`）に独立実装されている。

**テーマ行の最終出力除外**（2026-07-29／08-05 の修正。§1.2 参照）も、同一のコメント込みブロックが
**2ファイル3箇所**（`backtest_screener.py:575-579`、`screener_router.py:690-694`、`screener_router.py:829-833`）に
コピー＆ペーストされている。次に同種の「全経路共通の出力ルール」が増えれば、また同じ数だけ手書きになる。

加えて `backtest_runner.py:550` が `api.screener_router` から `_INDICATOR_COLUMNS` を import しており、
**バックテスト → API という逆向きの依存**が発生している（さらに `except ImportError` で握り潰されるため、
import が壊れると検証が静かに劣化する）。

### 1.2 過去の障害の分類（issue_list からの実証）

> 参照は `doc/issue_list.md` の**項目タイトルと日付**で行う（同ファイルは 294→686 行に再構成されており、
> 行番号では追跡できないため）。

| 型 | 内容 | 実例 |
| :--- | :--- | :--- |
| **F1: サイレント素通し**（列が無い／キー未知 → 何もせず全通過） | **最多** | 「流動性フィルタの2段階の取りこぼし」の2段階目（`avg_dollar_volume_21` の `_x/_y` サフィックス衝突）、`is_rs_blue_dot` の alias 不一致（2026-07-22）、`min_rs_ratio_rank_e14/e63` が `needs_rs*` 未登録（2026-07-05）、expression の true/false 非対応（D-2 I-6）、`group != "Check"` の preset 除外（2026-07-20） |
| **F2: 片側のみ実装** | **本課題の出発点。最新の実例あり（下記）** | **`category='テーマ'` の買い候補混入（2026-07-29〜08-05）**、流動性床 `min_avg_dollar_volume_21` の1段階目、出口ルールの二重実装（2026-07-20） |
| **F3: 意味論の差** | 少数 | S-1（`min_market_cap` のテーマ扱い）、S-2（`is_rs_ratio_rank_e21_gt_e63` のテーマ扱い） |
| **F4: データ供給の差**（同じ関数でも渡す DataFrame が違う） | F1 と結合して顕在化 | `has_all_premerged` 誤判定でシナリオテストが常時0件（`backtest_screener.py` L290-322） |

#### 決定的な実例: テーマ混入バグ（2026-07-29 → 2026-08-05）

`apply_filters_to_df` のベースフィルタが `category` を `['テーマ','個別']` に絞っており、
**テーマ行（実在ETF・仮想合成指数）がそのまま買い候補として最終出力に残っていた**。
戦略 D では全トレードの 21.7% がテーマ側だった。経過は以下の通り。

| 日付 | 出来事 |
| :--- | :--- |
| 2026-07-29 | バックテスト側（`apply_filters_to_df` 末尾）でテーマ行を除外。**生スクリーナー API 側は当初スコープ外と判断** |
| 2026-08-01 | 全12戦略を再実行・再最適化し、バックテスト側のテーマ混入0件を確認 |
| **2026-08-05** | **本番のフロントエンドで、B6「RS MACD and Theme」プリセットの結果に仮想合成指数 `_GRCL0C_` が混入しているのをユーザーが発見**。同型バグと判明し、`get_screener()` と `_build_preset_query()` の両方に `query.filter(Symbol.category != 'テーマ')` を追加 |

**この1件が本計画の必要性を最もよく示している**:

- 「片側だけ直す」判断が**意図的に**行われた（スコープ外と明示）。人間の規律だけでは防げない
- 検知したのは自動テストでも監査でもなく、**本番画面を見たユーザー**だった
- 発見まで **1週間**、その間の画面表示は誤っていた
- **経路間パリティテスト（Phase 2）があれば、07-29 のバックテスト修正をコミットした時点で即座にレッドになっていた**

#### 対策設計への帰結

主因は「共通化されていないこと」ではなく **「宣言されていない／供給されていないものを黙って無視すること」**（F1）である。
したがって共通化とパリティテストだけでは不足で、`fail-loud` 化が対策の中核になる。

- パリティテスト単体では「両側とも未実装」（F2 の初期状態）は**両方 0 件で一致して素通り**する。
  実際 `min_avg_dollar_volume_21` は「最適化バックテストにしか無い」状態で ①③ 間のパリティは成立していた。
- `fail-loud` 単体では「両側に実装があるが意味が違う」（F3）と「片側だけ直した」（F2 の後期）は検知できない。
  テーマ混入バグは fail-loud では捕まらず、**パリティテストでしか捕まらない**。
- よって **レジストリ + fail-loud + パリティ** の3層が必要で、どれも他で代替できない。

### 1.3 プロジェクトは既に fail-loud を部分採用している（2026-08-04）

本計画は新しい規律の持ち込みではなく、**既に採用され効果が確認されている規律をフィルタ層へ一般化する**ものである。
2026-08-04 の2件の修正が先行事例であり、Phase 1 の設計はこれに倣う。

| 先行事例 | 実装 | fail-loud の形 |
| :--- | :--- | :--- |
| シナリオテストの戦略ディスパッチ | `scenario_runner.report_strategy_scan_coverage()`（L118〜）。接頭辞を `SCENARIO_TARGET_PREFIX`（L99）へ一元化 | スキャン対象外の戦略を**理由つきで報告**し、**対象が1件も無ければ `ValueError` で停止** |
| シナリオバッチのポートフォリオ設定 | `run_scenario_batch.resolve_portfolio_params()` | **未知のキーは `ValueError` で停止**。上書き項目は実行時に表示 |

いずれもコメントに「黙って無視されると『設定したのに効かない』まま比較検証を進めてしまう（D-2/I-6 と同型の
サイレント失敗を作らない）」と明記されており、**本計画の問題意識と完全に一致している**。

> 残る小さな重複: `scenario_scorer.py:9` の `target_group_prefix` 既定値は文字列リテラル `'Rise - Check'` のまま。
> 呼び出し側（`scenario_runner.py:359`）が定数を明示的に渡し、一致することをテストで固定しているため実害は無い。

### 1.4 完了時の成功条件

1. フィルタキーの**唯一の定義場所**が `indicators/screener_filters.py` の `FILTER_SPECS` になり、
   ①②③ と TOML バリデータ・`/screener/meta` がすべてそれを参照する。
2. 未知のキー・必要カラムの欠落が**必ず失敗として顕在化**する（黙って通過しない）。
3. 全フィルタキーについて ①（SQLite 経路）と ②（Parquet 経路）の抽出銘柄集合が一致することが
   **レジストリ駆動のテストで自動的に検証**される（新キーはフィクスチャを書かないとテストが落ちる）。
4. 「全経路共通の出力ルール」（テーマ除外・流動性床）の実装が**各1箇所**になる。
5. スクリーン結果に「実際に適用されたフィルタ一覧」が残る。

### 1.5 ベースライン（着手前の実測値）

**Phase 0 で実測済み（2026-08-10）。以降の Phase はこの値を基準に判定する。**

| 指標 | 値 | 計測方法 |
| :--- | ---: | :--- |
| キー解釈ロジックの実装箇所 | 4 | §1.1 の表 |
| 必要カラム宣言の実装箇所 | 4 | §1.1 の表 |
| 共通ハード制約（流動性床）の注入箇所 | 5（4モジュール） | `grep min_avg_dollar_volume_21` |
| 共通出力ルール（テーマ除外）の実装箇所 | 3（2モジュール） | `grep "category != 'テーマ'"` 等 |
| **pytest 全件** | **795 passed / 115.98 秒（All Green）** | `pytest backend/tests/ -q` |
| **`GET /api/screener`（ウォーム）** | **0.25〜0.34 秒**（200件返却。特殊フィルタ有りでも 0.31〜0.34 秒） | 関数直呼び × 各3回 |
| **`GET /api/screener/dashboard`（ウォーム）** | **0.55 秒（最新日） / 0.95〜1.06 秒（過去日）** | 同上 × 各2回 |
| `GET /api/screener/dashboard`（コールド初回） | 20〜27 秒 | 下記の注記を参照 |
| `stocktool.db` サイズ | 1,619 MB（WAL 0 MB） | 2026-08-07 の肥大化修正後の正常値 |

> [!IMPORTANT]
> **性能計測はウォーム値で行うこと。** 過去日の dashboard は初回アクセスで 20〜27 秒かかるが、
> これは OS のページキャッシュがコールドなためで、同じ日付を2回目に呼ぶと 0.95〜1.06 秒に収まる
> （2026-08-10 に同一日付の連続呼び出しで切り分け済み）。**日付固有の遅さではない。**
> U-4 の受入基準（×1.5 以内）は**ウォーム値に対して**適用する。

主要モジュールの規模（2026-08-10）: `screener_router.py` 938行 / `backtest_screener.py` 591行 /
`backtest_runner.py` 750行 / `scenario_runner.py` 727行 / `optimization_runner.py` 751行 /
`screener_filters.py` 474行 / `screener_cross_section.py` 191行。

主要モジュールの規模（2026-08-10）: `screener_router.py` 938行 / `backtest_screener.py` 591行 /
`backtest_runner.py` 750行 / `scenario_runner.py` 727行 / `optimization_runner.py` 751行 /
`screener_filters.py` 474行 / `screener_cross_section.py` 191行。

---

## 2. スコープと設計判断

### 2.1 変更すること

- フィルタキーの宣言的レジストリ `FILTER_SPECS` の新設と、①②③・バリデータ・`/screener/meta` の参照統一
- 未知キー・必要カラム欠落の fail-loud 化（§1.3 の先行事例に倣う）
- 全経路共通ルール（流動性床・テーマ除外）の単一実装化
- 実行時の `applied_filters`（実際に適用されたキー一覧）の記録・出力
- ①（SQLite）と ②（Parquet）の経路間パリティテスト（レジストリ駆動）
- Phase 3: 正準クロスセクション契約 `ScreenerFrame` の定義と、SQL 側フィルタエンジンの撤去

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| T3 で全ブールフラグを事前計算し、両経路は列を読むだけにする | **却下** | パラメータ付きフィルタ（RRG の `intensity_threshold`、VCP の `pivot_tol`／`base_vol_dry_max` 等）が事前計算できず、Optuna で閾値を振れなくなる。最適化の根幹と両立しない。加えてフィルタ追加のたびにスキーマ変更＋サンドボックス昇格が必要になる |
| `strategy_normalizer.py`（旧キー → 正準キー変換層） | **維持** | 既に単一実装で機能しており、レジストリの前段として正しい位置にある。`FILTER_SPECS` は正準キーのみを持つ |
| SQLite と Parquet のデータ層そのものの統合 | **やらない** | ホット/コールドの分離は `architecture.md` §11 の中核設計。統合するのは「フィルタ評価」であって「保存形式」ではない |
| S-1 / S-2 の意味論 | **pandas 側を正とする既存判断を維持** | D-2（`doc/completed/screener_refactoring_d1_d2.md` §1.2）で決着済み。蒸し返さない |
| **テーマ行を最終出力から除外する**（中間データとしては保持） | **現行の挙動を正として固定** | 2026-08-05 に両経路へ実装済み。実売買不可能なため候補にしない。リーディングテーマ判定・構成銘柄への波及には `category=='テーマ'` 行が必要なのでフィルタ処理中は保持する。**この2段構えをパリティテストの期待値として明示的に固定する** |
| ②と③ のエンジン統合 | **不要（既に同一関数を共有）** | 分散していたのは前処理とハード制約の注入。そちらを Phase 1 のレジストリで解消する |
| ETFシナリオテスト（型2、`etf_single_runner.py`） | **対象外** | スクリーンフィルタを使わない（`screener_filters` を import していないことを確認済み） |
| `Fall` 側 preset の個別銘柄シナリオテスト対応 | **対象外** | ロング専用ゆえの意図的な未対応として 2026-08-04 に `backend_specification.md` §6.1 へ明記済み |
| フロントエンド（`ScreenerPage` / `ScreenerResultPage`） | **原則変更しない** | Phase 3 で API のレスポンス互換を保つ。`applied_filters` は追加フィールド（任意表示） |

---

## 3. 変更内容

### Phase 1: フィルタ仕様レジストリ ＋ fail-loud 化

**何を**: フィルタキーの宣言的レジストリを新設し、キーの解釈を1箇所に集約する。

#### 3.1.1 実使用キーの棚卸し（2026-08-10 実測）

`data/screener_presets*.toml` と `backend/backtest/backtest_config.toml`
（`[[strategy]]` + `[strategy.optimization]` + `[optimization.*]`）に登場するキーは **45種**。
これに実行時注入の `min_avg_dollar_volume_21` を加えた **46種**が Phase 1 の対象範囲。

| kind | 件数 | 実例（括弧内は総出現回数） |
| :--- | ---: | :--- |
| `numeric`（Indicator / DailyPrice / 派生列の min_/max_） | 25 | `min_market_cap`(44) `max_sma50_atr_mult`(38) `min_adr_pct_21`(35) `min_vol_surge_21`(31) `min_dist_21ema_pct` `min_change_intraday_pct` |
| `special`（既存の純関数15種のうち実使用10種） | 10 | `rrg_leading_in` `rrg_improving_in` `rrg_lagging_in` `is_rs_macd_hist_rising_21` `is_rs_ratio_rank_e21_gt_e63` `is_rs_trend_s14_lt_s21` `is_theme_rs_ratio_e21_gt_e63` `is_theme_rs_ratio_rank_e14_gt_e21` `is_theme_rs_ratio_rank_e21_gt_e63` `is_theme_rs_trend_rank_s14_gt_s21` |
| `rank`（RelativeRank 列の min_/max_） | 3 | `min_rs_ratio_rank_e14` `min_rs_ratio_rank_e21` `min_rs_trend_rank_s21` |
| `bool_column`（実在の SMALLINT 列の is_） | 2 | `is_trend_template`(11) `is_rs_blue_dot` |
| `close_gt` | 2 | `is_close_gt_ema63`(19) `is_close_gt_sma50` |
| `theme_rank` | 1 | `min_theme_rs_ratio_rank_e21` |
| `theme_numeric` | 1 | `min_theme_rs_trend_s21` |
| `expression` | 1 | プリセット3件で使用 |
| 実行時注入 | 1 | `min_avg_dollar_volume_21`（TOML には書かれない） |

> [!IMPORTANT]
> **プリセットファイルは4つあるが、git 管理下にあるのは `screener_presets.toml` だけ。**
> `screener_presets_A_only.toml` / `_B_only` / `_E_only` は本体チェックアウトにのみ存在する
> 未追跡の実験用ファイル（個別戦略のシナリオテスト用）で、**ワークツリーには存在しない**。
> この3ファイルにしか無いキーが2件ある: `max_dist_52w_high_pct` と `is_theme_rs_ratio_e21_gt_e63`。
> - 前者はルールベース解決（`max_` + 実在の Indicator 列）で**自動的にカバーされる**。
>   明示登録は不要で、これがルールベース設計の利点そのもの。
> - 実データ整合テストは `data/screener_presets*.toml` を **glob して「在るものを全部」対象にする**
>   （本体では4ファイル、ワークツリーでは1ファイル）。手書きの実験用プリセットで使ったキーも
>   レジストリで解決できなければ、そこが F1 の抜け穴になるため。

> [!NOTE]
> `SPECIAL_FILTER_KEYS` は **15種**（当初 14 と記載していたが実測は 15）。うち実使用ゼロは
> **5種**（`is_vcp_breakout` / `is_theme_rs_ratio_e14_gt_e21` / `is_rs_trend_s21_lt_s63` /
> `is_theme_rs_trend_rank_s21_gt_s63` / `is_rs_ratio_rank_e14_gt_e21`）。**削除せずレジストリに載せる**
> （issue_list P1「区分B」で採用候補として追跡中のため）。
> なお `is_theme_rs_ratio_e21_gt_e63` は未追跡の `_B_only` で使われているため「実使用」に分類した。

#### 3.1.2 モジュール配置と依存方向（設計判断）

**`backend/indicators/` の純粋性を壊さないため、レジストリは DB モデルを import しない。**

`indicators/` は `architecture.md` §3 で「純粋な計算アルゴリズム用モジュール群」と定義されている。
一方でレジストリは「どのカラムが実在するか」を知る必要がある。この2つを両立させるため、
**レジストリは「カラム名の文字列」だけを扱い、実在判定は呼び出し側が渡す `known_columns` に委ねる**。

```python
# backend/indicators/screener_registry.py（新規・pandas も SQLAlchemy も import しない）

class UnknownFilterKeyError(ValueError): ...      # 未知のキー
class MissingFilterColumnError(ValueError): ...   # 必要カラムが供給されていない

@dataclass(frozen=True)
class FilterSpec:
    key: str
    kind: str          # numeric|rank|theme_numeric|theme_rank|bool_column|close_gt|special
    column: str | None        # 比較対象の正準カラム名（special は None）
    op: str | None            # ">=" | "<=" | "=="
    requires: tuple[str, ...] = ()        # 当日に必要な正準カラム名
    prev_requires: tuple[str, ...] = ()   # 前日に prev_ 付きで必要なカラム名
    params: tuple[str, ...] = ()          # 随伴する数値パラメータキー（VCP の閾値群など）

EXPLICIT_SPECS: dict[str, FilterSpec]   # special / close_gt など明示登録が要るもの
METADATA_KEYS: frozenset[str]           # フィルタではない制御キー（後述）

def resolve_filter_spec(key: str, known_columns: AbstractSet[str],
                        rank_columns: AbstractSet[str]) -> FilterSpec:
    """キー1つを FilterSpec に解決する。解決できなければ UnknownFilterKeyError。

    解決順序（先に一致したものを採用）:
      1. EXPLICIT_SPECS（special / close_gt）
      2. min_theme_ / max_theme_ + rank_columns  -> theme_rank
      3. min_theme_ / max_theme_ + known_columns -> theme_numeric
      4. min_ / max_ + rank_columns              -> rank
      5. min_ / max_ + known_columns             -> numeric
      6. is_ / bool_ / has_ + known_columns      -> bool_column
      -> いずれにも当たらなければ raise
    """

@dataclass(frozen=True)
class RequiredColumns:
    today: frozenset[str]   # 基準日に必要なカラム（正準名）
    prev: frozenset[str]    # 前日に必要なカラム（prev_ を付けない素の名前）
    ranks: frozenset[str]   # RelativeRank から必要なカラム

def resolve_required_columns(strategy: Mapping, known_columns, rank_columns) -> RequiredColumns:
    """戦略dict全体から必要カラム集合を導出する（METADATA_KEYS は無視）。
    データ供給側（クロスセクション構築・日次マージ）はこの結果だけを見ればよい。"""
```

> [!NOTE]
> `RequiredColumns` は **frozen dataclass で属性アクセス**（`req.today` / `req.prev` / `req.ranks`）。
> `prev` は `prev_` 接頭辞を**付けない素のカラム名**で持ち、接頭辞の付与は供給側（マージ処理）の責務。
> 供給側ごとに接頭辞の規約が違う（API は `prev_` 固定、バックテストは `rename` で付与）ため、
> レジストリは論理名だけを持つ。

- **`known_columns` の供給元**: API 側は `Indicator.__table__.columns` + `DailyPrice.market_cap` +
  仮想カラム定義、バックテスト側は `merged.columns`（実際に手元にある列）。
  **「宣言されている」だけでなく「実際に供給されている」ことを検査できるのが要点**（P0-2 / F4 対策）。
- **`rank_columns`**: `RelativeRank.__table__.columns`（API）／ ranks DataFrame の列（バックテスト）。
- 既存の `SPECIAL_FILTER_KEYS`（`screener_filters.py`）は `EXPLICIT_SPECS` からの**導出値として残す**
  （外部参照が3ファイルあるため後方互換を保つ）。
- **特殊フィルタの関数本体は `screener_filters.py` のまま**。レジストリは関数参照を持たず
  `kind="special"` とキーだけを持ち、ディスパッチは既存の `evaluate_special_filters` /
  `apply_filters_to_df` が引き続き担当する（**Phase 1 では dispatch の構造を動かさない**。
  変更範囲を「キーの解釈」と「カラム供給」に限定して回帰リスクを抑える）。

#### 3.1.3 fail-loud の契約

| 検査 | 例外 | 発火箇所 |
| :--- | :--- | :--- |
| キーがどの kind にも解決できない | `UnknownFilterKeyError` | `resolve_filter_spec` |
| 解決できたが `requires` のカラムが実際には供給されていない | `MissingFilterColumnError` | フィルタ適用の直前 |

- 両方 `ValueError` のサブクラス。§1.3 の先行事例（`report_strategy_scan_coverage` /
  `resolve_portfolio_params`）が `ValueError` で停止する契約に揃える。
- **CLI 経路**（backtest / optimization / scenario）は例外をそのまま伝播させて停止する。
- **API 経路**は U-1 の決定に従い、プリセット単位で捕捉して当該プリセットのみ「エラー」を返し、
  他のプリセットは正常表示する（`_build_preset_query` の呼び出し元で try/except）。

> [!WARNING]
> **`max_hits_per_day` / `sort_column` / `min_avg_hits_per_day` / `max_allowed_dd` 等のメタキーを
> フィルタキーと誤認して例外にしないこと。** 現在この除外集合は `validate_strategies_config` の
> `METADATA_KEYS`（`backtest_runner.py`）と `apply_filters_to_df` の skip リスト
> （`backtest_screener.py` L370）と `_build_preset_query` の個別 `elif`（`screener_router.py` L634-637）に
> **3箇所へ分散している**。レジストリ側の `METADATA_KEYS` に集約し、3者ともそれを参照する。

#### 3.1.4 データ供給側の設計（2026-08-10 確定）

ここが Phase 1 で最もリスクが高い。**merged フレームの中身が実際に変わる**ため。

##### (a) 正準名 ↔ フレーム内名の対応をレジストリへ集約

レジストリは**正準名**（`rs_ratio_rank_e21`）で話すが、フレーム内の実列名は
**フレーム内名**（`rs21_rank`）という第3の命名になっている。この対応表は現在2箇所にあり、
**6件とも完全一致している**ことを実測で確認した:

| 場所 | 内容 |
| :--- | :--- |
| `screener_cross_section._RANK_COL_MAP`（L54-61） | 6件 |
| `backtest_screener.apply_filters_to_df` の `alias_map`（L354-367） | 同じ6件 ＋ 4件の付随エントリ |

→ レジストリに `RANK_FRAME_ALIASES: dict[str, str]`（正準名 → フレーム内名、未登録は恒等）を新設し、
両者がこれを参照する。

`alias_map` の残り4件は**レジストリ化で不要になる**:
- `change_intraday_pct` / `dist_21ema_pct` … 恒等写像（元から無意味）
- `rs_blue_dot` → `is_rs_blue_dot` / `rs_red_dot` → `is_rs_red_dot` …
  `is_` 接頭辞を機械的に剥がしていたことの後始末。レジストリの `kind='bool_column'` は
  **キーをそのまま列名として扱う**ため、この種のズレが原理的に起きない
  （2026-07-22 の `is_rs_blue_dot` サイレント素通しバグの構造的解消）

##### (b) `MissingFilterColumnError` をここで発火させる

Phase 1 の核心。`apply_filters_to_df` は merged 構築後・フィルタ適用前に
**必要カラムが実際に揃っているかを検査**し、欠けていれば `MissingFilterColumnError` で停止する。

これで過去の実障害が構造的に捕捉できるようになる:

| 過去の障害 | 検知される理由 |
| :--- | :--- |
| `avg_dollar_volume_21` の `_x/_y` サフィックス衝突 | 素の列名が merged に無い → 例外 |
| `is_rs_blue_dot` の alias 不一致 | (a) により発生しない。仮に起きても列不在で例外 |
| `min_rs_ratio_rank_e14/e63` の `needs_rs*` 未登録 | 必要ランクがレジストリ由来になるため発生しない |
| P0-2 の6ランク列 | `min_rs_value_rank` 等を書くと列不在で例外（現在は黙って無視） |

##### (c) long/wide の往復（P0-1）は Phase 1 では**直さない**

`preload_data` の melt（wide→long）と日次の再 wide 化は無駄だが、これを解消するには
`preload_data` / `backtest_screener` / `scenario_runner` とテストフィクスチャを同時に変える必要があり、
**Phase 1 の「差分ゼロ」目標に対して blast radius が大きすぎる**。
Phase 3 の `ScreenerFrame` 契約でまとめて解消する。Phase 1 は長形式のまま、
「どのランクを引くか」の決定だけをレジストリ駆動に置き換える。

##### (d) `known_columns` / `rank_columns` の供給元

| 経路 | `known_columns` | `rank_columns` |
| :--- | :--- | :--- |
| `apply_filters_to_df`（②③） | `df_ind.columns ∪ df_price.columns ∪ VIRTUAL_COLUMNS` | `RelativeRank` モデルの実カラム（**`df_ranks` は long 形式で列名にランク名が出ないため使えない**） |
| `build_cross_section`（①） | `Indicator` モデルの実カラム ∪ `market_cap` ∪ `VIRTUAL_COLUMNS` | `RelativeRank` モデルの実カラム |

##### (e) `build_cross_section` は「全特殊フィルタの必要カラムの和集合」を取る

API のクロスセクションは**リクエストごとに1回**構築され、全プリセットで共有される。
戦略ごとの必要カラムは使えないため、`_IND_COLS` / `_PREV_COLS` は
**レジストリの `kind='special'` 全 spec の `requires` / `prev_requires` の和集合**から導出する
（1日分 × 約3,100行なので数列多くても実質無コスト）。
これで新しい特殊フィルタを足したときにクロスセクションが自動追従し、
手書きリストの更新漏れ（F4）が起きなくなる。

**なぜ**: F1（サイレント素通し）と F4（データ供給の差）を構造的に消すため。
「必要カラム」がフィルタ自身の宣言になれば、`needs_rs14/21/63` の or 連鎖・`_IND_COLS`・
`prev_merge_cols`・`price_cols` といった手書きリストの同期漏れが原理的に発生しない。

**対象**:
- 新規: `backend/indicators/screener_registry.py`（`FilterSpec` / `EXPLICIT_SPECS` / `METADATA_KEYS` /
  `resolve_filter_spec()` / `resolve_required_columns()` / 例外2種）
- 改修: `backend/indicators/screener_filters.py`（`SPECIAL_FILTER_KEYS` をレジストリ導出値へ。関数本体は不変）
- 改修: `backend/api/screener_router.py`（`_build_preset_query` の `is_known` 判定 L639-676 をレジストリ参照へ）
- 改修: `backend/api/screener_cross_section.py`（`_IND_COLS` / `_PREV_COLS`（L38-52）をレジストリから導出）
- 改修: `backend/backtest/backtest_screener.py`（`needs_rs*` or 連鎖 L182-224・deny-by-default L299-321・`prev_merge_cols` L341 をレジストリ導出へ置換）
- 改修: `backend/backtest/backtest_runner.py`（`validate_strategies_config` L540-675 をレジストリ参照へ。
  `api.screener_router` への逆依存 L550 と `except ImportError` の握り潰しを削除）
- 改修: `backend/backtest/scenario_runner.py`（ランク事前マージ L508-531 をレジストリ導出へ）
- 新規: **全経路共通ルールの単一実装**
  - 流動性床の注入（4モジュール5箇所 → 1つの純関数）
  - テーマ行の最終出力除外（2モジュール3箇所 → 1箇所。SQL 側はクエリ述語、pandas 側はマスクを返す形で
    共有できる部分は共有し、少なくとも「除外するという事実と理由」の定義を1箇所にする）

**fail-loud の適用レベル**（§4 U-1 でユーザー確認。設計は §1.3 の `report_strategy_scan_coverage` に倣う）:

| 経路 | 未知キー | 必要カラム欠落 |
| :--- | :--- | :--- |
| バックテスト／最適化／シナリオテスト（CLI） | **`ValueError` で停止**（現在は warning 出力のみで実行継続） | **`ValueError` で停止** |
| スクリーナー API | プリセット検証で **ERROR ログ＋該当プリセットを明示的に「エラー」として返す**（他プリセットは正常表示） | 同左 |

**`applied_filters` の記録**:
- API: レスポンスに `applied_filters: list[str]` を追加（`/screener`・`/screener/dashboard`）
- バックテスト／シナリオテスト: 結果 JSON と実行ログに適用済みキー一覧を出力
  （`resolve_portfolio_params` が上書き項目を `Portfolio overrides: {...}` として表示するのと同じ思想）

**影響範囲（非互換・副作用）**:
- ⚠️ **fail-loud 化により、これまで黙って無視されていたフィルタが有効化される／エラーになる可能性がある。
  その場合スクリーン結果が変わり、過去の最適化 study・シナリオテスト結果と非互換になる**（S-1/S-2 統一時に
  プリセットの items が 89→92 に変化した前例と同型）。Phase 1 の検証では、この差分を**必ず全戦略・
  全プリセットについて実測し、ユーザーへ報告する**（§6）。

### Phase 2: 経路間パリティテスト（レジストリ駆動）

**何を**: `FILTER_SPECS` の全キーを列挙し、同一の合成データを SQLite（`api` 経路）と
Parquet／DataFrame（`backtest` 経路）の両方に投入して、通過 `symbol_id` 集合の一致を検証するテストを追加する。

- **レジストリ駆動**: `FILTER_SPECS` に登録があるのにフィクスチャが無いキーは**テストが落ちる**
  （＝新フィルタの実装漏れがレッドになる）
- **全経路共通ルール（テーマ除外・流動性床）も検証対象に含める**。テーマ混入バグ（§1.2）は
  「フィルタキー単位」ではなく「出力ルール単位」の不一致だったため、キーごとのパリティだけでは捕まらない
- S-1 / S-2 のような意図した差異は、`EXPECTED_DIVERGENCE` として理由付きで明示登録し、
  それ以外の不一致は全て失敗とする
- D-2 Phase 2 で作成済みの同値性テスト（`backend/tests/api/test_screener_special_filter_behavior.py` 等）を
  新テストへ統合する

**なぜ**: F2（片側のみ実装）と F3（意味論の差）を検知するため。**テーマ混入バグを 07-29 時点で捕まえられる形にする**。

**対象**: `backend/tests/` 配下（`test_screener_parity.py` 新設＋既存同値性テストの統合）

**影響範囲**: テストのみ。プロダクションコードは変更しない。

#### 3.2.1 詳細設計（2026-08-11 確定）

##### (a) データは「1つの真実」から両形式へ書き出す

パリティテストの最大の落とし穴は、**フィクスチャ自体が2つに分かれて食い違う**こと
（それでは検証しているつもりで何も検証していない）。したがって:

```
_PARITY_DATASET（プレーンな dict/list。これが唯一の真実）
  ├── seed_sqlite(session)   → symbols / daily_prices / indicators / relative_ranks / theme_constituents
  └── to_dataframes()        → df_symbols / df_prices / df_ind / df_ranks(long) / df_tc
```

**ランクは SQLite が wide・バックテストが long** という既知の差（P0-1）があるため、
`to_dataframes()` 側で long へ変換する。**この変換もデータセット定義から機械的に行い、手書きしない。**

##### (b) 母集団は小さく保つ（Top-N 打ち切りを避けるため）

- API 経路は `_build_preset_query` を使う（`/screener` の bool クエリ引数は9種しか無く
  特殊フィルタ15種を網羅できないため）。`_build_preset_query` は
  `get_screener_dashboard` 内のクロージャなので、`_load_presets` を差し替えて
  合成プリセット1件を注入する形で呼ぶ（既存 `test_special_removal.py` と同じ手法）。
- `get_screener_dashboard` は **top-8 で打ち切る**ため、**銘柄数は8以下**にする
  （テーマ2 + 個別6 程度）。打ち切りが起きていないことをテスト内で明示的に assert する。
- 流動性床が邪魔をしないよう、全銘柄に十分大きい `avg_dollar_volume_21` を持たせる
  （床そのもののパリティは別テストで検証する）。

##### (c) 各キーの「境界値」を1箇所で宣言する

```python
PARITY_CASES: dict[str, Any] = {
    "min_vol_surge_21": 1.5,      # 通過する銘柄と落ちる銘柄が both 出る値
    "is_trend_template": True,
    ...
}
```

- **`FILTER_SPECS` にあって `PARITY_CASES` に無いキーはテストが失敗する**（実装漏れ＝レッド）。
- 各ケースは「**全通過でも全落ちでもない**」ことを assert する。
  全通過/全落ちだと、両側が壊れていても一致してしまい検証にならない。

##### (d) 共通ルールは別立てで検証する

テーマ混入バグは「フィルタキー単位」ではなく「**出力ルール単位**」の不一致だったため、
キーごとのパリティでは捕まらない。以下を独立したテストにする:

| 検証 | 内容 |
| :--- | :--- |
| テーマ除外 | 両経路の出力に `category=='テーマ'` が1件も無いこと。かつ**テーマ行を必要とするフィルタ**（`is_theme_*`）が正しく動くこと（＝中間データとしては保持されていること） |
| 流動性床 | 床未満の銘柄が両経路の出力に出ないこと |

##### (e) 意図した差異の扱い

```python
EXPECTED_DIVERGENCE: dict[str, str] = {}   # キー -> 理由
```

D-2 で S-1（`min_market_cap` のテーマ免除）/ S-2 は**両側で統一済み**のため、
初期値は**空**を想定する。空でなくなった場合は理由を必ず書き、§7 に記録する。

### Phase 3: スクリーナーの完全 DataFrame 化（単一エンジン）

**何を**: 正準クロスセクション契約を定義し、ローダを2つ用意して、その上に単一のフィルタエンジンを載せる。

```
ScreenerFrame（1営業日分・wide・正準列名・派生列込み）
  ├── load_from_sqlite(db, date)     ← スクリーナー API
  └── load_from_parquet(cache, date) ← 最適化バックテスト／シナリオテスト
            ↓
     apply_filters(frame, strategy)  ← 唯一の実装
```

- `_apply_filter`（SQLAlchemy、148行）と `_parse_expression_to_filter`（80行）を撤去。SQL は取得のみ
- **ランクの long/wide 往復変換を廃止**（Phase 0 で実測: **Parquet の `ranks` は SQLite と同じ wide 形式**
  なのに、`backtest_runner.py:124-140` が毎回 wide→long に melt し、`backtest_screener.py` と
  `scenario_runner.py` が日次で long→wide に戻している。純粋な無駄であり F1 の温床。→ P0-1）
- **melt が落としている6ランク列を復活させる**（`rs_value_rank`・`rs_roc_ema_rank_e5/e14/e21/e63/e200`。
  Parquet/SQLite の双方に存在するのにバックテストからは恒久的に見えない状態。→ P0-2。
  なお Phase 1 のレジストリ導出を入れた時点で自動的に供給されるようになる見込み）
- パリティテストの対象が「エンジンの一致」から「**2つのローダが同一フレームを作るか**」へ縮退する
  （これが SQLite と Parquet の間で本質的に異なる唯一の部分）

**なぜ**: F2・F3 を構造的に消すため。実装箇所が1つしかなければ「片側だけ実装」も「意味論の差」も原理的に生じない。

**対象**:
- 新規: `backend/indicators/screener_frame.py`（契約定義＋`assert_frame_contract()`）
- 改修: `backend/api/screener_router.py`（大改修）、`backend/api/screener_cross_section.py`（ローダへ吸収）
- 改修: `backend/backtest/backtest_screener.py`、`backend/backtest/backtest_runner.py`、`backend/backtest/scenario_runner.py`
- 更新: `doc/backend_specification.md` §6.7（**現状の記述は陳腐化している** — `avg_dollar_volume_21` が
  抽出対象に載っておらず、`relative_ranks` を long 形式前提で説明している）

**影響範囲**:
- ⚠️ 回帰リスク最大。API のソート・上位200件スライス・sparkline 生成等の周辺処理を作り直す必要がある
- ⚠️ 性能退行の可能性（1日分 ≒ 3,000銘柄 × 数十列。API は既に特殊フィルタ用に同等のクロスセクションを
  構築しているため軽微と見込むが、§1.5 のベースラインに対する受入基準で担保する）

---

## 4. ユーザー確認事項

**2026-08-10、ユーザーレビュー完了。U-1〜U-6 すべて合意（提案どおり）。以下は確定した判断の記録。**

| # | 論点 | **決定** | 反映先 |
| :--- | :--- | :--- | :--- |
| **U-1** | スクリーナー API の fail-loud レベル | **(b) 該当プリセットのみ「エラー」として返し、他は正常表示**（画面は生きる）。CLI 側（バックテスト／最適化／シナリオテスト）は §1.3 の先行事例に倣い `ValueError` で停止 | §3 Phase 1「fail-loud の適用レベル」表 |
| **U-2** | 既存 TOML に fail-loud で落ちるキーが見つかった場合 | **(a) その場で正しいキーへ修正**。ただし修正でスクリーン結果が変わるため、差分を報告してから確定する | §5 Phase 1 の差分実測タスク |
| **U-3** | スクリーン結果が変わった場合の再最適化 | 差分の実測を先に出し、**再実行の要否はユーザー判断**。2026-08-01 に全12戦略の再実行・再最適化を済ませたばかりのため、**Phase 1 は「差分ゼロ」を目標**に進め、差分が出たら1件ずつ理由を説明する | §6.2 の期待値 |
| **U-3a** | **P1-7（`is_trend_template` 無効）の是正で D/E1/E2/F に差分が出た件（2026-08-10 追加判断）** | **このまま Phase 1 を進め、再最適化はユーザーのタイミングで実施する**。Phase 1 の残作業（流動性床・テーマ除外の集約、`applied_filters`）は抽出結果を変えない性質のものであり、また Phase 2 のパリティテストを入れてから再最適化した方が同種の隠れバグを二度踏まずに済むため | §8 の残作業へ |
| **U-4** | Phase 3 の性能受入基準 | **ベースライン × 1.5 倍以内**。超えたら Phase 3 は差し戻し | §6.3 の受入基準 |
| **U-5** | sandbox 検証の要否 | **種別 A（データ変更なし）**として扱う。スキーマ変更なし・DB は読み取りのみ。API スモークのみ `STOCKTOOL_ENV=sandbox` で実施 | §6.5 |
| **U-6** | Phase の区切りでのコミット／レビュー | 各 Phase 完了時にオーケストレーターが diff 確認＋全テスト実行し、ユーザーへ報告してから次へ進む | §5 の Phase 区切り |

---

## 5. 実装順序と進捗チェックリスト

### Phase 0: 着手前の実測（ベースライン確定） ✅ 完了（2026-08-10）

- [x] `pytest` 全件実行し、件数と所要時間を記録 → **795 passed / 115.98 秒（All Green）**
- [x] `/screener`・`/screener/dashboard` のウォーム応答時間を実測 → §1.5 の表に反映。
      **コールド初回とウォームで 20〜40 倍差があることを発見**し、切り分け済み
- [x] 現行の全プリセット・全戦略について抽出銘柄集合をスナップショットとして保存
      → `tmp/phase0_snapshot.py`（読み取り専用）／出力 `tmp/phase0_baseline_20260813_121011.json`
      - 対象日5件: `2026-08-11` / `08-04` / `07-28` / `07-13` / `06-10`（最新から 0/5/10/21/42 営業日前）
      - 経路①: `/screener/dashboard` 全18プリセットの top-8 ＋ `/screener` の母集団200件
      - 経路②③: `backtest_config.toml` 全16戦略の `scan_signals_for_date` 通過銘柄
      - フィルタロジックは**再実装せず実際のエンドポイント関数・`scan_signals_for_date` を直接呼ぶ**
        （再実装するとスナップショット側のバグと本体の変化を区別できなくなるため）
- [x] スナップショットで **テーマ除外・流動性床が①②③すべてで効いていること**を確認
      → **[OK] テーマ・仮想合成指数（`_XXX_` 形式）の混入は①②③すべてでゼロ**。2026-08-05 の修正は
      全経路に行き渡っている

### Phase 1: レジストリ ＋ fail-loud（案1）

- [ ] **[TDD-red]** `FILTER_SPECS` の単体テスト（全キーの `requires` が実カラムに解決できること、
      キーの重複が無いこと、`SPECIAL_FILTER_KEYS` との整合）
- [x] `FilterSpec` / `EXPLICIT_SPECS` / `resolve_filter_spec()` / `resolve_required_columns()` の実装
      → `backend/indicators/screener_registry.py`（新規）。`screener_filters.SPECIAL_FILTER_KEYS` は
      `EXPLICIT_SPECS` の kind="special" からの導出値に置換（関数本体は不変）。
      `backend/tests/indicators/test_screener_registry.py` 全30件（25ケース、うち6件は
      parametrize 展開）GREEN。呼び出し側（screener_router.py 等）の置換は次工程。
- [ ] **[TDD-red]** fail-loud のテスト（未知キー・必要カラム欠落で `ValueError`。
      現状の「黙って通す」挙動でレッドになることを確認）
- [x] `backtest_screener.py` の `needs_rs*` or 連鎖・deny-by-default 分岐・`prev_merge_cols` を
      レジストリ導出へ置換
      → `screener_registry.RANK_FRAME_ALIASES` / `to_frame_column()` を新設（正準名→フレーム内名。
      `screener_cross_section._RANK_COL_MAP` と `backtest_screener.alias_map` の重複6件を統合）。
      `apply_filters_to_df` は `resolve_required_columns()` の結果（`required.today/prev/ranks`）
      だけを見て merge するよう全面置換。`needs_rs14/21/63` の or 連鎖・deny-by-default の重複 or
      連鎖・`prev_merge_cols` 決め打ちリストを削除。`alias_map` を完全削除
      （`grep alias_map backend/backtest/backtest_screener.py` 0件）し、min_/max_/is_ の汎用ループを
      `FilterSpec.column`＋`to_frame_column()` 経由に置換（副作用として `is_trend_template` の
      alias 剥がしミスによる恒久的サイレント無効化バグが解消 — 差分実測は未実施の別チェックリスト
      項目で報告予定）。**`MissingFilterColumnError` の fail-loud 検査を新設**
      （merge 完了後・マスク適用前）。実装中に判明した2件の例外スコープ調整（設計判断ではなく
      実装バグの修正）:
      1. `is_theme_rs_ratio_e21_gt_e63`/`_e14_gt_e21`（生値のテーマ比較）は `merged` ではなく
         当日の `df_ind` スライスを直接参照する既存 dispatch のため、その `requires` は
         `merged` 存在チェックから除外
      2. `prev_date is None`（バックテスト初日等、前日データが原理的に存在しない）のときは
         RRG/RS-MACD 系の `prev_requires` チェックをスキップ（既存の graceful no-op 設計を保持）。
         `is_vcp_breakout` の `prev_requires` は `prev_date` の有無に関わらず常に除外（既存の
         deny-by-default 設計）
      テスト4件追加（`test_backtest_screener_refactoring.py`）: `MissingFilterColumnError`（欠落列名を
      メッセージに含む）／`_x`/`_y` サフィックス衝突が `UnknownFilterKeyError` で停止（サイレント
      素通ししない）／必要なランクだけ merge される（14/21/63 の6列を無条件に引かない）回帰／
      `is_vcp_breakout` は前日列が無くても deny-by-default のまま例外にならない。全 GREEN。
- [x] `screener_cross_section.py` の `_IND_COLS` / `_PREV_COLS` をレジストリ導出へ置換
      → `kind='special'` 全 spec の `requires`/`prev_requires` の和集合から `Indicator` 実カラムのみ
      抽出する形に変更。導出結果が旧ハードコードと完全一致することを確認済み（`is_vcp_breakout` の
      `requires` に `vcr` を追加登録して包含関係を成立させた。§7 に記録）。`_RANK_COL_MAP` は
      `screener_registry.RANK_FRAME_ALIASES` を直接参照する形に置換（重複定義を削除）。
- [x] `scenario_runner.py` のランク事前マージをレジストリ導出へ置換（`has_all_premerged` 分岐の解消）
      → `run_scenario_test` 冒頭で `SCENARIO_TARGET_PREFIX` に一致する全戦略の
      `resolve_required_columns().ranks` の和集合を1回だけ計算し、日次ループ内の
      14/21/63 決め打ちループを `required_rank_columns` ベースの merge に置換。
      `to_frame_column()` で `apply_filters_to_df` 側と同じ名前に揃えた（2026-07-18 の
      `has_all_premerged` 誤判定の再発防止）。
- [x] `backtest_runner.py::validate_strategies_config` をレジストリ参照へ置換し、
      `api.screener_router` への逆依存と `except ImportError` の握り潰しを削除
      → `resolve_filter_spec()` へ全面置換。手書き分類（`rank_column_names`/`alias_map`/接頭辞の
      場合分け）を削除。戻り値は引き続き `list[str]`（純関数）だが、呼び出し側（`run_backtest`）で
      非空なら `ValueError` を送出して停止するよう変更（U-1 の CLI 側決定）。
      `strategy.optimization` サブ dict のキーも検証対象に含めた。
      テスト3件追加（`test_backtest_runner_entry.py`）: タイポキーでエラー、METADATA_KEYS は
      エラーにならない、現行 `backtest_config.toml` 全戦略でエラー0件。GREEN。
- [x] `screener_router.py::_build_preset_query` の `is_known` 判定をレジストリ参照へ置換
      → 手書きの `is_known` 判定ブロックを `resolve_filter_spec()` 呼び出しに置換（`known_columns`
      = `_INDICATOR_COLUMNS.keys()` ∪ `_VIRTUAL_COLUMNS.keys()`、`rank_columns` = `RelativeRank`
      の実カラムから制御列を除いたもの）。`_use_hysteresis`/`rrg_intensity_threshold` の個別 `elif`
      は `screener_registry.METADATA_KEYS` 参照に統一。`special_flags` の分岐・`_apply_filter()` の
      呼び出し自体は変更なし。fail-loud: `UnknownFilterKeyError` はプリセット単位で捕捉し、
      `ScreenerDashboardCategory` に `error: Optional[str] = None` を追加（後方互換）、
      失敗プリセットは `items=[]` + `error` 付きで返し他のプリセットは正常表示を継続するよう変更
      （旧実装は `except Exception` でカテゴリごと黙って落としていた＝サイレント失敗だった）。
      テスト2件追加（`test_screener_api.py`）: 未知キーを含むプリセットが `items=[]`+`error` 付きで
      返り他は正常表示、現行 `data/screener_presets.toml` 全プリセットで `error` が None。
      GREEN（テスト用の共有 DB フィクスチャに、特殊フィルタが要求する rs_trend_s14/s21・
      rs_macd_hist_21・テーマ側のランク列を追加補完 — 本番は T3/T4 が一括で埋めるため
      未発生だが、最小フィクスチャでは None 混在による `TypeError` を誘発していた）。
      `grep backend.api.screener_router backend/backtest/backtest_runner.py` は0件（逆依存解消済み）。
      全体テスト 829 passed / 1 failed（環境要因の1件のみ、期待通り）。

      **（データ供給側3モジュール完了後の全体テスト）835 passed / 1 failed**（環境要因の1件のみ、
      期待通り。既存テスト2件（`test_scenario_runner_integration` / `TestRrgLeadingIn`・
      `TestRrgLaggingIn` の `test_no_prev_date_skips_rrg_filter`）はレジストリ導出化に伴う
      新規フィクスチャ不足で一時的にレッドになったため修正済み — 詳細は §7 参照）。
- [x] 流動性床（`min_avg_dollar_volume_21`）の注入を単一の純関数へ集約（4モジュール5箇所 → 1箇所）
      → 新規 `backend/backtest/common_constraints.py`（`load_min_avg_dollar_volume_21()` /
      `inject_liquidity_floor()` / `inject_liquidity_floor_all()`）。5箇所すべてを委譲に置換:
      `screener_router._load_min_avg_dollar_volume_21`・`backtest_runner.run_backtest`・
      `optimization_runner.py`（`run_holdout_validation`／`objective` の2箇所）・
      `scenario_runner.inject_liquidity_floor`（関数名は維持し中身のみ委譲。既存テストとの
      後方互換を確認済み）。テスト11件（`test_common_constraints.py`）追加、GREEN。
      **🔴 P1-8 も同時に修正**: `screener_router._build_preset_query`（`/screener/dashboard`）に
      `Indicator.avg_dollar_volume_21 >= floor` を追加（`get_screener()` と同じ無条件フィルタ、
      適用位置はテーマ除外と同じくフィルタ後・出力直前）。**意図的な挙動変更**（dashboard の
      表示銘柄が減る）。回帰テスト2件追加（`test_screener_dashboard_excludes_illiquid_symbols`
      ほか）。既存テスト `test_special_removal.py` の5件が、フィクスチャに
      `avg_dollar_volume_21` 未設定（NULL）のため新しい床フィルタで無条件除外されてレッドに
      なったため、フィクスチャへ明示値を追加して修正（設計判断ではなくテストフィクスチャの
      補完。§7 に類例あり）。
- [x] テーマ行の最終出力除外を単一定義へ集約（2モジュール3箇所 → 1箇所）
      → `indicators/screener_registry.py` に `OUTPUT_EXCLUDED_CATEGORIES: frozenset = {'テーマ'}`
      を新設。3箇所とも参照に置換: `backtest_screener.py`（`filtered['category'].isin(...)`）・
      `screener_router.py` の `_build_preset_query`／`get_screener()`（`Symbol.category.notin_(tuple(...))`）。
      適用位置（フィルタ後・出力直前）は変更していない。テスト1件追加
      （`TestOutputExcludedCategories`）、GREEN。
- [x] `applied_filters` の記録・出力（API レスポンス／バックテスト結果 JSON／ログ）
      → `schemas.ScreenerDashboardCategory` に `applied_filters: Optional[List[str]] = None` を
      追加（既存フィールドは無変更）。`_build_preset_query` が `_apply_filter` を通したキー ＋
      特殊フィルタキー ＋ 常時適用の制約（`min_avg_dollar_volume_21` / `exclude_theme_category`）
      をソート済みリストで返す。テスト1件追加
      （`test_screener_dashboard_applied_filters_include_liquidity_floor`）。
      `/screener` はレスポンスが `List[ScreenerResultItem]` でエンベロープが無いため
      **今回はレスポンスに追加しない**（U-1/計画書の制約どおり）。代わりに `logger.info` で
      適用キー一覧を出力。`backtest_runner.run_backtest()` は戦略ごとに実行開始時、
      `screener_registry.is_non_filter_key()` でメタキー・随伴パラメータを除いたキー集合を
      1行ログ出力（日次ループの外）。`scenario_runner.py` は本チェックリスト項目のログ出力
      対象に含めていない（計画書 §3.1.4 の記述は `run_backtest()` のみを明示）。
- [ ] **差分実測**: Phase 0 のスナップショットと突合し、抽出銘柄が変化したプリセット・戦略を全件列挙
- [ ] 全テスト実行 → **ユーザーへ差分報告し、U-2/U-3 の判断を仰ぐ**
- [ ] 仕様書更新（`backend_specification.md` §5 にレジストリと fail-loud を明記）

### Phase 2: 経路間パリティテスト（案2）

- [ ] **[TDD]** `FILTER_SPECS` 全キーを列挙するパラメタライズドテストの骨組み
      （フィクスチャ未整備のキーは失敗する形にする）
- [ ] 各キーの境界値フィクスチャ（SQLite シード ＋ 同値の DataFrame）を整備
- [ ] **全経路共通ルールのパリティ**（テーマ除外・流動性床）を独立したテストとして追加
      — テーマ混入バグを 07-29 時点で捕まえられる形になっているかを、当時のコードで**レッドを再現**して確認
- [ ] `EXPECTED_DIVERGENCE`（S-1 / S-2 等、意図した差異）を理由付きで登録
- [ ] 既存の同値性テスト（`test_screener_special_filter_behavior.py` 等）を新テストへ統合（重複を解消）
- [ ] pytest 全件での実行時間を確認し、遅すぎる場合はマーカーで分離
- [ ] 全テスト実行 → ユーザー報告
- [ ] 仕様書更新（`architecture.md` §7.1 に「新フィルタ追加時はレジストリ登録＋パリティフィクスチャ必須」を明記）

### Phase 3: 完全 DataFrame 化（案3）

- [ ] `ScreenerFrame` 契約の定義（正準列名・dtype・派生列・wide ランク）と `assert_frame_contract()`
- [ ] **[TDD-red]** 2つのローダが同一フレームを返すことのテスト
- [ ] `load_from_sqlite()`（`build_cross_section` を拡張）／`load_from_parquet()` の実装
- [ ] ランクの long/wide 変換を廃止し、両経路とも wide で統一
- [ ] `apply_filters(frame, strategy)` を単一エンジンとして確立（`apply_filters_to_df` を改称・整理）
- [ ] `screener_router.py` を DataFrame 経路へ切替（SQL は取得のみに縮退）
- [ ] `_apply_filter` / `_parse_expression_to_filter` の削除
- [ ] 性能実測（U-4 の受入基準に対する合否判定）
- [ ] 抽出結果の差分実測（Phase 1 完了時点のスナップショットと突合し、差分ゼロを確認）
- [ ] 全テスト実行 + sandbox での API スモーク
- [ ] 仕様書更新（`architecture.md` §2/§3、`backend_specification.md` §5・**§6.7 の陳腐化解消**）、
      `issue_list.md` P3「スクリーナーの完全 DataFrame 化」をクローズ

### 作業中メモ

**現在地（2026-08-10）**: Phase 1 の **レジストリ本体（`screener_registry.py`）まで完了・検収済み**。
次は**呼び出し側6モジュールの置き換え**（`backtest_screener.py` の `needs_rs*` or 連鎖 →
`screener_cross_section.py` の定数リスト → `scenario_runner.py` のランク事前マージ →
`backtest_runner.py` のバリデータと逆依存削除 → `screener_router.py` の `is_known` 判定）。

- 作業ブランチ: `worktree-screener-filter-registry`（`.claude/worktrees/screener-filter-registry`）
- **差分実測の手順**（ワークツリーのコードを本番データに向けて動かす。読み取り専用）:
  ```powershell
  # 1. ワークツリー内で、本体の DB を絶対パスで指定して取得
  #    （Parquet マスターの場所は DB パスの階層から自動解決される）
  cd .claude\worktrees\screener-filter-registry
  $env:PYTHONPATH="backend"
  & "d:\My Documents\Programing\stocktool\venv\Scripts\python.exe" tmp/phase0_snapshot.py `
      "d:\My Documents\Programing\stocktool\data\stocktool.db"

  # 2. 本体チェックアウトでベースラインと突合（差分ゼロなら exit 0）
  cd d:\My Documents\Programing\stocktool
  .\venv\Scripts\python.exe tmp/phase0_compare.py `
      tmp/phase0_baseline_20260813_121011.json `
      .claude/worktrees/screener-filter-registry/tmp/phase0_baseline_<新しい方>.json
  ```
  `tmp/phase0_compare.py` は**変異データを注入して検出できることを確認済み**
  （銘柄の消失・架空銘柄の追加・テーマ混入・共通ルール NG の4種）。
  「何を渡しても差分ゼロ」と言う道具では意味が無いため、信頼する前に必ずこの確認をすること。
- 注意点は §7 の P0-1〜P0-8 にまとめてある。とくに **P0-5（`Query` 既定値の罠）** は
  Phase 2 のパリティテスト設計時に必ず参照すること

> [!IMPORTANT]
> **ワークツリーでの pytest は必ず1件失敗する（環境要因・無視してよい）。**
> `backend/tests/backtest/test_scenario_comparison.py::test_run_comparison_generates_outputs` が
> `FileNotFoundError: Parquet master cache files not found` で落ちる。`data/` は git 管理外のため
> ワークツリーの `data/parquet_master/` が空であることが原因で、本体チェックアウトでは通る
> （Phase 0 の実測: 本体 795 passed / 0 failed）。
> **ワークツリーでの期待値は「824 passed, 1 failed」**（795 + 新規30 = 825 のうち1件が環境要因）。
> この1件以外が落ちたら、それは本当の回帰。

---

## 6. 検証プラン / 結果

### 6.1 各 Phase 共通

```powershell
# 全テスト
$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/ -v
```

**All Green が各 Phase の必須完了条件**（`architecture.md` §7.1）。

### 6.2 抽出結果の差分実測（Phase 1 / Phase 3）

Phase 0 で取得したスナップショットに対し、全プリセット・全戦略の抽出銘柄集合を突合する。

| 期待 | Phase |
| :--- | :--- |
| **差分ゼロが目標**（U-3）。出た場合は「これまでサイレント素通ししていたフィルタの有効化」として**1件ずつ理由を説明できること** | Phase 1 |
| **差分ゼロ**（エンジン統合は挙動を変えない） | Phase 3 |

差分が説明できない場合は、その Phase を完了とせず原因を §7 に記録して解決する。

### 6.3 性能計測（Phase 3）

**必ずウォーム（同一条件で2回目以降）で計測する**（P0-3。コールド初回は 20〜40 倍のばらつきが出る）。

| 対象 | ベースライン（2026-08-10 実測） | 受入基準（U-4） | 実測 |
| :--- | ---: | ---: | ---: |
| `GET /api/screener`（ウォーム） | 0.25〜0.34 秒 | **0.51 秒以内** | — |
| `GET /api/screener`＋特殊フィルタ（ウォーム） | 0.31〜0.34 秒 | **0.51 秒以内** | — |
| `GET /api/screener/dashboard` 最新日（ウォーム） | 0.55 秒 | **0.83 秒以内** | — |
| `GET /api/screener/dashboard` 過去日（ウォーム） | 0.95〜1.06 秒 | **1.59 秒以内** | — |
| Parquet プリロード（43営業日分） | 1.84 秒 / 347 MB | 退行なし | — |

### 6.4 fail-loud / パリティの実効性検証

過去の実障害を**フィクスチャとして再現**し、新しい仕組みが実際に検知することを確認する。
「テストを書いたが実は検知できない」を避けるため、**当時のコードでレッドになることまで確認する**。

| 再現する障害 | 検知するはずの仕組み | Phase |
| :--- | :--- | :--- |
| `avg_dollar_volume_21` の `_x/_y` サフィックス衝突 | 必要カラム欠落 → `ValueError` | 1 |
| `is_rs_blue_dot` の alias 不一致 | 未知キーまたはカラム欠落 → `ValueError` | 1 |
| `min_rs_ratio_rank_e63` の単独使用 | ランク列がレジストリから自動供給される | 1 |
| タイポしたキー（例: `min_vol_surge_2`） | 未知キー → `ValueError` | 1 |
| **`category='テーマ'` の買い候補混入（片側だけ修正した状態）** | **経路間パリティ → 不一致で失敗** | 2 |
| 流動性床が片側にしか無い状態 | 経路間パリティ → 不一致で失敗 | 2 |

### 6.5 API スモーク（Phase 1 / Phase 3）

```powershell
$env:STOCKTOOL_ENV="sandbox"
# API 起動後、/screener・/screener/dashboard・/screener/meta・/screener/presets が 200 を返すこと
# フロント左上にオレンジの Sandbox バッジが出ていることを目視確認
# B6「RS MACD and Theme」プリセットに仮想合成指数（_XXX_ 形式）が出ていないことを確認（2026-08-05 の再発防止）
```

---

## 7. 途中発生した課題

### Phase 0（2026-08-10）で判明したこと

| ID | 事象 | 影響 / 対応 |
| :--- | :--- | :--- |
| **P0-1** | **Parquet の `ranks` は SQLite と同じ wide 形式（26列）だった**。にもかかわらず `backtest_runner.preload_data`（L124-140）が毎回 wide→long に melt し、`backtest_screener` / `scenario_runner` が日次で long→wide に戻している | 仕様書（`architecture.md` §11.1「スキーマは Parquet と SQLite で完全に同一」）は**正しい**。melt は純粋な往復の無駄であり、long 形式を前提とした処理（`indicator_name` / `percent_rank` の絞り込み）は**すべて Phase 3 で削除できる**。計画の前提が強まった |
| **P0-2** | **melt の `available_vars` が6つのランク列を落としている**: `rs_value_rank` と `rs_roc_ema_rank_e5/e14/e21/e63/e200`。Parquet/SQLite の双方に存在するのにバックテスト側からは**恒久的に見えない** | F1（サイレント素通し）の未報告の実例。API スクリーナーからは使えるがバックテストからは使えない、という**経路間の能力差**。issue_list P1「未採用インジケーターの区分C（backtest の merged にランク未配管）」と同一事象。Phase 1 のレジストリで `requires` から自動供給されるようにすれば構造的に解消する |
| **P0-3** | dashboard の過去日アクセスが初回 20〜27 秒 | OS ページキャッシュのコールドが原因と切り分け済み（同一日付の2回目は 0.95〜1.06 秒）。**日付固有の遅さではない**。U-4 の受入基準はウォーム値に対して適用する（§1.5 の注記） |
| **P0-4** | `/screener` は常に**ちょうど200件**を返す（`results[:200]` のスライス上限に飽和） | スナップショットは**201位以下の変化を検出できない**という限界がある。Phase 1 の差分実測ではこの点を明示し、必要なら順位を落とした比較（母集団全体のハッシュ等）を追加する |
| **P0-5** | `get_screener()` は素の関数として呼ぶと FastAPI の `Query(False)` 既定値が `Query` オブジェクトのまま渡り、**全特殊フィルタが truthy と評価されて有効化**される（`TypeError: '>=' not supported between 'float' and 'Query'` で発覚） | Phase 2 のパリティテストで API 経路を関数直呼びする際の**落とし穴**。テストでは全パラメータを明示的に渡すこと。`tmp/phase0_snapshot.py` に注記済み |
| **P0-6** | 2026-08-11 時点で、API プリセット `rrg_improving_in` は2件・バックテスト戦略 `C2_rrg_improving_in` は0件 | 両者は別ファイル（`screener_presets.toml` / `backtest_config.toml`）で閾値が異なる可能性が高く、**現時点では不一致と断定できない**。Phase 2 で同一パラメータを与えたときに一致するかを検証する対象として記録しておく |

### Phase 1（2026-08-10）で判明したこと

| ID | 事象 | 影響 / 対応 |
| :--- | :--- | :--- |
| **P0-7** | **`METADATA_KEYS` の3箇所の和集合を取ったところ、`max_avg_hits_per_day` が `backtest_runner.py::validate_strategies_config` の `METADATA_KEYS` に非対称に欠落していた**（`min_avg_hits_per_day` はあるのに対になる `max_` が無い） | `backtest_config.toml` で実際に使われている制御キーなので、バリデータが「未知パラメータ」警告を出していたはず。レジストリ側の `METADATA_KEYS` で補完済み。**3箇所を統合しなければ気づけなかった類の非対称**で、集約の効果がさっそく1件出た |
| **P0-8** | **`is_close_gt_*` の短縮名正規化が、既存3箇所のうち `screener_router._apply_filter`（L313-324）だけ無条件で、他2箇所は「既知の12短縮名のときだけ」というガード付きだった** | `_apply_filter` は `is_close_gt_ema_63` のような**正準形も受け付けていた**。短縮名だけをレジストリに登録すると、呼び出し側を切り替えた時点でこのキーが `UnknownFilterKeyError` になる（＝Phase 1 の「差分ゼロ」目標を崩す潜在的な後退）。現行 TOML に正準形の使用は無いが、**短縮名と正準名の両方を登録して塞いだ**（`_build_close_gt_specs`。close_gt の登録数 24→48） |

### 🔴 Phase 1「データ供給側」の差分実測（2026-08-10）で判明した重大事項

差分実測で **25件の差分**が出た（U-3 の「差分ゼロ」目標に反する）。原因を2つに切り分け、
1つは修正、もう1つは**既存バグの是正**と判明した。

#### P1-6: `sort_column` の必要カラム漏れ（設計の穴・修正済み）

| 項目 | 内容 |
| :--- | :--- |
| **事象** | `max_hits_per_day` に達する戦略（C1 / C2 / G2 / G3）で、上位10件の顔ぶれが総入れ替えになった |
| **原因** | `sort_column` は `METADATA_KEYS` に含まれるため `resolve_required_columns` が拾わず、**並べ替えキー（`rs21_rank`）が merge されなくなった**。列が無いと既存実装は `sort_values` をスキップして `head(max_hits)` にフォールバックするため、**順序がデータ順のまま Top-N が切られた**。旧 `needs_rs*` の or 連鎖は `sort_col in (...)` を明示的にトリガーに含めていた |
| **なぜ一部の戦略だけか** | フィルタで `rs_ratio_rank_e21` を要求する戦略（A/B系/G1/D/F）は副作用で merge されていたため無傷。要求しない戦略だけが露出した。さらに**検出件数が `max_hits_per_day` に達していない日は並べ替えが結果に影響しない**ため、日付によっても出方が違った |
| **対応** | `resolve_required_columns()` に `extra_columns` 引数を追加（「フィルタ以外の理由で必要な列」）。`apply_filters_to_df` と `scenario_runner` が解決済みの `sort_column` を渡す。**修正後、C1/C2/G2/G3 の差分は完全に消滅**（25件 → 18件） |
| **教訓** | 「必要カラム」は**フィルタキーだけから導出できない**。並べ替え・表示・スコアリングなど、フィルタ以外の理由で必要な列がある。§3.1.4 の設計はこれを見落としていた |

#### P1-7: 🔴 `is_trend_template` がバックテストで**完全に無効**だった（既存バグ・是正）

**残る18件の差分は全て、この1つの既存バグの是正によるもの。**

| 項目 | 内容 |
| :--- | :--- |
| **事象** | `is_trend_template = true` を持つ4戦略（**D / E1 / E2 / F**）で検出銘柄が変化・減少 |
| **原因** | 旧 `apply_filters_to_df` の汎用ループが `is_` 接頭辞を機械的に剥がして `trend_template` という列名を導出していたが、**DB の実列名は `is_trend_template`**。`alias_map` に変換エントリが無く、存在しない列を参照して**何もフィルタされていなかった**（サイレント素通し） |
| **既知バグとの関係** | **2026-07-22 に修正された `is_rs_blue_dot` の alias 不一致と完全に同型**。あのとき `rs_blue_dot` / `rs_red_dot` の2件は `alias_map` に追加されたが、**`trend_template` は見落とされたまま残っていた** |
| **実証** | 変更前のコード（本体チェックアウト）で E1 を `is_trend_template` 有り／無しで実行し、**結果が完全に同一（6件・同じ銘柄）**であることを確認。変更後は4件になり、除外された `TECH` / `UTZ` は実際に `is_trend_template=0` であることを DB で確認 |
| **影響規模** | 基準日 2026-08-11 で個別銘柄 2,876 件のうち `is_trend_template=1` は **711 件（24.7%）**。フィルタが無効だった間、**D/E1/E2/F の母集団は意図の約4倍**に膨らんでいた |
| **API 側は正常だった** | `_apply_filter` は `_INDICATOR_COLUMNS['is_trend_template']` を直接引くため**正しく適用されていた**。つまり**画面とバックテストで別の銘柄集合を見ていた**（F2/F3 の典型。Phase 2 のパリティテストがあれば即座に検出できた） |
| **帰結** | **D / E1 / E2 / F の過去の最適化 study・シナリオテスト結果は、トレンドテンプレートが効いていない状態での評価**。再最適化の要否はユーザー判断（U-3） |

#### P1-8: 🔴 流動性床が `/screener/dashboard` に適用されていない（既存バグ・F2 の3件目）

| 項目 | 内容 |
| :--- | :--- |
| **事象** | 「全戦略共通のハード制約」である `min_avg_dollar_volume_21`（$2M/日）が、`get_screener()`（`/screener`）には適用されているが、**`get_screener_dashboard()`（`/screener/dashboard`）には適用されていない** |
| **実測（2026-08-11）** | dashboard の表示銘柄 82件のうち **10件（12%）が床を下回る**。最悪は `ANPA` の **$108,939/日**（床の18分の1）。他に `ULBI` $245k / `HQI` $254k / `OIO` $279k / `EVGN` $374k など |
| **なぜ見逃されたか** | 2026-07-27 の対策時に「生スクリーナー API」として `get_screener()` にだけ追加され、**同じルーターにあるもう1つのエンドポイントが漏れた**。issue_list の対策記録も `get_screener()` にしか言及していない |
| **深刻度** | dashboard は**日常的に見る画面**であり、`/screener` より露出が大きい。E1/E2 の `BETR`（実測 $2,758/日）でバックテストの7割が汚染されていた事例と同じ性質の銘柄が、画面に出続けていた |
| **対応** | Phase 1 の「流動性床の注入を1箇所に集約」で**両エンドポイントに適用されるようにする**。これは意図的な挙動変更であり、**dashboard の表示銘柄が減る**（差分ゼロにはならない）。ユーザーへ報告する |

### Phase 2（2026-08-11）— パリティテストが初回実行で本番バグを検出した

#### P2-1: 🔴 `close_gt` の短縮名正規化が正準形の入力を壊していた（既存バグ・修正済み）

**Phase 2 のパリティテストが、導入初日に自力で見つけた最初のバグ。**

| 項目 | 内容 |
| :--- | :--- |
| **事象** | `screener_router._apply_filter` の短縮名正規化が、**既に正準形の入力 `ema_150` / `sma_150` を `ema_1_50` / `sma_1_50` という実在しない列名に壊していた**。`_resolve_column()` が None を返し、**フィルタがサイレントに素通し**される（F1） |
| **原因** | `for num in ('200','150','63','50','21','5')` のループで、`'150'` の判定は「既に `_150` なので書き換え不要」として通過するが、**ループが継続して `'50'` の判定に到達**する。`'ema_150'.endswith('50')` は True（150 の末尾が 50）かつ `endswith('_50')` は False なので条件成立し、`replace('50','_50')` が発火する。**影響を受けるのは `_150` だけ**（リスト中で他メンバーを接尾に含む唯一の数値のため） |
| **私（オーケストレーター）の判断ミス** | P0-8 で正準形キー（`is_close_gt_ema_63` 等）を `EXPLICIT_SPECS` に登録した際、「`_apply_filter` は無条件に正規化するので正準形も受け付ける」と判断した。**`ema_63` では検証したが `ema_150` を検証しなかった**。結果、レジストリは「有効なキー」と宣言する一方 API は黙って無視する、という F1 を新たに作りかけていた |
| **なぜ既存テストで見つからなかったか** | 現行の TOML はどれも短縮形（`ema63`）しか使っておらず、正準形は未使用。**実データ由来のテストでは永久に踏まない**。パリティテストが「レジストリに載っている全キー」を機械的に列挙したからこそ露見した |
| **対応** | 正規化を `screener_registry.normalize_close_gt_target()`（ガード付き）に**一本化**し、`screener_router` と `backtest_screener` の複製を削除。3箇所 → 1箇所。ガードが必須である理由を関数の docstring に明記した |
| **本番影響** | **無し**（差分実測でゼロを確認）。正準形が TOML で使われた瞬間に顕在化する潜在バグだった |

> **この1件が Phase 2 の価値を最もよく示している。**
> レジストリ（Phase 1）は「キーが宣言されているか」しか見ない。fail-loud も「解決できるか」しか見ない。
> **「両経路が同じ答えを出すか」は、実際に両方を走らせて突き合わせないと分からない。**
> しかも実データ由来のテストでは踏まないケースだったため、
> **レジストリ駆動で全キーを機械的に列挙する**という設計（§3.2.1 (c)）が効いた。

#### P2-2: 🔴 マージ後、最適化実行で90件の誤検知（**本計画が持ち込んだ回帰**・修正済み）

| 項目 | 内容 |
| :--- | :--- |
| **事象** | main へのマージ後、ユーザーが最適化を実行したところ `WARNING: Invalid or unsupported strategy parameters detected in config!` が **90件**出力された（`min_market_cap` / `min_vol_surge_21` など**ごく基本的なキーが全て「未知」**扱い） |
| **原因** | `optimization_runner.py:631` は `validate_strategies_config(strategies, pd.DataFrame(), pd.DataFrame())` と **列を持たない DataFrame** を渡す。旧実装はカラム集合を `api.screener_router._INDICATOR_COLUMNS` から得ていたため DataFrame に依存しなかったが、**逆依存を削除した際に「渡された DataFrame の列」だけに依存させてしまった** |
| **影響** | `run_optimization` 系のみ。**警告出力だけで停止はせず、結果は正しい**（`run_backtest` は正しい DataFrame を渡すため無影響）。ただし検証が事実上無効化されており、本物のタイポを見逃す状態だった |
| **なぜテストで見つからなかったか** | 私が追加した `..._current_backtest_config_has_zero_errors` は**実カラムを持つ DataFrame** を渡していた。**実際の呼び出し側は空の DataFrame を渡す**という現実を再現していなかった（P1-2 / P0-2 と同じ「フィクスチャが現実より恵まれている」パターン。本計画で**3度目**） |
| **対応** | カラム集合の権威を**モデル定義（`Indicator` / `DailyPrice`）**に変更し、引数の DataFrame は補助（派生列の追加）とした。この関数の責務は**スキーマ検証**（キー名が妥当か）であり、「実際に供給されているか」の検証は適用時の `MissingFilterColumnError` の責務なので、この分担が正しい。回帰テストを**実際の呼び出し方（空 DataFrame）で**追加 |

> **教訓（本計画で3度目）**: テストのフィクスチャが現実の呼び出し側より恵まれていると、
> テストが緑でも本番で壊れる。**「実際の呼び出し側と同じ渡し方」でのテストを1本入れる**こと。

### Phase 1「検証系2箇所」（2026-08-10）で判明したこと

| ID | 事象 | 影響 / 対応 |
| :--- | :--- | :--- |
| **P1-1** | **随伴パラメータの取りこぼしによるハードな後退（検収で発見・修正済み）**。旧 `validate_strategies_config` のローカル定数 `FILTER_ATTACHED_PARAM_KEYS`（`is_vcp_breakout` の閾値8種）がレジストリ化の際に消え、代替が無かった | `pivot_tol` 等が「未知のキー」と判定され、**fail-loud により `is_vcp_breakout` を使う戦略でバックテスト全体が `ValueError` で停止する**。検収で実際に8件の誤検知として再現。現在どの戦略も `is_vcp_breakout` を使っていないため既存テストはすり抜けた（issue_list で「温存」と明記されているフィルタなので、いずれ必ず踏む）。**対応**: レジストリに `ATTACHED_PARAM_KEYS`（全 `spec.params` の和集合）と述語 `is_non_filter_key()` を追加し、全呼び出し側がこれ1つを参照する形にした。回帰テスト2件を追加（VCP 8種 / `rrg_intensity_threshold`）。**教訓: 「除外集合」は「フィルタ集合」と同じくらい重要で、集約時に落ちやすい** |
| **P1-2** | **`_build_preset_query` の `except Exception` が、失敗したプリセットを黙ってレスポンスから落としていた**（＝それ自体がサイレント失敗）。fail-loud 化の過程で、テストフィクスチャの列が全て `None` のとき特殊フィルタが `TypeError: float > None` を出し、それが握り潰されていたことが判明 | **本番データでは現在発生していない**ことを確認（基準日 2026-08-11 のクロスセクション 3,148行で object dtype 列はゼロ。NULL は最大2件で pandas が float64 に推論するため）。ただし**列が丸ごと NULL になると object dtype になり TypeError を起こす**。これは「T3 に新しい指標カラムを追加したがパイプライン未実行」の状態で実際に起きうる（`avg_dollar_volume_21` 追加時が該当）。**対応**: カテゴリを残したまま `items=[]` ＋ `error` を返すよう変更し、少なくとも**見えるようになった**。dtype 脆弱性そのものは未対処で、Phase 3 の `assert_frame_contract()` で数値 dtype を強制するのが本筋 |

### Phase 1「データ供給側3モジュール」（2026-08-13）で判明したこと

| ID | 事象 | 影響 / 対応 |
| :--- | :--- | :--- |
| **P1-3** | `screener_cross_section._IND_COLS` の導出値と旧ハードコードを突合したところ、`is_vcp_breakout` の当日 `requires` に `vcr` が無かった（`prev_requires` にのみ登録されていた）ため、導出結果が旧ハードコード（`vcr` を当日分も取得していた）を1列だけ包含できなかった | `filter_vcp_breakout` 自体は `prev_vcr` しか参照しないため機能的には無害だったが、§3.1.4 (e) の「不足があれば宣言漏れとしてレジストリ側を直す」方針に従い `requires` に `vcr` を追加登録。導出後の `_IND_COLS`/`_PREV_COLS` が旧ハードコードと完全一致することを確認した |
| **P1-4** | `apply_filters_to_df` に `MissingFilterColumnError` の fail-loud 検査を追加したところ、既存テスト3件が新規レッドになった。(a) `is_theme_rs_ratio_e21_gt_e63`/`_e14_gt_e21`（生値のテーマ比較）は `merged` ではなく当日の `df_ind` スライスを直接参照する既存 dispatch であり、`merged` に無くて当然の列を検査対象にしていた。(b) `prev_date=None`（バックテスト初日相当）のとき、RRG系フィルタの `prev_requires` を無条件に検査しており、既存の「前日データが無ければ no-op で通過」という graceful degradation の仕様と衝突していた | (a) は当該2キーの `requires` を `merged` 存在チェックから除外。(b) は `prev_date is not None` のときだけ `required.prev` を検査する形に変更（`is_vcp_breakout` の deny-by-default 除外は `prev_date` の有無に関わらず維持）。いずれも「設計判断」ではなく実装側の検査スコープの誤りとして即時修正し、回帰テスト4件を追加した |
| **P1-5** | `scenario_runner.py` の必要ランク列の和集合計算・`apply_filters_to_df` の新規レジストリ呼び出しを、既存の `test_scenario_runner_integration`（デフォルトの本番 `data/screener_presets.toml` を使う統合テスト）が使う最小フィクスチャに対して実行すると、`min_vol_surge_21` 等の実使用キーが要求する列（`vol_surge_21`/`adr_pct_21`/`vol_surge_rel_spy_21`）がフィクスチャに無く `UnknownFilterKeyError` になった | 本番の T3 は全列を埋めるため実害は無い。フィクスチャを実際にスキャンされる2戦略（`active_rise_ids` かつ `group='Check'` の `rrg_improving_in`/`check_1d_gain`）が要求する列を補う形で修正（P1-2 と同型のテストフィクスチャ不足） |

---

## 8. スコープ外・残作業

| 項目 | 理由 / 引き継ぎ先 |
| :--- | :--- |
| 出口ルール（§6.4）の共通化 | 2026-07-20〜21 に `evaluate_position_exit_for_day()` へ一本化済み。本計画は**エントリー側のスクリーン条件のみ**を扱う |
| ETFシナリオテスト（型2） | スクリーンフィルタを使わないため対象外（§2.2） |
| `scenario_scorer.py:9` の `target_group_prefix` 既定値がリテラルのまま | 呼び出し側が定数を渡し、一致をテストで固定済みのため実害なし。Phase 1 で近くを触るならついでに解消してよいが必須ではない |
| 複数戦略で共有資本を奪い合うメカニズムが未使用（issue_list P1、2026-08-02 再整理） | 「同じ条件が同じ銘柄を抽出するか」ではなく「抽出後の資本配分」の問題。本計画の対象外 |
| 未採用インジケーターの検証（issue_list P1、区分B の5項目） | フィルタ関数は実装済みで戦略 TOML が採用していないだけ。レジストリ化で**採用可能なキーの一覧が明確になる**副次効果はあるが、検証自体は別タスク |
| `backend_specification.md` §6.7 の陳腐化 | Phase 3 のドキュメント更新に含めた（`avg_dollar_volume_21` 未記載・`relative_ranks` の long 形式前提） |
| フロントエンドでの `applied_filters` 表示 | Phase 1 では API が返すところまで。UI への露出は必要になった時点で別途 |

### 2026-08-10 の再調査で解決済みと確認し、本計画から外した項目

| 項目 | 状況 |
| :--- | :--- |
| 個別銘柄シナリオテストの戦略ディスパッチがサイレント除外（issue_list、2026-07-20 発見） | **2026-08-04 修正済み**。`report_strategy_scan_coverage()` で報告＋全滅時 `ValueError`。→ §1.3 の先行事例として本計画が参照する |
| `run_scenario_batch.py` のポートフォリオ設定ハードコード（2026-07-20 発見） | **2026-08-04 修正済み**。`[job.portfolio]` で上書き可能、未知キーは `ValueError`。→ 同上 |
| `/ranking` エンドポイントのデッドコード | **2026-08-09 削除済み** |
