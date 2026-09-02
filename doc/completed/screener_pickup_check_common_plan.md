# スクリーナー画面 Pickup/Check/Common 再編 計画書

- **ステータス**: ✅ 完了(Rise側スクリーナーをPickup/Check/Common+既存Overhead signに再編し、Pickupパネルにバッジ+ポップオーバー表示を追加。付随して型3シナリオテストのスキャン対象接頭辞をCheck→Pickupへ追従修正)
- **実施者**: AI エージェント(implementer, Claude Sonnet 5) + オーケストレーター(Claude Sonnet 5)
- **開始日**: 2026-09-02 / **完了日**: 2026-09-03
- **作業ブランチ**: `worktree-screener-pickup-check-common`(`.claude/worktrees/screener-pickup-check-common`)
- **対象 issue / 関連ドキュメント**: `doc/completed/structure_pivot_screener_plan.md`, `doc/completed/e5_vcp_state_restore_plan.md`, `doc/in_progress/rs_dot_age_plan.md`

## 1. 背景と目的

現状 `data/screener_presets.toml` の Rise 側「Check」グループには、バックテストで実績検証済みの高勝率戦略(Theme RSRank Momentum, Theme Leader 等 8 件、alpha/win_rate 等の実績コメント付き)と、検証なしの素朴な条件(1D% Gain, Volume Surge)が同居しており、UI上で区別がつかない。

ユーザーの意図は以下の3層への再編:
- **Pickup**: 当日の値動きで拾う、バックテストで edge が検証済みの戦略(現行「Check」の大半)
- **Check**: 1st/2nd pivot・Trend Line Break・ブルードット・VCP など、当日ではなく**数日後の推移を観察すべき**構造的セットアップ
- **Common**: 1D%・出来高等、検証済み edge を謳わない基本指標

加えて、TOMLコメントに埋もれているバックテスト成績を UI 上でバッジ/ポップオーバー表示することで、「どのグループに属するか」ではなく「実績があるかどうか」が一目でわかるようにする。

調査の結果、Check 用に想定していた5シグナルは**いずれも既存の本番カラム/特殊フィルタ**であり、新規指標開発なしで TOML 追加のみで実現できる(ユーザー合意 2026-09-02、2026-09-03に再確認・訂正):
- structure pivot / 1st pivot: `sp_pivot`/`sp_hl`
- 2nd pivot: `is_structure_2nd_break`(特殊フィルタ。`sp_pivot`+`close`+`change_1d_pct`からスクリーン評価時に算出、T3カラムではない)
- Trend Line Break: `sp_counter` T3カラム + `is_structure_trend_line_break`特殊フィルタ(2026-08-29昇格)
- VCP ブレイクアウト: `is_vcp_breakout`
- ブルードット: `rs_blue_dot_age`

> **訂正(2026-09-03)**: 当初「Trend Line Break / 2nd pivot は未実装」と記載していたが誤り。`doc/completed/structure_pivot_screener_plan.md` §5.6 に両方とも実装・本番昇格済みと明記されている。調査不足によるもの。Check プリセットは当初の3件から5件に拡張する。

なお structure pivot(1st pivot)は過去の検証(`structure_pivot_screener_plan.md`)で「エントリー条件として使うと成績が悪化する」と結論済みであり、これは Check の「観察用途であり edge を謳わない」という位置づけと整合する。

**完了基準**: Rise 画面が Pickup → Check → Common → Overhead sign の順で表示され、Pickup パネルにホバー/クリックで説明文+主要成績が出る。Check に新設した 5 プリセットが動作し、0件にならない妥当な母集団を返す。

## 2. スコープと設計判断

### 2.1 変更すること

- `data/screener_presets.toml`: Rise 側グループの再分類、新規5プリセット追加、バックテスト成績のコメント→構造化フィールド化
- `backend/api/schemas.py`: `ScreenerPresetItem` に `description` / `backtest` フィールド追加
- `backend/api/screener_router.py`: `get_screener_presets` で新フィールドをパース
- フロントエンドの `PresetItem` 型定義(`ScreenerPage.tsx` 内ローカル定義)・パネル描画へバッジ/ポップオーバー追加
- `backend/backtest/scenario_runner.py` / `scenario_scorer.py` / `run_scenario_batch.py`: 型3(個別銘柄シナリオテスト)がスキャン対象を選ぶ際の接頭辞 `SCENARIO_TARGET_PREFIX` を `'Rise - Check'` → `'Rise - Pickup'` に追従(グループ移動に伴う参照先の追従。実装中に発覚 → §7.1)

