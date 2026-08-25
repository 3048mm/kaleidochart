# バックテスト戦略カード表示の統一 計画書

- **ステータス**: ✅ 完了 — 両タブの戦略カードを共通骨格へ統一し、実行条件を折りたたみ Run Info バーに集約した（未実施は本体への merge と API サーバー再起動のみ）
- **実施者**: AI エージェント (Claude Opus 5)
- **開始日**: 2026-08-25 / **完了日**: 2026-08-25
- **作業ブランチ**: worktree-backtest-card-unification（`.claude/worktrees/backtest-card-unification`）
- **対象 issue / 関連ドキュメント**: `doc/frontend_specification.md`

## 1. 背景と目的

Backtest ダッシュボードの 2 タブ（`📈 ETF Backtest` / `⚖️ Scenario Test`）で、
戦略ごとの結果を出す「四角枠のカード」の構成が揃っておらず、視線の置き場が
タブ間で変わってしまう。

現状の非対称:

| | ETF (`StrategyCard`) | シナリオ (`PanelCard`) |
|---|---|---|
| ミニグラフ | なし | あり |
| 主結果 | 最終資産 + 総リターン% | CAGR + Max/Min |
| メトリクス | 2列6個 | 2列5個（片側が空く） |
| 末尾 | なし | 「最終資産」バー（ゲイン%なし） |
| Period 情報 | 常時表示（実データ） | なし。凡例に固定文字列がベタ書き |

**完了条件**: 両タブのカードが同一の骨格（ミニグラフ → 主結果 CAGR/DD →
2行目 → 3行目 → 末尾 Final Capital + 総ゲイン）で描画され、
PERIOD / TRADING DAYS / INITIAL CAP / TAX RATE が両タブで
折りたたみ（デフォルト閉）から参照でき、カード内の日本語ラベルが英語化されていること。

## 2. スコープと設計判断

### 2.1 変更すること

- 共通カードコンポーネントの新設と、両ページからの利用
- 共通 Run Info バー（折りたたみ、デフォルト閉）の新設と、両ページへの設置
- シナリオ側 summary API に Run Info 用フィールドを追加
- カード内日本語ラベルの英語化
- 詳細分析チャートの Cash 線の配色変更（グレー点線の意味の衝突解消）

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 |
|---|---|
| ETF ミニグラフのベンチマーク点線 | **Buy & Hold**。ETF タブは SPY 以外（TQQQ/SOXL/UGL 等）も選べ、SPY 固定だとスケール差で SPY 線が潰れて読めなくなるため。B&H カード自身は点線なし |
| ETF の Regime Changes | **残す**。3行目を `Rebalances / Regime Changes` の2列にして枠を埋める（削除だと片側が空く） |
| シナリオ側の TAX RATE 取得元 | **config 読み + 今後は run_params に記録**。既存の全 run 出力を再実行なしで表示でき、今後の run は実行時の正確な値を持つ。API 側は `run_params.consider_tax` → 無ければ `load_tax_rate()` フォールバック |
| Run Info の粒度 | **タブごとに1つ**（ページ上部）。カードごとには置かない |
| MC の cagr_max / cagr_min | CAGR 直下に小さく残す（optional prop）。ETF 側は渡さない |
| ScenarioDetailView の KPI カード群（大きい方） | **今回は触らない**。対象は「戦略別結果表示の四角枠」に限定 |
| 詳細分析チャートの SPY 線 | 青点線のまま。動かすのは Cash 線のみ |

## 3. 変更内容

### 3.1 統一後のカード仕様

```
┌──────────────────────────────────┐
│ ● 💎 Full Position               │  ヘッダ（色ドット＋アイコン＋ラベル）
│ ╭──────────────────────────────╮ │
│ │    ╱╲      ╱                 │ │  ミニグラフ h=80
│ │  ╱  ╲╱ ┈┈┈┈┈┈┈┈┈             │ │  実線=戦略 / グレー点線=ベンチマーク
│ ╰──────────────────────────────╯ │
│     +18.2%          -22.1%       │  主結果（2カラム）
│     CAGR            MAX DD       │
│  ┌───────────┐  ┌───────────┐    │  2行目
│  ┌───────────┐  ┌───────────┐    │  3行目
│ ┃ Final Capital   $284,120        │  末尾（総ゲイン%付き）
│ ┃                 (+184.1%)       │
└──────────────────────────────────┘
```

| | 2行目 | 3行目 |
|---|---|---|
| ETF | Sharpe / Time in Market | Rebalances / Regime Changes |
| Scenario | Avg Trade % / Win Rate | Trades / Profit Factor |

### 3.2 バックエンド

