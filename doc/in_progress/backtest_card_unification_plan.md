# バックテスト戦略カード表示の統一 計画書

- **ステータス**: 🚧 進行中
- **実施者**: AI エージェント (Claude Opus 5)
- **開始日**: 2026-08-25 / **完了日**: —
- **作業ブランチ**: worktree-objective-quality-first（本体チェックアウト）
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

- [ ] 5-1. バックエンド: `BacktestScenarioSummary` スキーマ拡張
- [ ] 5-2. バックエンド: `get_scenario_summary` の両分岐で Run Info 項目を埋める
- [ ] 5-3. バックエンド: `scenario_runner.run_params` に `consider_tax` を記録
- [ ] 5-4. バックエンド: `backend/tests/api/test_backtest_api.py` に Run Info 項目のテストを追加
- [ ] 5-5. フロント: `BacktestStrategyCard.tsx` 新設
- [ ] 5-6. フロント: `BacktestRunInfo.tsx` 新設
- [ ] 5-7. フロント: `EtfSingleBacktestPage` を移行（ミニグラフ新設含む）
- [ ] 5-8. フロント: `RegimeComparisonPage` を移行（ラベル英語化含む）
- [ ] 5-9. フロント: `ScenarioDetailView` の Cash 線を別色に
- [ ] 5-10. 既存テスト更新（`RegimeComparisonPage.test.tsx` は `1取引平均` を直接参照している）
- [ ] 5-11. 全体検証（pytest / vitest / tsc build）
- [ ] 5-12. `doc/frontend_specification.md` の該当節を更新し、本計画書を `doc/completed/` へ移動

### 作業中メモ

（着手前）

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

結果: （未実施）

## 7. 途中発生した課題

（なし）

## 8. スコープ外・残作業

- `ScenarioDetailView` 上部の大きい KPI カード群の日本語ラベル（`CAGR (年平均成長率)` 等）は今回対象外
- `RegimeComparisonPage` の `STRATEGIES` 定数ハードコードは別 issue（`doc/issue_list.md` 起票済み）