### 2.2 変更しないこと(確定した設計判断)

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| Overhead sign / Warning / Rebound sign | 4分類目として現状維持。Pickup/Check/Common へ統合しない | 買い候補選定ではなくリスク管理目的で性質が異なる(前回会話で合意済み) |
| structure pivot をスコア因子として採用 | しない。Check は観察用途に限定し、フィルタ閾値は非最適化の目安値とする | `structure_pivot_screener_plan.md` でエントリー条件化は逆効果と実測済み |
| Fall(売り目線)タブの再編 | 今回のスコープ外 | 発端は Rise 側「Check」混在の解消。対称化は要望があれば別プラン |
| Pickup 群8プリセットの閾値見直し・再最適化 | しない。既存プリセットをそのまま group 移動するのみ | 今回は表示分類の再編であり、モデル改善は別タスク |
| 既存 preset id (`check_1d_gain` 等) のリネーム | しない。`group` フィールドのみ変更 | id は URL/リンクの一部になり得るため、無意味な破壊的変更を避ける |

## 3. 変更内容

### 3.1 TOML 再編 (`data/screener_presets.toml`)

- 既存「Check」グループ8件を再分類:
  - **Pickup 行き**(バックテスト実績コメントあり): Theme RSRank Momentum, Theme Leader, RS MACD and Theme, RS Trend with Theme, Momentum no exhaust, RRG Improving In, 21EMA Pullback(`check_21ema`), Momentum 99(`check_momentum99`) → `group = "Pickup"`
  - **Common 行き**(実績コメントなし): 1D% Gain(`check_1d_gain`), Volume Surge(`check_volume_surge`) → `group = "Common"`
- **新規5プリセット追加**(`group = "Check"`):
  - `check_structure_pivot` 「Structure Pivot Watch」(1st pivot)— `sp_dist_pivot_pct` 等の virtual column(`screener_router.py` 既存定義)でピボット接近を検出。閾値は非最適化の目安値(§4で確認済み)
  - `check_2nd_break` 「2nd Break」— 既存特殊フィルタ `is_structure_2nd_break` + 流動性フロア
  - `check_trend_line_break` 「Trend Line Break」— 既存特殊フィルタ `is_structure_trend_line_break` + 流動性フロア
  - `check_vcp_breakout` 「VCP Breakout」— 既存特殊フィルタ `is_vcp_breakout` + 流動性フロア
  - `check_blue_dot` 「Blue Dot Recent」— `max_rs_blue_dot_age` で直近転換のみ抽出。日数は§4で確認済み
- 全プリセットに `description`(日本語の説明文、ポップオーバー表示用)を追加
- Pickup 8件について、コメント形式のバックテスト成績(`# port_cagr 147.62` 等)を `[rise.backtest]` サブテーブルに構造化(値は転記のみ、再計算しない)
- ファイル内の並び順を Pickup → Check → Common → Overhead sign に変更(表示グループ順はプリセット出現順に依存するため、これだけで UI 側の並びも揃う)

### 3.2 バックエンド API

- `schemas.py`: `ScreenerBacktestStats`(win_rate, expectancy_lcb, port_cagr, max_drawdown, port_vs_spy, total_trades、すべて `Optional[float]`。alpha/expectancy/spy_cagr は保持不要と判断し外す — §4.3参照)を追加し、`ScreenerPresetItem` に `description: Optional[str]` と `backtest: Optional[ScreenerBacktestStats]` を追加
- `screener_router.py::get_screener_presets`: TOML の `description` / `backtest` テーブルをパースして渡す(欠落時は None、後方互換)

### 3.3 フロントエンド

