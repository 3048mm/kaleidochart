# スクリーンパラメータ一元化（フィルタ仕様レジストリ → パリティ検証 → 単一エンジン）計画書

- **ステータス**: 🚧 計画レビュー中
- **実施者**: Claude Code（`claude-opus-5`、オーケストレーター）／実装は implementer・test-writer へ委譲
- **開始日**: 2026-07-29 / **完了日**: —
- **作業ブランチ**: 未定（Phase ごとにワークツリーを切る想定）
- **対象 issue / 関連ドキュメント**:
  - `doc/issue_list.md` P3「スクリーナーの完全 DataFrame 化（D-2 の最終形）」（本計画の Phase 3 に相当）
  - `doc/issue_list.md` P0「流動性フィルタが片側にしか存在しなかった」／同 P1 の各サイレント失敗事例
  - `doc/completed/screener_refactoring_d1_d2.md`（D-2 = 特殊フィルタ共通化。本計画はその続き）
  - `doc/backend_specification.md` §5・§6.1・§6.7

---

## 1. 背景と目的

### 1.1 現状（2026-07-29 時点の実測）

スクリーン条件の評価経路は3つあるが、実態は **「2エンジン × 3データ供給」**である。

| 経路 | フィルタエンジン | データ供給 |
| :--- | :--- | :--- |
| ① フロントのスクリーナー | **SQLAlchemy**（`api/screener_router.py::_apply_filter`）＋ 特殊フィルタのみ pandas | SQLite 1日分 |
| ② パラメータ最適化バックテスト | **pandas**（`backtest/backtest_screener.py::apply_filters_to_df`） | Parquet キャッシュ |
| ③ 個別銘柄シナリオテスト | **pandas（②と同一関数）** | Parquet キャッシュ（前処理は `scenario_runner.py` 独自） |

D-2（2026-07-04）で特殊ブールフィルタ14種は `indicators/screener_filters.py` に一本化済み。
**残っている二重実装は数値 `min_/max_` 系・テーマ系・`is_close_gt_*`・`expression`・カラム供給・
共通制約の注入**であり、フィルタキーの解釈ロジックは以下 **4箇所**に独立して存在する。

| # | 場所 | 役割 | 行数 |
| :--- | :--- | :--- | ---: |
| 1 | `api/screener_router.py::_apply_filter`（L204-351） | 適用（SQL） | 148 |
| 2 | `api/screener_router.py::_build_preset_query` の `is_known` 判定（L639-676） | 検証 | 38 |
| 3 | `backtest/backtest_screener.py::apply_filters_to_df`（L139-585） | 適用（pandas） | 447 |
| 4 | `backtest/backtest_runner.py::validate_strategies_config`（L515-645） | 検証 | 131 |

「フィルタが必要とするカラム」の宣言も **4箇所**に手書きで分散している。

| # | 場所 | 形態 |
| :--- | :--- | :--- |
| 1 | `api/screener_cross_section.py` の `_IND_COLS` / `_PREV_COLS` / `_RANK_COL_MAP` | 定数リスト |
| 2 | `backtest_screener.py` の `needs_rs14/21/63`（L182-224）＋ deny-by-default の重複 or 連鎖（L299-321）＋ `prev_merge_cols`（L341） | or 連鎖 |
| 3 | `scenario_runner.py` のランク事前マージ（L455-467） | ループのコピー |
| 4 | `backtest_runner.py::preload_data` のキャッシュ列リスト（`backend_specification.md` §6.7） | 定数リスト |

全戦略共通のハード制約（`min_avg_dollar_volume_21`）の注入も **4モジュール5箇所**（`screener_router.py:76`、
`backtest_runner.py:479`、`scenario_runner.py:86`、`optimization_runner.py:363,443`）に独立実装されている。

加えて `backtest_runner.py:525` が `api.screener_router` から `_INDICATOR_COLUMNS` を import しており、
**バックテスト → API という逆向きの依存**が発生している（さらに `except ImportError` で握り潰されるため、
import が壊れると検証が静かに劣化する）。

### 1.2 過去の障害の分類（issue_list からの実証）

