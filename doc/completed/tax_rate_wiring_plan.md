# 税率（`consider_tax`）が売買戦略に届いていない不具合の修正 計画書

- **ステータス**: ✅ 完了（2026-09-07）
- **実施者**: AI エージェント (Claude Opus 5) — オーケストレーター
- **開始日**: 2026-08-20 / **完了日**: 2026-09-07
- **作業ブランチ**: worktree-tax-rate-wiring
- **関連**: `doc/backend_specification.md` §6.1 / §6.5.1、`doc/issue_list.md`

## 1. 背景と目的

`backtest_config.toml` の `[general] consider_tax` を `20.0` にして
個別銘柄シナリオテストを実行しても、**結果が税なし実行と1円も変わらなかった**
（2026-08-19 実測。5ジョブ × 5モデル = 25組すべてで CAGR・最終資産が完全一致）。

### 原因

税の控除ロジック自体は `scenario_portfolio.py` に正しく実装され、配線も通っている:

```
PortfolioConfig.consider_tax → ScenarioPortfolio.tax → tax_rate
  → apply_exits() で実現益から控除（L324-325 部分利確 / L354-355 全決済）
```

しかし **`run_scenario_test(consider_tax=...)` に実際の値を渡す呼び出し元が1つも無い**。
引数の既定値 `0.0`（`scenario_runner.py:214`）が使われ続けていた。

| 呼び出し元 | `consider_tax` を渡すか |
| :--- | :--- |
| `run_scenario_batch.py:177`（並列バッチ。**実運用で使う経路**） | 渡していない |
| `scenario_runner.py:720`（CLI）— `--tax` 引数自体が存在しない | 渡していない |
| `optimization_market_score.py:50` | 渡していない |
| `scenario_comparison_runner.py:42` | 渡していない |

`[general] consider_tax` を読んでいるのは `backtest_runner.py:487` と
`optimization_runner.py:372/486` のみ、すなわち**型1（最適化バックテスト）だけ**。
**型3（個別銘柄シナリオ）側には設定ファイルからの読み取り経路が最初から存在しなかった。**

> **記録**: 2026-08-16 のセッションで「`[general] consider_tax` はシナリオにも最適化にも
> 効くので、シナリオ限定の仕組みを先に決める必要がある」と説明したが、**これは誤り**。
> 実際は逆で、最適化側にしか効いていなかった。

### 単位の不整合（同時に発覚）

コードは `consider_tax` を**率**として扱う（`pnl * tax_rate` / `pnl * (1.0 - consider_tax)`）。
`etf_single_runner.py` の `--tax` も `0.20` 形式。
一方 `backtest_config.toml` の値は **`20.0`** で、このまま渡ると **2000% 課税**になる。
配線を直すと同時にこの地雷が顕在化するため、**両方を1回で直す必要がある**。

### 成功条件

1. `consider_tax = 0.2` で実行すると、税なし実行に対して**結果が実際に下がる**
2. 不正な単位（`> 1.0`）を渡したら**黙って走らず停止する**
3. 適用税率が**実行ログから確認できる**（「値がある = 効いている」の誤認を防ぐ）

## 2. スコープと設計判断

### 2.1 変更すること

- `backtest_config.toml` の `consider_tax` を `20.0` → `0.2`（率に統一）
- 税率の解決＋検証を**1箇所に集約**し、設定を読む全経路がそれを使う
- 税率を受け取っていなかった4つの呼び出し元すべてに配線
- 適用税率のログ出力

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| `consider_tax` → `tax_rate` への改名 | **今回はやらない。別タスクとして切り出す** | 9ファイル・40箇所超（`etf_single_runner.py` だけで20箇所超）＋ API スキーマ `schemas.py:641` に露出。バグ修正と混ぜると「税が効くようになった」のか「改名で壊れた」のか切り分けられない（ユーザー合意 2026-08-20） |
| どの経路に税を効かせるか | **全売買戦略に反映**（ユーザー判断 2026-08-20）。型1・型2・型3・MTS最適化・レジーム比較のすべて | 最終判断で「SPY との比較を含め、どの戦略が DD と CAGR のバランスを取れるか」を見るため、経路ごとに税の有無が違うと横比較できない |
| SPY ベンチマークへの課税 | **課税しない**（現状維持） | `spy_benchmark_return_pct` は純粋な価格リターン（`scenario_reporter.py:113`）。バイ&ホールドは売却まで課税が繰り延べられるため、**無課税で比較するのが現実的**。ただしこれは高回転戦略に不利に働く実在の差なので、§6 で明記して誤読を防ぐ |
| 税の計算方式 | **実現益に対する即時課税**（現行実装のまま） | 損益通算・繰越控除は実装しない。保守側（税を多めに見積もる）に倒れるため、判断を誤る方向には働かない |
| 最適化の目的関数に税を入れるか | **入れる**（`[general]` なので自動的に効く） | 従来は §6.5.1 に基づき 0.0 だったが、今回ユーザーが全経路反映を選択。**順位が変わらない**ことは 2026-08-16 に全16戦略で実測済みなので、入れても最適化結果は変わらない見込み。§6 で再確認する |

