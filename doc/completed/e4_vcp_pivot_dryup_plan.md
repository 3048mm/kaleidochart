# E1/E2 VCP ブレイク改良: 真のピボットクロス復活＋土台ドライアップゲート 計画書

- **ステータス**: ✅ 完了（pivot_tol / base_vol_dry_max を追加。サニティで PF 1.37→2.25(E1)/2.31(E2)・期待値 +1.06%→+3.00% に改善。全スイート green）
- **実施者**: AI エージェント (Claude Code, claude-fable-5)
- **開始日**: 2026-07-08 / **完了日**: 2026-07-09
- **作業ブランチ**: worktree-e1e2-vcp-pivot-dryup（バックグラウンドジョブのワークツリー隔離。**main 未反映**）
- **対象 issue / 関連ドキュメント**: `doc/completed/e3_vcp_breakout_plan.md`（前回のイベント化再設計）、2026-07-08 の E1/E2 式再評価の会話

## 1. 背景と目的

2026-07-05 の再設計で E1/E2 は「状態版 VCP」から「ブレイクアウトイベント版」になった（発火 3.12→1.04 回/銘柄）。ただしブレイク検出を `change_1d_pct` の大陽線に切り替えた際、次の 2 つが VCP の本質から欠落した:

1. **ピボット上抜け性の欠如**: 現行の「高値から `near_high_tol`(4%) 以内 ＋ 当日 +4%」は抵抗線を実際に超えた保証がない。-8%→-4% へ戻る土台内リバウンド陽線でも発火し、ブレイク後の上昇継続中の大陽線でも再発火しうる。
2. **土台の出来高ドライアップ未検証**: 古典的 VCP は「収縮末端の出来高枯れ → ブレイク日の膨張」のコントラストが核心だが、現行式はブレイク日の膨張のみを見る。

**成功条件**: ①昨日までのローリング高値（当日を含まない真のピボット）を終値で上抜けた日だけ発火する、②前日の出来高が枯れていた土台のみ通過する — の 2 条件を追加した上で、1年間のファネル診断で検出数が LCB 評価に耐える水準（目安 0.2件/日以上 ≒ 学習2期間で130件以上）を維持すること。→ **達成**（§6）。

### ベースライン(現行式、e3 計画書 §6 より)
- change_1d ベース設計: 113件/251日 ≈ 0.45件/日、勝率50.4%・PF1.37・期待値+1.06%
- 発火重複: 1.04回/銘柄

## 2. スコープと設計判断

### 2.1 変更すること
- `filter_vcp_breakout` に **ピボットクロス条件**（`pivot_tol`）と **ドライアップ条件**（`base_vol_dry_max`）をオプション引数として追加
- prev マージに `vol_surge_21` を追加（バックテスト側・API 側の両方）
- E1/E2 の TOML に新パラメータ＋最適化範囲を追加
- `FILTER_ATTACHED_PARAM_KEYS` に新パラメータ 2 件を登録

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 |
|---|---|
| 新規 T3 カラム | **不要**。ピボットクロスは既存カラムから代数的に復元（§3.1）。サンドボックス不要 |
| `breakout_change` の ADR 正規化（評価④） | 今回はスコープ外。①②の効果測定を先に行う（同時に変えると寄与が分離できない） |
| E1/E2 の相互排他化（評価⑤） | 同上。config のみで後から可能 |
| 収縮の持続性・段階性検証（評価③） | v2 のまま保留（T3 カラム追加＝サンドボックス案件） |
| `near_high_tol` / `breakout_change` | **温存**。ピボットクロスと意味が重なるが、削除せず Optuna に緩和/併用を探索させる |
| 後方互換 | 新引数はデフォルト `None`＝無効。TOML に書いた場合のみ発火し、既存テスト・他プリセットの挙動は不変 |
| GUI プリセットへの反映 | 見送り（Q2 未回答のため安全側。新引数は None=無効で壊れない。必要なら後から TOML に追記のみ） |
| コミット/プッシュ | 行わない（`doc/agent_execution_rules.md` §7）。`git add` まで |

## 3. 変更内容

### 3.1 ピボットクロス条件（`backend/indicators/screener_filters.py`）

**何を**: 「今日の終値が、**昨日までの** N日ローリング最大高値を上抜けた」を条件化する。

**鍵となる導出**（新カラム不要の根拠）:
- `change_1d_pct = close.pct_change()*100`（calculate.py:27）→ `prev_close = close / (1 + change_1d_pct/100)`
- `dist_W_high_pct = (close - rollmax_W)/rollmax_W*100` → `prev_rollmax_W = prev_close / (1 + prev_dist_W_high_pct/100)`
- クロス条件 `close ≥ prev_rollmax_W × (1 − pivot_tol/100)` は代数的に:

```
(1 + change_1d_pct/100) × (1 + prev_dist_{W}_high_pct/100) ≥ 1 − pivot_tol/100
```

`change_1d_pct`（当日）と `prev_dist_63d/52w_high_pct`（前日）は両経路とも既にマージ済み
（backtest_screener.py / screener_cross_section.py）なので、**フィルタ内部の式だけで実装できる**。
旧設計（2026-07-05 に破棄）の「dist が当日を含むローリング最大で計算され新高値日ほど落ちる」構造欠陥は、
比較対象が*前日まで*の最大なので発生しない。

- 新引数: `pivot_tol: float | None = None`。`None`=条件無効（従来挙動）。`0.0`=昨日の高値ちょうど、正の値=高値の少し下まで許容。
- E1 は `prev_dist_63d_high_pct`、E2 は `prev_dist_52w_high_pct` を参照（`high_window` で切替。deny-by-default の required_prev に該当列を追加）。

### 3.2 ドライアップ条件（同上＋prev マージ 2 箇所）

**何を**: `prev_vol_surge_21 ≤ base_vol_dry_max` を条件化（前日の出来高が 21日平均比で枯れていた）。

- 新引数: `base_vol_dry_max: float | None = None`。`None`=無効。
- prev マージに `vol_surge_21` を追加:
  - `backend/backtest/backtest_screener.py` の `prev_merge_cols`
  - `backend/api/screener_cross_section.py` の `_PREV_COLS`
- 有効時のみ `prev_vol_surge_21` を required に加える（無効時は列が無くても従来どおり動く）。

### 3.3 ディスパッチ結線（2 箇所）

- `backend/backtest/backtest_screener.py` / `backend/api/screener_cross_section.py` の `filter_vcp_breakout` 呼び出しに `pivot_tol` / `base_vol_dry_max` を追加（未指定は None のまま渡す）
- `backend/backtest/backtest_runner.py` の `FILTER_ATTACHED_PARAM_KEYS` に `"pivot_tol"`, `"base_vol_dry_max"` を追加

### 3.4 E1/E2 の TOML（`backend/backtest/backtest_config.toml`）

```toml
# E1/E2 共通で追加（固定値はファネル診断 §6 で調整済み）
breakout_change = 3.0      # 4.0→3.0（クロス条件が主トリガーになったため）
pivot_tol = 0.5
base_vol_dry_max = 1.2     # 1.0 は過剰でイベントが枯渇（§6/§7）

[strategy.optimization]
pivot_tol        = { type = "float", min = 0.0, max = 2.0, step = 0.25 }
base_vol_dry_max = { type = "float", min = 0.7, max = 1.3, step = 0.1 }
```

**影響範囲（非互換）**: フィルタの意味が変わるため、**既存の E1/E2 study とは非互換**（前回の E1_old 退避と同じ扱い）。

## 4. ユーザー確認事項

- **Q1: 検出数が不足した場合の優先順** → 回答（2026-07-09）: 方針承認。実測では `breakout_change` 緩和はほぼ効かず（§7）、探索範囲内の `base_vol_dry_max=1.2` 緩和で 0.2件/日超を回復。
- **Q2: GUI プリセットへの反映** → 未回答のため安全側（反映しない）で実施。必要なら TOML 追記のみで対応可。
- **Q3: 既存 E1/E2 study の退避命名** → 前回同様ユーザー側で実施。

## 5. 実装順序と進捗チェックリスト（TDD）

- [x] `test_vcp_breakout_filter.py` に失敗テスト追加（クロス成立日のみ通過／pivot_tol 許容幅／ドライアップ NG 不通過／None 時は従来挙動／有効時 prev 列欠損 deny／52週版の参照列／API 同値性 — 新規9件）
- [x] `filter_vcp_breakout` 実装（pivot_tol / base_vol_dry_max、docstring 更新）
- [x] prev マージに `vol_surge_21` 追加（backtest_screener.py / screener_cross_section.py）
- [x] ディスパッチ結線 + `FILTER_ATTACHED_PARAM_KEYS` 追加
- [x] E1/E2 TOML 更新（固定値＋最適化範囲）
- [x] ファネル診断（`tmp/funnel_vcp_pivot_dryup.py` / `tmp/funnel_sweep.py`）
- [x] 全テストスイート green（333 passed / 既存の環境依存 1 件のみ fail — §7）
- [x] サニティ・バックテスト（E1/E2、2024-06〜2025-05）
- [x] 仕様書 §3.6・issue_list 反映
- [x] 完了時に本計画書を `doc/completed/` へ移動