| 対象 | 変更 | 影響範囲 |
|---|---|---|
| `backend/api/schemas.py` `BacktestScenarioSummary` | `start_date` / `end_date` / `initial_capital` / `trading_days` / `consider_tax` / `total_return_pct` を Optional で追加 | 追加のみ。既存フィールドは不変 |
| `backend/api/backtest_router.py` `get_scenario_summary` | MC 集約分岐・単発分岐の**両方**で上記を埋める。`trading_days` は equity CSV 行数、`consider_tax` は `run_params.consider_tax` → 無ければ `load_tax_rate()` | 既存レスポンスに対して後方互換 |
| `backend/backtest/scenario_runner.py` `run_params` | `consider_tax` を追記 | 今後の run のみ。既存出力には影響なし |

### 3.3 フロントエンド

| 対象 | 変更 |
|---|---|
| `frontend/src/components/BacktestStrategyCard.tsx`（新規） | 共通カード骨格。ミニグラフ／主結果／2行目／3行目／末尾を props で受ける |
| `frontend/src/components/BacktestRunInfo.tsx`（新規） | 折りたたみ Run Info バー（デフォルト閉） |
| `frontend/src/pages/EtfSingleBacktestPage.tsx` | `StrategyCard` 廃止。常時表示の Period バーを Run Info バーへ。equity から戦略別ミニグラフ系列（+ B&H 点線）を生成してカードへ渡す |
| `frontend/src/pages/RegimeComparisonPage.tsx` | `PanelCard` 廃止。Run Info バー追加。ベタ書き凡例を実データ化。`1取引平均`→`Avg Trade` / `最終資産`→`Final Capital` / `CAGR (年率)`→`CAGR` |
| `frontend/src/pages/ScenarioDetailView.tsx` | Cash 線を白点線 → 低 opacity の白実線に変更し、グレー点線＝SPY という意味づけと衝突させない |
| `frontend/src/api/backtest.ts` | `BacktestScenarioSummary` 型に追加フィールド |

## 4. ユーザー確認事項

計画レビュー時点で判断済み（§2.2 に反映）:

1. ETF ミニグラフの点線 → **Buy & Hold** ✅
2. ETF の Regime Changes → **3行目に並べる** ✅
3. シナリオ側 TAX RATE → **config 読み + 今後は run_params に記録** ✅
4. 詳細分析チャートの Cash 線 → **別色に振る（今回のスコープに含める）** ✅

未解決の確認事項: なし

## 5. 実装順序と進捗チェックリスト

- [x] 5-1. バックエンド: `BacktestScenarioSummary` スキーマ拡張
- [x] 5-2. バックエンド: `get_scenario_summary` の両分岐で Run Info 項目を埋める
- [x] 5-3. バックエンド: `scenario_runner.run_params` に `consider_tax` を記録
- [x] 5-4. バックエンド: `backend/tests/api/test_backtest_api.py` に Run Info 項目のテストを追加
- [x] 5-5. フロント: `BacktestStrategyCard.tsx` 新設
- [x] 5-6. フロント: `BacktestRunInfo.tsx` 新設
- [x] 5-7. フロント: `EtfSingleBacktestPage` を移行（ミニグラフ新設含む）
- [x] 5-8. フロント: `RegimeComparisonPage` を移行（ラベル英語化含む）
- [x] 5-9. フロント: `ScenarioDetailView` の Cash 線を別色に
- [x] 5-10. 既存テスト更新（`RegimeComparisonPage.test.tsx` は `1取引平均` を直接参照していた）
- [x] 5-11. 全体検証（pytest / vitest / tsc build）
- [x] 5-12. `doc/frontend_specification.md` の該当節を更新
- [x] 5-13. ブラウザで目視確認（ユーザー）→ 「基本的にオッケー」で合格
- [ ] 5-14. 本体へ merge し、API サーバーを再起動（ユーザー作業。再起動しないとシナリオタブの Run Info バーが出ない）

### 作業中メモ

なし。

## 6. 検証プラン / 結果

```powershell
# バックエンド
$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/api/test_backtest_api.py backend/tests/api/test_etf_single_api.py -v

# フロントエンド
cd frontend
npm test
npm run build
```

加えて目視確認: `run/run_server.bat` + `npm run dev` で `/backtest` を開き、
両タブのカードが同一骨格で並ぶこと・Run Info がデフォルト閉で開閉すること・
詳細分析チャートで Cash と SPY が区別できることを確認する。

結果:

| 検証 | 結果 |
| :--- | :--- |
| pytest（backend 全体） | 1045 passed / **1 failed** — 失敗は `test_scenario_comparison.py::test_run_comparison_generates_outputs` で、`data/parquet_master` が無いことによる `FileNotFoundError`。`agent_execution_rules.md` §10.3「ワークツリーの `data/` はほぼ空」の通りの環境要因で、本変更とは無関係 |
| vitest（frontend 全体） | 28 passed / 7 files passed |
| `tsc -b` | エラーなし |
| `npm run build` | 成功（783 modules） |