- `PresetItem` 型(`ScreenerPage.tsx` 内)に `description?` / `backtest?` を追加
- パネルヘッダーに小さな info バッジを追加。**`App.tsx` の System Status ポップオーバー(`healthPopoverOpen` によるクリックトグル、`position: absolute; top: 100%` のパネル、× クローズボタン)と同じ操作パターンを踏襲**。1枚だけ開く想定で `openInfoPopoverId: string | null` のような state 一本で管理する
  - ポップオーバー内容: 説明文(`description`) + 成績テーブル(`backtest` がある場合のみ。win_rate / expectancy_lcb / port_cagr / max_drawdown / port_vs_spy / total_trades)
- **`renderHotPicks()` の confluence 判定グループを修正**: 現行 `allowedGroups = activeTab === 'Rise' ? ['Check'] : ['Warning']`(`ScreenerPage.tsx` 該当箇所)は Rise 側で `'Check'` をハードコードしており、今回の再編で Check の中身が「観察対象3プリセット」に変わると Pickup 同士の一致が拾えなくなり黙って壊れる。Rise 側は `['Pickup']` に変更する(Fall 側 `['Warning']` は今回スコープ外のため変更しない)
- グループ見出し `[Pickup]` `[Check]` `[Common]` `[Overhead sign]` の並びは TOML 側の順序変更で対応済みのため、`ScreenerPage.tsx` のグルーピングロジック自体は変更不要

## 4. ユーザー確認事項(解決済み)

1. **Check 新設2プリセットの閾値**: 「Structure Pivot Watch = `sp_dist_pivot_pct` 0〜5%以内」「Blue Dot Recent = `rs_blue_dot_age` <= 5日」で確定。
2. **ポップオーバーの開閉トリガー**: System Status ウィジェットと同じクリックトグル+固定位置パネル+×クローズで確定(§3.3に反映)。
3. **バッジに出す成績指標**: 構造化フィールド方式を採用(TOML `Note=` の自由記述文字列をそのまま出す案は不採用)。理由: 自由記述はプリセットごとに書式が揺れやすく、将来ローカライズする際に文全体の翻訳が必要になる(数値+ラベル分離なら数値はそのまま、ラベルだけ差し替えられる)。項目は元の4つ(win_rate / expectancy_lcb / port_vs_spy / total_trades)に **CAGR(`port_cagr`)・DD(`max_drawdown`)を追加した6項目**で確定(§3.2に反映)。
4. **Hot Pick の抽出元グループ**: `Check` → `Pickup` に変更(既存ロジックが Check グループ内の複数シグナル一致でHot Pickを抽出していたため、これを Pickup に向け直す。§3.3に反映)。

## 5. 実装順序と進捗チェックリスト

- [x] TOML: 既存8プリセットの group 再分類(Pickup/Common)
- [x] TOML: バックテスト成績コメント → `[rise.backtest]` 構造化テーブルへ変換(Pickup 8件)
- [x] TOML: 新規5プリセット追加(Check group: 1st pivot / 2nd break / trend line break / VCP / ブルードット)
- [x] TOML: 全プリセットに `description` 追加
- [x] TOML: プリセット出現順を Pickup → Check → Common → Overhead sign に並べ替え
- [x] backend: `schemas.py` に `ScreenerBacktestStats` / フィールド追加
- [x] backend: `screener_router.py::get_screener_presets` のパススルー実装
- [x] backend: 関連 pytest 実行(`test_screener_api.py`, `test_screener_parity.py`)で既存挙動に影響ないことを確認(116 passed)。ただし `backend/tests/` フルスイート実行で `test_scenario_runner_integration` が新たに FAIL することを検出 → §7.1 参照(要オーケストレーター判断)
- [x] frontend: `PresetItem` 型更新
- [x] frontend: バッジ+ポップオーバー UI 実装(System Status と同じ操作パターン)
- [x] frontend: `renderHotPicks()` の `allowedGroups`(Rise側)を `['Check']` → `['Pickup']` に修正
- [x] frontend: `npm test` / `npm run build`(44 passed / build 成功)
- [x] 実機確認(代替手段): backend/frontend の実起動によるブラウザ目視は、本ワークツリーが
      本番データ未プロビジョニング(`data/parquet_master` 空・`data/stocktool.db` 空)のため
      実施できなかった。代わりに本番 `stocktool.db` を **read-only(`mode=ro`)** で直接開き、
      実際にAPIが使う `indicators.screener_filters` の特殊フィルタ関数を直接呼び出して
      新規5プリセットのヒット件数を検証した(詳細は §6)。加えて `test_screener_dashboard_current_presets_have_no_errors`
      (既存 pytest)が全プリセットで `error is None` であることを固定的に検証しており、
      ダッシュボードAPI配線自体は確認済み。並び順(Pickup→Check→Common→Overhead sign)は
      `/api/screener/presets` の実データパースで確認済み(§6)。ポップオーバーのブラウザ上の
      見た目は未確認(要フォローアップ)。