| 型 | 内容 | 実例 |
| :--- | :--- | :--- |
| **F1: サイレント素通し**（列が無い／キー未知 → 何もせず全通過） | **最多** | `avg_dollar_volume_21` の `_x/_y` サフィックス衝突（P0 L45）、`is_rs_blue_dot` の alias 不一致（P1 L124）、`min_rs_ratio_rank_e14/e63` が `needs_rs*` 未登録（P1 L162）、expression の true/false 非対応（D-2 I-6）、`group != "Check"` の preset 除外（P2 L206） |
| **F2: 片側のみ実装** | 本課題の出発点 | 流動性床（P0 L12-17）、出口ルールの二重実装（P1 L105） |
| **F3: 意味論の差** | 少数 | S-1（`min_market_cap` のテーマ扱い）、S-2（`is_rs_ratio_rank_e21_gt_e63` のテーマ扱い） |
| **F4: データ供給の差**（同じ関数でも渡す DataFrame が違う） | F1 と結合して顕在化 | `has_all_premerged` 誤判定でシナリオテストが常時0件（`backtest_screener.py` L290-322） |

**結論**: 主因は「共通化されていないこと」ではなく **「宣言されていない／供給されていないものを黙って無視すること」**（F1）である。
したがって共通化とパリティテストだけでは不足で、`fail-loud` 化が対策の中核になる。

- パリティテスト単体では「両側とも未実装」（F2 の初期状態）は**両方 0 件で一致して素通り**する。
  実際 `min_avg_dollar_volume_21` は「最適化バックテストにしか無い」状態で ①③ 間のパリティは成立していた。
- `fail-loud` 単体では「両側に実装があるが意味が違う」（F3）は検知できない。
- よって **レジストリ + fail-loud + パリティ** の3層が必要。

### 1.3 完了時の成功条件

1. フィルタキーの**唯一の定義場所**が `indicators/screener_filters.py` の `FILTER_SPECS` になり、
   ①②③ と TOML バリデータ・`/screener/meta` がすべてそれを参照する。
2. 未知のキー・必要カラムの欠落が**必ず失敗として顕在化**する（黙って通過しない）。
3. 全フィルタキーについて ①（SQLite 経路）と ②（Parquet 経路）の抽出銘柄集合が一致することが
   **レジストリ駆動のテストで自動的に検証**される（新キーはフィクスチャを書かないとテストが落ちる）。
4. スクリーン結果に「実際に適用されたフィルタ一覧」が残る。

### 1.4 ベースライン（着手前の実測値）

| 指標 | 値 | 計測方法 |
| :--- | ---: | :--- |
| キー解釈ロジックの実装箇所 | 4 | 上表 §1.1 |
| 必要カラム宣言の実装箇所 | 4 | 上表 §1.1 |
| 共通ハード制約の注入箇所 | 5（4モジュール） | `grep min_avg_dollar_volume_21` |
| `GET /api/screener` 応答時間（ウォーム） | 約 0.3 秒 | issue_list 完了済み L262。**Phase 3 の受入基準に使う（要再計測）** |
| `GET /api/screener/dashboard` 応答時間（ウォーム） | 約 0.9 秒 | D-2 Phase 2 の実測。**要再計測** |
| pytest 全体 | 466 件 | issue_list P0 L48 時点。**着手時に再計測** |

---

## 2. スコープと設計判断

### 2.1 変更すること

- フィルタキーの宣言的レジストリ `FILTER_SPECS` の新設と、①②③・バリデータ・`/screener/meta` の参照統一
- 未知キー・必要カラム欠落の fail-loud 化
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
| ②と③ のエンジン統合 | **不要（既に同一関数を共有）** | 分散していたのは前処理とハード制約の注入。そちらを Phase 1 のレジストリで解消する |
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
- 改修: `backend/api/screener_router.py`（`_build_preset_query` の `is_known` 判定をレジストリ参照へ）
- 改修: `backend/api/screener_cross_section.py`（`_IND_COLS` / `_PREV_COLS` をレジストリから導出）
- 改修: `backend/backtest/backtest_screener.py`（`needs_rs*` or 連鎖・`prev_merge_cols` をレジストリ導出へ置換）
- 改修: `backend/backtest/backtest_runner.py`（`validate_strategies_config` をレジストリ参照へ。
  `api.screener_router` への逆依存を削除）