### 作業中メモ

なし（完了）

## 6. 検証プラン / 結果

- **単体**: 14 passed（既存5＋registry等＋新規9）。`None` 引数の回帰テストで従来挙動の不変を固定。
- **同値性**: API（`evaluate_special_filters`）とバックテストで同一通過集合 — green。
- **全スイート**: 333 passed / 1 failed（`test_api_rs_data.py` — `data/stocktool.db` を直接開く既存テストで、ワークツリーに本番 DB が無いための環境要因。本変更と無関係）。
- **ファネル診断**（本番 Parquet 読み取りのみ、2024-06-01〜2025-05-31、249営業日、個別銘柄）:

| 設計（E1, mcap≥3e8） | 件数 | 件/日 | 発火/銘柄 |
|---|---|---|---|
| 現行（change_1d≥4 のみ） | 131 | 0.53 | 1.07 |
| ＋ピボットクロス(tol=0.5) | 112 | 0.45 | 1.06 |
| ＋ドライアップ(≤1.0) | 43 | 0.17 | 1.02 |
| 両方（dry≤1.0, chg≥4） | 35 | 0.14 | 1.03 |
| **両方（dry≤1.2, chg≥3）= 採用値** | **58** | **0.23** | **1.05** |

（E2 も同傾向: 採用値で 59件・0.24件/日）

- **サニティ・バックテスト**（同期間、`backtest_runner.py --strategy`）:

| | Trades | WinRate | PF | 期待値 | α/trade | 平均日数 |
|---|---|---|---|---|---|---|
| E1 旧式（e3 実測） | 113 | 50.4% | 1.37 | +1.06% | +0.08% | — |
| **E1 新式** | 48 | 52.1% | **2.25** | **+3.00%** | **+1.33%** | 24.0 |
| **E2 新式** | 47 | 51.1% | **2.31** | **+2.97%** | **+1.18%** | 23.8 |

件数は約半減したが 0.2件/日は維持（学習2期間で約150件相当）、質は PF・期待値・α とも大幅改善。
TOML 検証の invalid parameter 警告なし（FILTER_ATTACHED_PARAM_KEYS 登録の確認）。

- **再現コマンド**:
  - `$env:PYTHONPATH="backend"; venv\Scripts\python.exe -m pytest backend/tests/indicators/test_vcp_breakout_filter.py -v`
  - `$env:PYTHONPATH="backend"; venv\Scripts\python.exe tmp\funnel_vcp_pivot_dryup.py`（スイープは `tmp\funnel_sweep.py`）
  - `$env:PYTHONPATH="backend"; venv\Scripts\python.exe backend\backtest\backtest_runner.py --strategy E1_vcp_breakout_mid --start-date 2024-06-01 --end-date 2025-05-31`

## 7. 途中発生した課題

- **Q1(A)「breakout_change 緩和」は単独では無効と判明**: 両ゲート有効時に chg≥4→≥2 へ下げても E1 は 35→41件しか戻らない（クロス条件が「高値近傍での強い上げ」を既に含意するため、大陽線閾値はほぼ冗長化）。効いたのは `base_vol_dry_max` の 1.0→1.2 緩和（35→49〜64件）。デフォルトを dry≤1.2 / chg≥3.0 に設定（どちらも Optuna 探索範囲内なので最適化で追い込める）。
- **全スイートの 1 fail は環境要因**: `test_api_rs_data.py::test_rs_data_availability` は `data/stocktool.db`（本番 DB）を直接開くテストで、ワークツリーには DB が無いため fail。sqlite3.connect が空 DB（139KB）をワークツリー `data/` に生成したが、gitignore 対象で無害。

## 8. スコープ外・残作業

- `breakout_change` の ADR 正規化（評価④）と E1/E2 相互排他化（評価⑤）— 本件の効果測定後に別プランで
- 収縮の持続性・段階性（vcr の N日持続等、評価③）— T3 カラム追加＝サンドボックス案件として v2
- GUI プリセット（`screener_presets*.toml` の E 系）への新パラメータ反映 — 要望があれば TOML 追記のみ
- 変更後の Optuna 再最適化（約12時間）はユーザー実施。**実行前に変更が main に取り込まれているかの確認を必須とする**（2026-07-06 の教訓）