- [x] `backend/backtest/scenario_runner.py` / `scenario_scorer.py` / `run_scenario_batch.py` の
      `SCENARIO_TARGET_PREFIX` 追従修正(§7.1、オーケストレーターが実施)。関連テスト4ファイル
      の期待値も更新し、`test_scenario_runner_integration` を含む40件が pass することを確認
- [x] `backend/tests/` フルスイート最終確認(オーケストレーター実施): **1461 passed, 3 failed**。
      残り3件は §7.2 記載の環境依存(本ワークツリーに `data/parquet_master` 等が未provisioning)
      で、いずれも本タスクの変更とは無関係。`test_scenario_runner_integration` は §7.1 の
      修正で pass 済み
- [x] `doc/completed/` へ移動

### 作業中メモ

なし(完了)

## 6. 検証プラン / 結果

- `$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/api/test_screener_api.py backend/tests/api/test_screener_parity.py -v`
  → **116 passed**。
- `backend/tests/` フルスイート実行: 当初 1460 passed, 4 failed(`test_scenario_runner_integration`
  が本タスク由来の regression → §7.1、残り3件は本タスクと無関係な環境依存の既存失敗 → §7.2)。
  §7.1 の `SCENARIO_TARGET_PREFIX` 追従修正後に再実行し、**1461 passed, 3 failed**(残る3件は
  §7.2 の環境依存のみ)を確認して完了。
- `cd frontend; npm test; npm run build`
  → `npm test`: **44 passed**。`npm run build`: **成功**(tsc + vite build。既存の
  チャンクサイズ警告のみ、本タスク由来のエラーなし)。
- 実機確認: 本ワークツリーは `data/parquet_master` / `data/stocktool.db` が未プロビジョニング
  (`tools/provision_worktree_data.py . --mode read` を実行したが、read モードは
  `config.local.toml` に `prod_root` を記録するのみで DB 自体はコピーしない仕様のため、
  フル起動した API サーバーではダッシュボードを引けなかった)。そのため、本番
  `data/stocktool.db` を **`sqlite3.connect(f"file:{path}?mode=ro", uri=True)` で
  read-only 接続**し、実際に API が使う `indicators/screener_filters.py` の
  `filter_structure_2nd_break` / `filter_structure_trend_line_break` / `filter_vcp_breakout`
  を直接呼び出して新規5プリセットのヒット件数を検証した(書き込み操作は一切行っていない。
  使い捨てスクリプトは `tmp/` ではなく本エージェントのスクラッチパッドに置いて実行済み・
  リポジトリには残していない)。

  対象日 2026-09-01(本番最新日)・母集団3150銘柄(active かつ category in (テーマ, 個別)、
  当日 T3 あり)での結果:

  | プリセット | ヒット件数(フィルタのみ) | 流動性フロー込み |
  | :--- | :--- | :--- |
  | `check_structure_pivot`(`sp_dist_pivot_pct` 0〜5%) | 508 | (フロー無し設計のため対象外) |
  | `check_2nd_break`(`is_structure_2nd_break`) | 52 | 45(`min_market_cap>=3e8`) |
  | `check_trend_line_break`(`is_structure_trend_line_break`) | 7 | 5 |
  | `check_vcp_breakout`(`is_vcp_breakout`) | 0 | 0 |
  | `check_blue_dot`(`rs_blue_dot_age<=5`) | 15 | (フロー無し設計のため対象外) |

  `check_vcp_breakout` が対象日のみ0件だったため、直近24営業日(2026-07-30〜2026-09-01)で
  再検証したところ、9日分で1〜3件のヒットがあり(残り15日は0件)、**イベント型フィルタとして
  正常に機能している**(`doc/backend_specification.md` にある通り出現頻度が低いのは既知・
  意図通り)。よって「0件にならない妥当な母集団」の完了基準は、単日ではなく複数日で見れば
  満たされていることを確認した。
  また `test_screener_dashboard_current_presets_have_no_errors`(既存pytest、TOML全プリセットの
  `error is None` を固定検証)が pass しており、ダッシュボードAPIの配線自体(特殊フィルタの
  キー解決・仮想カラム式の解決)にも問題がないことを確認済み。
  `/api/screener/presets` 相当のTOML直接パースでも、Rise側プリセットの出現順が
  Pickup(8件)→Check(5件)→Common(2件)→Overhead sign(3件)になっていることを確認した。
  一方、ブラウザ上でのポップオーバーの見た目・実際のクリック操作は未確認(要フォローアップ)。