- 改修: `backend/backtest/scenario_runner.py`（ランク事前マージをレジストリ導出へ）
- 新規: 共通ハード制約の注入を単一の純関数へ集約（4モジュール5箇所 → 1箇所）

**fail-loud の適用レベル**（§4 でユーザー確認）:

| 経路 | 未知キー | 必要カラム欠落 |
| :--- | :--- | :--- |
| バックテスト／最適化／シナリオテスト（CLI） | **例外送出**（現在は warning 出力のみで実行継続） | **例外送出** |
| スクリーナー API | 起動時のプリセット検証で **ERROR ログ＋該当プリセットを明示的に「エラー」として返す** | 同左 |

**`applied_filters` の記録**:
- API: レスポンスに `applied_filters: list[str]` を追加（`/screener`・`/screener/dashboard`）
- バックテスト／シナリオテスト: 結果 JSON と実行ログに適用済みキー一覧を出力

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
- S-1 / S-2 のような意図した差異は、`EXPECTED_DIVERGENCE` として理由付きで明示登録し、
  それ以外の不一致は全て失敗とする
- D-2 Phase 2 で作成済みの同値性テスト14件（`backend/tests/api/` 配下）を土台に拡張する

**なぜ**: F2（片側のみ実装）と F3（意味論の差）を検知するため。

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
- **ランクの long/wide 変換を廃止**（現状 `backtest_runner.py:126-140` で wide→melt→long にし、
  日次で再び wide へ merge し直しており、無駄かつ F1 の温床）
- パリティテストの対象が「エンジンの一致」から「**2つのローダが同一フレームを作るか**」へ縮退する
  （これが SQLite と Parquet の間で本質的に異なる唯一の部分）

**なぜ**: F2・F3 を構造的に消すため。実装箇所が1つしかなければ「片側だけ実装」も「意味論の差」も原理的に生じない。

**対象**:
- 新規: `backend/indicators/screener_frame.py`（契約定義＋`assert_frame_contract()`）
- 改修: `backend/api/screener_router.py`（大改修）、`backend/api/screener_cross_section.py`（ローダへ吸収）
- 改修: `backend/backtest/backtest_screener.py`、`backend/backtest/backtest_runner.py`、`backend/backtest/scenario_runner.py`

**影響範囲**:
- ⚠️ 回帰リスク最大。API のソート・上位200件スライス・sparkline 生成等の周辺処理を作り直す必要がある
- ⚠️ 性能退行の可能性（1日分 ≒ 3,000銘柄 × 数十列。API は既に特殊フィルタ用に同等のクロスセクションを
  構築しているため軽微と見込むが、§1.4 のベースラインに対する受入基準で担保する）

---

## 4. ユーザー確認事項

| # | 論点 | 選択肢 | 推奨 |
| :--- | :--- | :--- | :--- |
| **U-1** | **スクリーナー API の fail-loud レベル** | (a) 不正プリセットは 500 で落とす (b) 該当プリセットのみ「エラー」として返し、他は正常表示（画面は生きる） (c) 現状維持（ログのみ） | **(b)**。画面が丸ごと死ぬのは日常運用に耐えないが、エラーが目に見えないと F1 が再発する |
| **U-2** | **既存 TOML に fail-loud で落ちるキーが見つかった場合の扱い** | (a) その場で正しいキーへ修正 (b) 一旦レジストリに登録して挙動を維持し、別途判断 | **(a)**。ただし修正でスクリーン結果が変わるため、差分を報告してから確定する |
| **U-3** | **⚠️ スクリーン結果が変わった場合の再最適化** | Phase 1 でこれまで無効だったフィルタが有効化されると、過去の study・シナリオテスト結果が非互換になる。再最適化（約12時間 ×戦略数）が必要になる可能性がある | 差分の実測を先に出し、**再実行の要否はユーザー判断**とする |
| **U-4** | **Phase 3 の性能受入基準** | `/screener` 0.3秒・`/screener/dashboard` 0.9秒（§1.4）に対して許容する退行幅 | **1.5倍以内**を提案。超えたら Phase 3 は差し戻し |
| **U-5** | **sandbox 検証の要否** | 本計画は**スキーマ変更を伴わず、DB へは読み取りのみ**（`.claude/skills/sandbox-workflow` の必須対象外）。ただし Phase 1/3 で API を実機確認する際は `STOCKTOOL_ENV=sandbox` を使う | スキーマ変更なしのため**種別 A（データ変更なし）**として扱う。API スモークのみ sandbox で実施 |
| **U-6** | **Phase の区切りでのコミット/レビュー** | 各 Phase 完了時にオーケストレーターが diff 確認＋全テスト実行し、ユーザーへ報告してから次へ進む | 合意済み（CLAUDE.md 委譲ルール） |