## 3. 変更内容

### 3.1 税率の解決と検証の一本化（新規）

`backend/backtest/common_constraints.py` に追加する。
このモジュールは Phase 1 で「全戦略共通の制約（流動性床）の解決と注入を一本化する」
目的で作られており、同じ役割の税率もここに置くのが自然。

```python
class InvalidTaxRateError(ValueError):
    """税率が率（0.0〜1.0）として不正。20% を 20.0 と書く事故を検出する。"""

def load_tax_rate(config: dict) -> float:
    """backtest_config.toml の [general] consider_tax を率として解決する。

    値は **率**（0.2 = 20%）。1.0 を超える値は単位の取り違え（20.0 = 2000%）
    としてエラーにする。0.0（税なし）は正当な設定として許可する。
    """
    rate = float(config.get('general', {}).get('consider_tax', 0.0))
    if rate < 0.0 or rate > 1.0:
        raise InvalidTaxRateError(
            f"consider_tax は率で指定してください（20% なら 0.2）。現在値: {rate}"
        )
    return rate
```

> **`> 1.0` を弾く根拠**: 税率が100%を超えることは実務上ありえない。
> 一方 `20.0` と書く事故は実際に起きた。**境界を 1.0 に置けば、
> 「率のつもりの正しい値」を弾かず「%のつもりの誤った値」だけを弾ける。**

### 3.2 呼び出し元の配線（4箇所）

| ファイル | 変更 |
| :--- | :--- |
| `run_scenario_batch.py` | `backtest_config.toml` を読んで `load_tax_rate()` で解決し、`run_scenario_test(consider_tax=...)` へ渡す。**既に `tomli` で別の TOML を読んでいるので、混同しないこと**（L45 は `scenario_batch_jobs.toml`） |
| `scenario_runner.py` の CLI | `--tax` 引数を追加（既定は `backtest_config.toml` の値。明示指定で上書き可） |
| `optimization_market_score.py` | `load_tax_rate()` で解決して `run_scenario_test()` へ渡す |
| `scenario_comparison_runner.py` | 同上（L97 付近で既に `app_config` を読んでいるので、そこから解決する） |

既に読んでいる `backtest_runner.py:487` / `optimization_runner.py:372,486` も
**`load_tax_rate()` 経由に置き換える**（検証を全経路で効かせるため）。

`etf_single_runner.py` の `--tax`（既定 `0.0`）も、**未指定時は設定値を既定にする**。

### 3.3 ログ出力

各エントリポイントの実行開始時に1行:

```
[Tax] 適用税率: 20.0% (consider_tax=0.2)     # 0.0 のときは「税なし」と明示
```

> 「設定したのに効いていない」を結果から見抜くのは不可能だった（今回まさにそうだった）。
> **数値を読む前に気づけるようにする**のが目的。流動性床の `applied_filters` と同じ思想。

### 3.4 設定ファイル

`backtest_config.toml` の `consider_tax = 20.0` → `0.2`。コメントで単位を明記する。

## 4. ユーザー確認事項

| 項目 | 判断 |
| :--- | :--- |
| 設定値の単位 | **率に統一（`0.2`）**（2026-08-20） |
| `> 1.0` の扱い | **エラーで停止**（2026-08-20） |
| 適用範囲 | **全売買戦略**（2026-08-20） |
| `tax_rate` への改名 | **別タスク**（2026-08-20） |

**未解決**: なし

## 5. 実装順序と進捗チェックリスト