## 7. 途中発生した課題

### 7.1 `backend/backtest/scenario_runner.py` の `SCENARIO_TARGET_PREFIX` ハードコード(計画の穴・要判断)

§6 で指定されたスコープ(`test_screener_api.py` / `test_screener_parity.py`)だけでなく
`backend/tests/` 全体を実行したところ、
`backend/tests/backtest/test_scenario_runner.py::test_scenario_runner_integration` が
TOML 再編後に失敗するようになった(再編前の TOML に戻すと pass することを
`git stash` で確認済み)。

原因: `backend/backtest/scenario_runner.py` に

```python
SCENARIO_TARGET_PREFIX = 'Rise - Check'
```

というハードコードがあり、`run_scenario_test()` は `data/screener_presets.toml` を
実ファイルとして読み込み(`config_path: str = "data/screener_presets.toml"` がデフォルト引数)、
`active_rise_ids` に含まれるプリセットのうち `"Rise - {group} - {name}"` が
`'Rise - Check'` で始まるものだけを「個別銘柄シナリオテスト(型3)でスキャン対象とする戦略」として
抽出している。今回 Pickup 8件の `group` を `"Check"` → `"Pickup"` に変更したため、
`active_rise_ids` に残っている `rrg_improving_in`(→Pickup) と `check_1d_gain`(→Common) は
どちらも `'Rise - Check'` にマッチしなくなり、スキャン対象が0件になって
`ValueError`(「シナリオテストの対象となる戦略が1つも見つかりません」)で失敗する。

これはフロントエンドの `renderHotPicks()` の `allowedGroups` ハードコードと**全く同じ種類の
問題**だが、計画書(§3.3)で明示的にスコープに入っていたのはフロントエンドの当該箇所のみで、
このバックエンドファイルは §2.1/§3 のどこにも挙げられていない。

**オーケストレーター判断(2026-09-03、解決済み)**: ワーカーの見立て通り「グループ移動に伴う
参照先の追従」であり、型3の対象選定という新たな設計判断ではないと判断した。理由:
「型3がスキャンする対象はバックテストで edge が検証済みの戦略」という**既存の設計方針そのもの
は変わっておらず**、変わったのはその戦略群を指す TOML 上のラベル(`"Check"` → `"Pickup"`)
だけ。旧ラベルのまま放置すると型3が新方針(Pickup)ではなく空集合をスキャンすることになり、
「グループ移動しても型3の対象は変わらない」という §2.2 の意図(表示分類の再編であり、
モデル改善やテスト対象の変更は行わない)にむしろ反する。よって以下の追従修正を適用した:

- `backend/backtest/scenario_runner.py`: `SCENARIO_TARGET_PREFIX = 'Rise - Check'` →
  `'Rise - Pickup'`(101行目)。同ファイル内のエラーメッセージ文言(122-151行目、
  ユーザー向けに `group を 'Check' に` と案内していた2箇所)も `'Pickup'` に追従。