---

## 5. 実装順序と進捗チェックリスト

### Phase 0: 着手前の実測（ベースライン確定）

- [ ] `pytest` 全件実行し、件数と所要時間を記録（§1.4 の表を更新）
- [ ] `/screener`・`/screener/dashboard` のウォーム応答時間を実測（§1.4 の表を更新）
- [ ] 現行の全プリセット・全戦略について抽出銘柄集合をスナップショットとして保存
      （`tmp/` 配下。Phase 1 の差分検出のリファレンス）

### Phase 1: レジストリ ＋ fail-loud（案1）

- [ ] **[TDD-red]** `FILTER_SPECS` の単体テスト（全キーの `requires` が実カラムに解決できること、
      キーの重複が無いこと、`SPECIAL_FILTER_KEYS` との整合）
- [ ] `FilterSpec` / `FILTER_SPECS` / `resolve_required_columns()` の実装
- [ ] **[TDD-red]** fail-loud のテスト（未知キー・必要カラム欠落で例外が上がること。
      現状の「黙って通す」挙動でレッドになることを確認）
- [ ] `backtest_screener.py` の `needs_rs*` or 連鎖・deny-by-default 分岐・`prev_merge_cols` を
      レジストリ導出へ置換
- [ ] `screener_cross_section.py` の `_IND_COLS` / `_PREV_COLS` をレジストリ導出へ置換
- [ ] `scenario_runner.py` のランク事前マージをレジストリ導出へ置換（`has_all_premerged` 分岐の解消）
- [ ] `backtest_runner.py::validate_strategies_config` をレジストリ参照へ置換し、
      `api.screener_router` への逆依存と `except ImportError` の握り潰しを削除
- [ ] `screener_router.py::_build_preset_query` の `is_known` 判定をレジストリ参照へ置換
- [ ] 共通ハード制約（`min_avg_dollar_volume_21`）の注入を単一の純関数へ集約（4モジュール5箇所 → 1箇所）
- [ ] `applied_filters` の記録・出力（API レスポンス／バックテスト結果 JSON／ログ）
- [ ] **差分実測**: Phase 0 のスナップショットと突合し、抽出銘柄が変化したプリセット・戦略を全件列挙
- [ ] 全テスト実行 → **ユーザーへ差分報告し、U-2/U-3 の判断を仰ぐ**
- [ ] 仕様書更新（`backend_specification.md` §5 にレジストリと fail-loud を明記）

### Phase 2: 経路間パリティテスト（案2）

- [ ] **[TDD]** `FILTER_SPECS` 全キーを列挙するパラメタライズドテストの骨組み
      （フィクスチャ未整備のキーは失敗する形にする）
- [ ] 各キーの境界値フィクスチャ（SQLite シード ＋ 同値の DataFrame）を整備
- [ ] `EXPECTED_DIVERGENCE`（S-1 / S-2 等、意図した差異）を理由付きで登録
- [ ] D-2 Phase 2 の既存同値性テスト14件を新テストへ統合（重複を解消）
- [ ] CI 相当（`pytest` 全件）での実行時間を確認し、遅すぎる場合はマーカーで分離
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
- [ ] 仕様書更新（`architecture.md` §2/§3、`backend_specification.md` §5・§6.7）、`issue_list.md` P3 をクローズ