目視確認（未実施・ユーザー）: `run/run_server.bat` + `npm run dev` で `/backtest` を開き、
両タブのカードが同一骨格で並ぶこと・Run Info がデフォルト閉で開閉すること・
詳細分析チャートで Cash と SPY が区別できることを確認する。

## 7. 途中発生した課題

### 7.1 本体チェックアウト（main）で作業を始めてしまった

着手時にワークツリーを切らず、本体チェックアウトの `main` 上で編集を進めた
（`CLAUDE.md`「本体でのコミットは禁止」「main への直接コミット禁止」に違反する状態）。
コミット前に気付いたため、以下で是正した:

1. `git worktree add .claude/worktrees/backtest-card-unification -b worktree-backtest-card-unification HEAD`
2. 変更・新規の 14 ファイルをワークツリーへコピーし、`cmp` で全件バイト一致を確認
3. 本体を `git checkout --` / `rm` で復元（`frontend/tsconfig.tsbuildinfo` も戻した）
4. 本体の `git status` が空であることを確認してからワークツリーへ移動

**教訓**: 開発アイテムの着手時は、計画書を書く前にワークツリーを切る。

### 7.3 目視確認用に立てた vite dev サーバーで大きい応答が途中停止した

ワークツリーに立てた dev サーバー（5175）でシナリオタブを開くとローディングが
終わらなかった。切り分けた結果、**本変更とは無関係の dev サーバー側の問題**だった。

同じ13リクエスト（シナリオタブが投げるもの）を各経路で計測:

| 経路 | 結果 |
| :--- | :--- |
| バックエンド 8000 に直接 | 全13本が2秒以内・サイズ完全 |
| 本体の dev サーバー 5173 経由 | 全13本が2秒以内（2回試行とも） |
| ワークツリーの dev サーバー 5175 経由 | equity 5本中3本が無応答、本文も 391,680B で停止 |

`curl` の生リクエストで再現するため React のコードは関与しない。シナリオタブは
equity（約436KB）を5本同時に取りに行くので直撃し、ETF タブは1本なので通っていた。

途中、最初に誤起動した `npx vite`（プロジェクト外の 8.2.2 を取得し、設定を読めて
おらず proxy 無し）が停止後も 5175 を二重 listen していたのも発見して潰したが、
それを解消しても再現したため別要因。本体の 5173 が同条件で正常なので
インスタンス固有と判断し、深追いはしていない。

**回避策**: `tmp/preview_server.js`（使い捨て・コミット対象外）を作り、vite dev を
介さずビルド済み `dist/` を配って `/api` を 8000 へ素通しする構成で目視確認した。

> [!NOTE]
> ワークツリーで dev サーバーを立てるときは、**起動前に `npx` ではなく
> `npm run dev` を使う**（`npx vite` はプロジェクト外の vite を取得しうる）。
> また停止後は `Get-NetTCPConnection -LocalPort <port>` で listen が残っていないか
> 確認する（プロセスが生き残って二重 listen する事故があった）。

### 7.2 main が 17 コミット進んでいたのでマージした

作業中に別セッションが main を進めていた（`c19ea44` → `32e07a4`）。
自ブランチへコミットしてから `git merge main` を実行し、**コンフリクトなし**で取り込んだ。

変更が重なったのは `backend/api/backtest_router.py` の1ファイルのみで、
main 側の変更は `yearly_performance` 集計への `geo_pnl_pct` 追加。
本変更（Run Info フィールド追加・`_count_trading_days` / `_resolve_consider_tax` の新設）とは
別リージョンのため、機械的にも意味的にも衝突しない。マージ後の backend テストも
上記の環境要因1件を除いて全通過。

## 8. スコープ外・残作業

- `ScenarioDetailView` 上部の大きい KPI カード群の日本語ラベル（`CAGR (年平均成長率)` 等）は今回対象外
- `RegimeComparisonPage` の `STRATEGIES` 定数ハードコードは別 issue（`doc/issue_list.md` 起票済み）

### 作業中に見つけて直したもの（スコープ外・ついで）

- `frontend/src/components/__tests__/RsLineChart.test.tsx` の `createChart` モックに
  `addHistogramSeries` / `priceScale` が無く、2件が失敗していた。`RsLineChart.tsx` が
  コミット `3ef3b93`（DataView インジケータ追加）で RS 相対出来高のヒストグラムを
  描き始めたのにモックが追随していなかったもので、本変更とは無関係の既存不具合。
  「コミット前に全テスト通過」を満たすため、モックの穴だけ埋めた（テストのみの変更）。