- `backend/backtest/scenario_scorer.py`: `ScenarioScorer.__init__` の既定値
  `target_group_prefix: str = 'Rise - Check'` → `'Rise - Pickup'`(9行目。
  `test_prefix_constant_matches_scorer_default` がこの既定値と `SCENARIO_TARGET_PREFIX`
  の一致を検証しているため、両者を必ず同時に変更する必要がある)。
- `backend/backtest/run_scenario_batch.py`: Optuna最適化結果からシナリオ用TOMLを動的生成する
  `generate_preset_toml`(139-143行目)が `group = "Check"` を固定出力していた箇所を
  `group = "Pickup"` に変更(＋コメント追従)。ここを直さないと、今後 Optuna で最適化した
  新戦略が型3で一件もスキャンされなくなる、同種のサイレント失敗が再発する。
- 追従して以下のテストの期待値も更新(いずれも「`"Check"` 固定」を前提にしていた既存アサーション):
  `backend/tests/backtest/test_scenario_batch_jobs.py`(4箇所)、
  `backend/tests/backtest/test_scenario_strategy_coverage.py`(ファイル冒頭の説明文・
  テスト関数名 `test_check_group_is_scanned`→`test_pickup_group_is_scanned`・
  フィクスチャ文字列・エラーメッセージアサーションを全面更新)、
  `backend/tests/backtest/test_scenario_runner.py::test_scenario_runner_excludes_illiquid_symbols`
  (テスト内で動的生成する一時TOMLの `group = "Check"` を `"Pickup"` に変更)。
- 検証: `pytest backend/tests/backtest/test_scenario_runner.py backend/tests/backtest/test_scenario_scorer.py backend/tests/backtest/test_scenario_batch_jobs.py backend/tests/backtest/test_scenario_strategy_coverage.py -q`
  → **40 passed**(修正前は `test_scenario_runner_integration` 1件 FAIL)。

- 影響ファイル: `backend/backtest/scenario_runner.py`, `backend/backtest/scenario_scorer.py`,
  `backend/backtest/run_scenario_batch.py`, `backend/tests/backtest/test_scenario_batch_jobs.py`,
  `backend/tests/backtest/test_scenario_strategy_coverage.py`, `backend/tests/backtest/test_scenario_runner.py`
- 結果: **解決済み**。§2.1(変更すること)に本項目を追記し、§8で「型3のスキャン対象を
  Pickup に統一した」ことを明記する。

### 7.2 `backend/tests/` フルスイートのその他の失敗(本タスクと無関係と判断)

`backend/tests/` 全体実行で以下も失敗したが、いずれも本タスクの変更(TOML再編・schemas.py・
screener_router.py)とは無関係と判断し、対応していない:

- `backend/tests/backtest/test_scenario_comparison.py::test_run_comparison_generates_outputs`:
  `data/parquet_master` にファイルが存在せず `FileNotFoundError`。本ワークツリーに
  本番Parquetマスタが provisioning されていない環境要因(このワークツリーは
  スクリーナー再編用に用意されたもので、バックテスト用の全期間Parquetは持っていない)。
  TOML再編・スキーマ変更とは無関係。
- `backend/tests/services/test_special_removal.py::TestTOMLMigration::test_a_only_toml_no_special` /
  `test_b_only_toml_no_special`: 絶対パス `d:\My Documents\Programing\stocktool\data\screener_presets_A_only.toml` /
  `screener_presets_B_only.toml`(本体チェックアウト側のパスで、ワークツリーには存在しない)を
  直接開こうとして `FileNotFoundError`。本タスクで触れたファイルとは無関係の既存の
  環境依存テスト。

## 8. スコープ外・残作業

- Fall(売り目線)タブへの同様の再編(Warning/Rebound sign の Pickup/Check/Common 相当分類)は今回対象外。要望があれば別プラン
- `is_vcp_breakout` を使った「ブレイク通知」用途は `e5_vcp_state_restore_plan.md` で一度見送られた経緯があるため、今回の Check プリセットで実際に有用かは運用しながら判断する
- `is_structure_2nd_break` / `is_structure_trend_line_break` はスクリーン条件としての採否検証(バックテストでの edge の有無)は未実施。Check は「観察用途で edge を謳わない」前提のため今回は検証しないが、将来 Pickup 相当に格上げしたくなった場合は別途バックテスト検証が必要