### 作業中メモ

（未着手。Phase 0 から開始する）

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
| 差分は「これまでサイレント素通ししていたフィルタの有効化」のみ。**理由を1件ずつ説明できること** | Phase 1 |
| **差分ゼロ**（エンジン統合は挙動を変えない） | Phase 3 |

差分が説明できない場合は、その Phase を完了とせず原因を §7 に記録して解決する。

### 6.3 性能計測（Phase 3）

| 対象 | ベースライン | 受入基準（U-4） | 実測 |
| :--- | ---: | ---: | ---: |
| `GET /api/screener`（ウォーム） | 要 Phase 0 計測 | ベースライン × 1.5 以内 | — |
| `GET /api/screener/dashboard`（ウォーム） | 要 Phase 0 計測 | ベースライン × 1.5 以内 | — |
| バックテスト1戦略の実行時間 | 要 Phase 0 計測 | 退行なし | — |

### 6.4 fail-loud の実効性検証（Phase 1）

過去の実障害を**フィクスチャとして再現**し、fail-loud が実際に検知することを確認する。

- [ ] `avg_dollar_volume_21` の `_x/_y` サフィックス衝突（P0 L45）→ 必要カラム欠落として例外
- [ ] `is_rs_blue_dot` の alias 不一致（P1 L124）→ 未知キーまたはカラム欠落として例外
- [ ] `min_rs_ratio_rank_e63` の単独使用（P1 L162）→ ランク列が自動供給される
- [ ] タイポしたキー（例: `min_vol_surge_2`）→ 未知キーとして例外

### 6.5 API スモーク（Phase 1 / Phase 3）

```powershell
$env:STOCKTOOL_ENV="sandbox"
# API 起動後、/screener・/screener/dashboard・/screener/meta・/screener/presets が 200 を返すこと
# フロント左上にオレンジの Sandbox バッジが出ていることを目視確認
```

---

## 7. 途中発生した課題

（未着手）

---

## 8. スコープ外・残作業

| 項目 | 理由 / 引き継ぎ先 |
| :--- | :--- |
| 出口ルール（§6.4）の共通化 | 2026-07-20〜21 に `evaluate_position_exit_for_day()` へ一本化済み（issue_list P1 L105）。本計画は**エントリー側のスクリーン条件のみ**を扱う |
| `scenario_runner.py` の `startswith('Rise - Check')` によるサイレント除外（issue_list P2 L206） | 同型の F1 だが、フィルタではなく**戦略ディスパッチ**の問題。Phase 1 の fail-loud の考え方を適用できるが、別 issue として残す |
| `data/screener_presets.toml` の `active_rise_ids`/`active_fall_ids`（2026-08-01発見） | コメントは「フロントエンド・バックテストの両方で使われます」と書いてあるが、実際に参照しているのは `scenario_runner.py::load_scenario_config()` のみ。`screener_router.py` の `get_screener_presets()`/`get_screener_dashboard()` は `rise`/`fall` を無条件全件返しており参照していない（コメントが実態と乖離）。バッチ最適化（`run_scenario_batch.py`）は独自に `tmp/preset_*.toml` を生成するため無関係、CLIで`scenario_runner.py`を直接手動実行する場合のみ関係する。Phase 1 でレジストリ化する際にコメントを実態に合わせて修正、または本当にフロント側にも効かせるべきかを合わせて判断する |
| `run_scenario_batch.py` のポートフォリオ設定ハードコード（issue_list P2 L209） | スクリーン条件ではなくポートフォリオ構成の問題。スコープ外 |
| `get_groupby_cache` の `id(df)` キャッシュキー問題（issue_list P1 L121） | 別枠。本計画では触らない |
| 最適化バックテストと個別銘柄シナリオテストの**目的関数の乖離**（issue_list P1 L147） | 本計画は「同じ条件が同じ銘柄を抽出するか」を扱う。「同じ銘柄を抽出しても評価が食い違う」問題は目的関数側の課題であり別物 |
| フロントエンドでの `applied_filters` 表示 | Phase 1 では API が返すところまで。UI への露出は必要になった時点で別途 |
