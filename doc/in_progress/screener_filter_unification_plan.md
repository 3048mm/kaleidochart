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

**何を**: `indicators/screener_filters.py` に全フィルタキーの宣言的定義を追加する。

```python
@dataclass(frozen=True)
class FilterSpec:
    kind: str                      # "numeric" | "theme_numeric" | "rank" | "theme_rank"
                                   # | "close_gt" | "special"
    requires: tuple[str, ...]      # 当日に必要なカラム（正準名）
    prev_requires: tuple[str, ...] = ()   # 前日に prev_ 付きで必要なカラム
    fn: Callable | None = None     # kind="special" のときの評価関数
    params: tuple[str, ...] = ()   # 随伴する数値パラメータキー
    op: str | None = None          # ">=" | "<=" | "=="

FILTER_SPECS: dict[str, FilterSpec] = { ... }
```

- 数値系は `min_/max_` × カラム名の機械展開で生成（Indicator モデルのカラム走査＋仮想カラム定義）
- 特殊系は既存の `SPECIAL_FILTER_KEYS` を `FILTER_SPECS` へ移行（後方互換のため `SPECIAL_FILTER_KEYS`
  は `FILTER_SPECS` からの導出プロパティとして残す）

**なぜ**: F1（サイレント素通し）と F4（データ供給の差）を構造的に消すため。
「必要カラム」がフィルタ自身の宣言になれば、`needs_rs14/21/63` の or 連鎖・`_IND_COLS`・
`prev_merge_cols`・`price_cols` といった手書きリストの同期漏れが原理的に発生しない。

**対象**:
- 新規: `backend/indicators/screener_filters.py`（`FilterSpec` / `FILTER_SPECS` / `resolve_required_columns()`）
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
- [ ] `FilterSpec` / `FILTER_SPECS` / `resolve_required_columns()` の実装
- [ ] **[TDD-red]** fail-loud のテスト（未知キー・必要カラム欠落で `ValueError`。
      現状の「黙って通す」挙動でレッドになることを確認）
- [ ] `backtest_screener.py` の `needs_rs*` or 連鎖・deny-by-default 分岐・`prev_merge_cols` を
      レジストリ導出へ置換
- [ ] `screener_cross_section.py` の `_IND_COLS` / `_PREV_COLS` をレジストリ導出へ置換
- [ ] `scenario_runner.py` のランク事前マージをレジストリ導出へ置換（`has_all_premerged` 分岐の解消）
- [ ] `backtest_runner.py::validate_strategies_config` をレジストリ参照へ置換し、
      `api.screener_router` への逆依存と `except ImportError` の握り潰しを削除
- [ ] `screener_router.py::_build_preset_query` の `is_known` 判定をレジストリ参照へ置換
- [ ] 流動性床（`min_avg_dollar_volume_21`）の注入を単一の純関数へ集約（4モジュール5箇所 → 1箇所）
- [ ] テーマ行の最終出力除外を単一定義へ集約（2モジュール3箇所 → 1箇所）
- [ ] `applied_filters` の記録・出力（API レスポンス／バックテスト結果 JSON／ログ）
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

**現在地（2026-08-10）**: Phase 0 完了。次は **Phase 1 の TDD-red**（`FILTER_SPECS` の単体テストと
fail-loud テストを先に書き、現状の「黙って通す」挙動でレッドになることを確認する）から着手する。

- ベースラインの再取得コマンド: `$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe tmp/phase0_snapshot.py`
  （読み取り専用。Phase 1 完了後に再実行して `tmp/phase0_baseline_20260813_121011.json` と突合する）
- Phase 1 はワークツリーを切って作業する（本体チェックアウトではコミット禁止）
- 注意点は §7 の P0-1〜P0-6 にまとめてある。とくに **P0-5（`Query` 既定値の罠）** は
  Phase 2 のパリティテスト設計時に必ず参照すること

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