- [x] `common_constraints.load_tax_rate()` + `InvalidTaxRateError`（テスト先行）
- [x] `backtest_config.toml` を `0.2` へ
- [x] 既存2経路（`backtest_runner` / `optimization_runner`）を `load_tax_rate()` 経由へ
- [x] `run_scenario_batch.py` に配線（**最重要。実運用で使う経路**）
- [x] `scenario_runner.py` CLI に `--tax` を追加
- [x] `optimization_market_score.py` / `scenario_comparison_runner.py` に配線
- [x] `etf_single_runner.py` の `--tax` 既定を設定値由来へ
- [x] 各エントリポイントに税率ログ
- [x] 全テスト（backend）
- [x] 型1（`run_single_strategy`）で税が効くことを実測（§6。複利倍率 18.18 → 8.23）
- [x] **型3（`run_scenario_batch`）の並列 MC で税が効くことを実測**（§6.1。2026-09-07）
- [x] `run_scenario_batch.py` に `--tax` を追加（実測のために本番設定を書き換えずに済ませる）
- [x] 仕様書更新（`backend_specification.md` §6.1 / §6.5.1）
- [x] `issue_list.md` に本件を完了として起票
- [x] 計画書を `doc/completed/` へ移動

### 作業中メモ

- **`run_scenario_batch.py` は既に `tomli` で `scenario_batch_jobs.toml` を読んでいる**（L45）。
  税率は `backtest_config.toml` から読む別物なので、変数名を分けて混同を防ぐこと。
- 並列 MC はサブプロセスで走る（`ProcessPoolExecutor`）。**税率がワーカーまで届くか**を
  必ず実測で確認すること（親プロセスだけ設定されていて子に渡っていない、が起こりうる。
  これは今回のバグとまったく同じ型の失敗）。

## 6. 検証プラン / 結果

### 単体
- `load_tax_rate()`: `0.0` は許可 / `0.2` は通る / `20.0` は `InvalidTaxRateError` /
  `-0.1` はエラー / キー欠如は `0.0`
- 既存の税計算テスト（`test_etf_single_backtest.py` 等）が壊れていないこと

### 結合（**これが本丸**）
短期間・少数戦略で `consider_tax = 0.2` と `0.0` を実行し、**結果が変わること**を確認する。

> **「実行できた」では不十分。今回のバグはまさに「実行できたが効いていない」だった。**
> 最終資産が税なし比で**低下していること**を数値で確認する。
> 変わらなければ配線がまだ切れている。

- 型3（`run_scenario_batch.py`）: 1ジョブ × 1モデル × 2 run 程度で前後比較
- 型1（`backtest_runner.py`）: 1戦略で前後比較
- 型2（`etf_single_runner.py`）: 1銘柄で前後比較

### 結果（2026-08-29 棚卸しで確認）

**実装は全経路に入っている**（コードを1件ずつ確認）:

| 項目 | 確認方法 | 結果 |
| :--- | :--- | :--- |
| `load_tax_rate()` / `InvalidTaxRateError` | `common_constraints.py` | あり（テスト6件） |
| `backtest_config.toml` の `consider_tax` | 設定値 | `0.2`（型1専用の `consider_tax_optimization` は `0.0`） |
| 7経路への配線 | `grep -l load_tax_rate` | `backtest_runner` / `optimization_runner` / `run_scenario_batch` / `scenario_runner` / `optimization_market_score` / `scenario_comparison_runner` / `etf_single_runner` の**全7件** |
| `scenario_runner --tax` | CLI | あり（L717-727） |
| 税率ログ | `grep "\[Tax\]"` | 14箇所 |

**型1で税が効くことを実測**（`tmp/verify_tax_effect.py`。B2 / 2024-06〜2025-12 / 145取引）:

| 税率 | 取引 | 勝率 | 1取引平均 | 複利倍率 |
| ---: | ---: | ---: | ---: | ---: |
| 0.00 | 145 | 44.8% | +12.816% | **18.1793** |
| 0.20 | 145 | 44.8% | +12.816% | **8.2305** |

複利倍率が 18.18 → 8.23 に落ちており、**配線は生きている**。

> [!NOTE]
> **`avg_gain` / 勝率 / PF は税引前のまま**なのは仕様。税は
> `backtest_report.py` L210-211 で `strat_mult`（複利倍率）を積む際に
> 勝ちトレードへ適用される。1トレードの質を表す指標には乗らない。

### 未了: 型3（`run_scenario_batch`）の実測

計画書 §6 が「**これが本丸**」とした並列 MC ワーカーへの伝播が**未確認**。
コード上は `scenario_portfolio.PortfolioConfig` のフィールドとして渡るので
サブプロセスへも pickle される見込みだが、**実測していない**。

さらに棚卸しで分かったこと:

> [!WARNING]
> **現在 `output/scenario/` にある結果（750 run）は、税の配線が入る前のもの。**
> `run_params` に `consider_tax` キーが無い。同キーを書き出すコミットは
> `9f380fc`（2026-08-25 08:36）だが、出力ファイルの更新は **同日 02:09** で
> 6時間半前。**つまり今読めるシナリオ結果は税が効いていない可能性が高い。**
> `consider_tax = 0.2` を前提に読むと成績を過大評価する。
> 型3 のバッチを回し直したときに `run_params.consider_tax = 0.2` が
> 記録されることを確認するのが、そのまま本丸の検証になる。

### 最適化への影響（確認のみ）
2026-08-16 に「税を入れても全16戦略の順位は変わらない」ことを実測済み。
今回 `[general]` 経由で最適化にも税が効くようになるため、
**次回の最適化結果が大きく動かないこと**を確認する（動いたら §2.2 の前提が崩れる）。

### SPY 比較の解釈（仕様書に明記する）
SPY ベンチマークは**無課税のバイ&ホールド**。売却まで課税が繰り延べられる実態を
反映しているが、**高回転の売買戦略には構造的に不利な比較**になる。
「戦略が SPY に負けた」を読むときは、この非対称性を織り込むこと。


## 6.1 型3の実測結果（2026-09-07。本計画の本丸）

### 先に `--tax` を足した理由

`run_scenario_batch.py` は `backtest_config.toml` の `[general] consider_tax` を
読むだけで、CLI での上書きができなかった。実測のたびに**本番設定ファイルを
書き換える**必要があり、戻し忘れると以降の全シナリオテストが税なしになる。

そこで `scenario_runner.py` の前例（L710-720）に合わせて `--tax` を追加した。
CLI 値も `load_tax_rate({'general': {'consider_tax': ...}})` に通し、
**単位検証を共有**している。元のバグが `consider_tax = 20.0`（＝2000%）という
単位の取り違えだったので、CLI だけ検証を素通りさせると同じ穴が開く。

> [!IMPORTANT]
> `resolve_tax_rate()` の判定は **`is not None`** で行っている。
> `if tax_override:` と書くと **`--tax 0.0`（税なし）が「未指定」に化けて
> 設定値が使われ**、比較したつもりが同じ条件を2回走ることになる。
> それはまさに「変わらなかった」という誤った結論を生む経路で、
> 元のバグと同じ型の失敗。テストで固定してある
> （`test_resolve_tax_rate_treats_zero_as_tax_free_not_as_unset`）。

### 実測方法

`main()` は 5モデル × 10run をハードコードしており、税あり/なしで2回回すと
非常に長い。検証したい核心は **`ProcessPoolExecutor` の子プロセスまで税率が
届くか**（Windows は spawn なので親のメモリ空間を共有しない）なので、
`main()` の重い部分は通さず、**同じ `run_single_mc_scenario` を同じ
`ProcessPoolExecutor` で** 1モデル × 2run だけ走らせた（`tmp/verify_tax_type3.py`）。

`monte_carlo_seed=run_idx` なので**両者の乱数系列は同一**。差は税だけに起因する。

### 結果

```
型3 税効果の実測  job=B2 model=full_position runs=2  2024-01-01〜2026-03-26

run 0: 347,495.66 → 250,234.38  (-27.99%)  税が効いている
run 1: 245,864.64 → 183,938.74  (-25.19%)  税が効いている

✅ 2 run すべてで税ありの最終資産が低下。並列MCで税が効いている。
```

元のバグは「5ジョブ × 5モデル = 25組すべてで CAGR・最終資産が**完全一致**」
だった。**明確に異なる挙動**であり、配線は生きていると結論する。

### なぜコード確認で済ませなかったか

配線自体は `executor.submit(run_single_mc_scenario, ..., portfolio, tax_rate)` と
**引数渡し**になっており、読めば正しいと分かる（`run_scenario_batch.py:531-541`）。

それでも実測した理由は、**元のバグが「配線が通っているように見えて効いていなかった」
ものだった**から。同じ確認方法（コードを読んで納得する）で二度目を判定するのは、
一度目と同じ失敗の仕方になる。§6 の警告のとおり:

> **「実行できた」では不十分。今回のバグはまさに「実行できたが効いていない」だった。**

### 再実行の手順

```powershell
$env:PYTHONPATH="backend"
.\venv\Scripts\python.exe backend\backtest\run_scenario_batch.py --jobs B2 --tax 0.0
.\venv\Scripts\python.exe backend\backtest\run_scenario_batch.py --jobs B2 --tax 0.2
```

ログの `[Tax]` 行に**出どころ**（`--tax` か `backtest_config.toml` か）が出るので、
後からどちらの条件で走らせたログかを判別できる。

