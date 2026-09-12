# T5（market_signals）のリフレッシュを Parquet 基点にする 計画書

- **ステータス**: 🚧 進行中（§4 は 2026-09-11 にユーザー判断済み）
- **実施者**: AI エージェント（Claude Opus 5）
- **開始日**: 2026-09-11 / **完了日**: —
- **作業ブランチ**: `worktree-feat+t5-parquet-rebuild`（ワークツリー `.claude/worktrees/feat+t5-parquet-rebuild`、`--mode write` でプロビジョニング済み。分岐元 main `5c7cbe7`）
- **対象 issue**: `doc/issue_list.md`「T5（`market_signals`）のリフレッシュが SQLite 基点のまま — Parquet 基点化が T5 に及んでおらず、リフレッシュのたびに MTS の先頭が壊れる」（以下 **②**）
- **後続**: `doc/in_progress/min_periods_warmup_plan.md`（以下 **①**）の昇格は本計画の完了が前提（① §4-6）

> [!CAUTION]
> **本計画が完了するまで、本番で `--rebuild-from` 系（`tools/deploy_after_merge.ps1` を含む）を実行しないこと。** 実行すると T5 がまた壊れる。

## 1. 背景と目的

T5 を全日付で再計算すると、SQLite の `daily_prices` にある SPY だけ（ホット期間 約503本）で計算するため、窓の先頭で `sma_200` の遡りが足りなくなり、MTS の SPY 由来列が誤った値で保存される。**SPY は 2010-04-01 から Parquet にあり正解の値は存在するのに、誤った値が保存され、再計算しない限り残り続ける。**

2026-08-29 の事故（730日窓で T3 を回して77万行を壊した）を受け、**2026-09-04 の `ae77596` で T3/T4 は Parquet 基点に強制された**。しかし同じ再構築手順の最後の `[4/4] T5`（`update_pipeline.py:71`）だけが SQLite 基点のまま残った。本計画は **Parquet 基点のルールを T5 まで広げ**、本番 MTS の誤りを修復する。

### 1.1 発生条件（確定）

**T5 の全日付再計算時のみ。デイリーでは起きない。**

- デイリー: T3・T5 とも書き込むのは更新日だけ（`t3_indicators.py:36-39` の `date > t3_max` / `t5_signals.py:17-19,88-89` の `gap_dates`）。最新日は窓の末尾で遡りが十分
- 全日付再計算: `orchestrator.py:861` が `active_lvl <= 5` で `market_signals` を全削除 → `t5_signals.py:25` が SQLite の SPY 全行で計算 → 全日付を書き込む → rotate のマージ（`parquet_cache_manager.py:207` の `keep='last'`＝**同一日付は SQLite 側を採用**）で Parquet の正しい値を上書き

T5 全削除が走る経路（`orchestrator.py:812-814,861`）:

| 経路 | `active_lvl` | 実例 |
|---|---:|---|
| `--rebuild-from T3` / `T4`（Parquet 委譲後に `rebuild_from="T5"` で続行。`update_pipeline.py:161-163`） | 5 | 09-04 12:35 / 20:44 |
| `--rebuild-from T5` | 5 | 09-06 23:35 |
| `tools/deploy_after_merge.ps1`（`deploy_after_merge.py:108-114` が `--rebuild-from T3` を呼ぶ） | 5 | — |
| `--rebuild-from T2 --category X` | 2 | — |
| `--re-calculate`（`run/tool/refresh_All.bat`） | 2 | — |

### 1.2 ベースライン（着手前の実測値・2026-09-11）

Parquet 世代 `20260911_145621` に対する実測。

**(a) MTS の SPY 由来列を Parquet 全期間から計算し直し、本番と全列比較**（`tmp/t5_parquet_baseline.py`）

breadth は `t5_signals.py:58-82` のロジックをそのまま Parquet 上で再現。不一致日数（数値列は |差|>1e-6、括弧内は最大|差|）:

| 列 | P1 2010-04〜2018-03 | P2 2018-04〜2024-09-02 | **P3 2024-09-03〜2025-06-30** | P4 2025-07〜 |
|---|---:|---:|---:|---:|
| `market_phase` | 0 | 0 | **38** | 0 |
| `spy_above_sma200` | 0 | 0 | **15** | 0 |
| `spy_sma200_rising` | 0 | 0 | **58** | 0 |
| `distribution_days` | 0 | 0 | **22**（最大6） | 0 |
| `is_distribution_day` / `follow_through_day` | 0 | 0 | 0 | 0 |
| `vxv_vix_ratio` | 0 | 0 | 0 | 0 |
| `market_trend_score` | 0 | 1,336（最大 18.5） | **197（最大 15.5）** | 147（最大 0.17） |
| `breadth_sma50` | 312（本番 NULL） | 1,613（最大 0.424） | 0 | 147（最大 0.004） |
| 期間の日数 | 2,013 | 1,616 | 206 | 301 |

- **SPY 由来の6列は、P1・P2・P4（壊れていない期間）で本番と完全一致。** 壊れているのは P3 だけ
- **P3 の `market_trend_score` 誤差**: 平均 5.31pt / 最大 15.51pt / 5pt 超 92日 / 10pt 超 40日（206日中）

**(b) 2026-08-29 世代（最初のリフレッシュより前に作られた値）と比較**

**SPY 由来の6列は全期間（P1〜P4）で完全一致**。P3 の `market_trend_score` の差は最大 0.089pt（breadth の微差のみ）。→ 8/29 世代は正しく、Parquet 再計算は正しい値を再現できる。

**(c) P2 の `breadth_sma50` 食い違いの原因**（`tmp/t5_breadth_population_check.py`）

| 仮説 | 検証 | 結果 |
|---|---|---|
| 生存者バイアス（現在の `active=1` だけで過去を計算） | 全個別銘柄（`active` 問わず）で再計算 | **否定**。`active=0` は15銘柄のみ、差はほぼ変わらない（平均 0.0027 → 0.0026） |
| **充足前の起点 × `min_periods=1`**（① の問題） | 差の大きい期間を特定 | **確定**。breadth |差|>0.01 は **2018-04-02〜05-29 の33日だけ**、MTS |差|>1pt は 2018-04-02〜05-18 の24日だけ。`sma_50` の窓（50本）で解消する期間と一致 |

価格履歴の充足（2026-09-04）以前は個別銘柄の起点が 2018-04-01 で、当時の T3 の `sma_50` は `min_periods=1` により1本目が終値そのものになり、**2018-04-02 の本番 breadth は `0.000`**（全銘柄で `close > sma_50` が False）。T3 は充足後に Parquet で再計算されたが、T5 の breadth は当時の値のまま Parquet に残っていた。**これも壊れた値であり、全期間再計算で正しい値に戻る**。`has_breadth = date >= '2018-04-01'`（`market_signals.py:174`）のハードコードもこの旧起点に由来する。

**2018-07 以降の差は breadth 最大 0.0024 / MTS 最大 0.110pt・平均 0.022pt** で、許容範囲の微差（T3 再計算の丸め等）。

**(d) いつ壊れたか**（`tmp/spy_mts_generation_check.py` × `logs/pipeline.log`）

| Parquet 世代 | `market_phase` の誤り（2024-08〜2025-06） |
|---|---:|
| `20260829_144251` | **0日** |
| `20260910_145529` | 38日 |
| `20260911_085414` | 38日（デイリー後も変わらず） |

T5 の全日付再計算（`Saved 501〜503 signal records`）は4回: 08-29 16:25（事故）/ **09-04 12:35・20:44（Parquet 基点化の後）** / 09-06 23:35。

### 1.3 完了時の状態（成功条件）

1. `--rebuild-from T3/T4/T5` と `deploy_after_merge` で T5 が Parquet 全期間から計算され、**SQLite の窓に依存しない**
2. 本番 `market_signals` の SPY 由来6列が、Parquet 全期間から計算した値と**全期間で完全一致**（P3 の誤り 0日）
3. 本番 `market_signals` の SPY 由来6列が、**2026-08-29 世代と 2026-08-29 以前の全日付で完全一致**（独立経路の検算）
4. 残る全削除経路（`--rebuild-from T2` / `--re-calculate`）でも、SPY の遡りが足りない日付を**黙って書き込まない**（§4-1）
5. **ワークツリー・`deploy_after_merge` の作業領域から、T3/T4/T5 の再計算が本番 Parquet に書き込めない**（§3.6。`--apply` を試みると `ProductionWriteError`）
6. 「SPY の値が変わる変更は T3 以降のリフレッシュ必須」「T3 以降の再構築は Parquet 基点」がルールとして明記されている
7. memory `t5-sqlite-rebuild-freeze` が解除され、① の §4-6 が「前提充足」に更新されている

## 2. スコープと設計判断

### 2.1 変更すること

1. **`backend/scripts/recompute_parquet_signals.py` を新設** — `recompute_parquet_ranks.py` と同じ形（Parquet を読んで全期間の T5 を計算 → 新世代 publish → ポインタ更新。`--dry-run` / `--apply`、`pipeline_lock`、旧世代は prune しない）
2. **breadth の計算を純関数に切り出す** — `t5_signals.py:58-82` のインライン処理を関数化し、SQLite 経路と Parquet 経路で**同じ関数を使う**（二重実装にしない）
3. **再構築手順を「T3 → T4 → T5 すべて Parquet → SQLite 復元」に変える** — `_rebuild_t3_or_t4_from_parquet` に T5 を加え、SQLite 基点の `[4/4] T5` を撤去。`--rebuild-from T5` 単独も同じ手順に委譲する
4. **残る全削除経路に遡り不足ガードを入れる**（§4-1）
5. **本番 MTS を修復する**（§4-2）
6. **再計算スクリプト3本の隔離を直す**（§7-1・§3.6）— `recompute_parquet_signals.py`（新設）/ `recompute_parquet_indicators.py` / `recompute_parquet_ranks.py` のパス解決を `paths.resolve_db_path_for_init()` に揃え、書き込み前に `paths.ensure_writable()` を掛ける
7. **ルールを明記する** — `.claude/skills/pipeline-debugging/SKILL.md:30`（現状「T4/T5 も連鎖再計算される」とだけある）ほか

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 |
|---|---|
| **方式: T5 の SPY 入力だけ Parquet から補う（SQLite 基点のまま）** | **不採用。** P3 は直せても、SQLite 窓の外にある 2018-04〜05 の breadth の壊れ（§1.2(c)）は直せない。T3/T4 と同じ「Parquet で全期間を計算して publish」の形に揃える方が一貫する（2026-09-11 合意「Parquet 基点のルールを T5 まで広げる」） |
| **修復: 2026-08-29 世代から壊れた範囲だけ書き戻す** | **不採用。** 8/29 以降の T2 修正（例: `vxv_vix_ratio` が 8/29 世代と P4 で12日食い違う＝VIX データが後から直っている）を反映できない。部分パッチは「どの範囲がどの世代由来か」の二重管理になる。8/29 世代は**検算用**に使う |
| **breadth の母集団を「その日時点の active」にする** | **しない。** 現在の `active=1` で全期間を再計算する（`recompute_parquet_ranks.py` と同じ方針）。生存者バイアスの影響は `active=0` が15銘柄で、実測で無視できる（§1.2(c)） |
| **2018-04〜05 の breadth 変化（MTS 最大 18.5pt）を避ける** | **避けない。** 当時の壊れた値（① の旧起点 × `min_periods=1`）が正しい値に戻るもので、修正にあたる。バックテストの学習期間（2022 / 2024-06〜2025-12）・検証窓（最古 2018-10）の外 |
| **`has_breadth = date >= '2018-04-01'` のハードコードを直す** | **今回はしない。** 旧起点に由来するガードだが、撤去すると 2017-01〜2018-03 の MTS が3成分→4成分に変わる。① §8 の「既存ガードの撤去」で扱う |
| **`--rebuild-from T2` の T3 が SQLite 基点のまま** | **本計画では扱わない。** T2 のカテゴリ再取得後の T3 も同じ構図の可能性があるが、T5 の問題ではない。§8 に残す |

## 3. 変更内容

### 3.1 breadth の純関数化（`pipeline/phases/t5_signals.py`）

`t5_signals.py:58-82` の「個別銘柄の `close` / `sma_50` / 前日 `close` → 日付別の `breadth_sma50` / `momentum_ratio`」を関数に切り出す。置き場所は `indicators/market_signals.py`（T5 の純関数が集まる場所）を想定。**SQLite 経路の出力は1ビットも変えない**（テストで担保）。

### 3.2 `recompute_parquet_signals.py`（新設）

1. Parquet 最新世代から読む: SPY / `^VIX` / `^VIX3M` の価格、`active=1` かつ `個別` の `close` と `sma_50`
2. §3.1 の関数で breadth を作り、`calculate_market_signals()` で全期間の T5 を計算
3. `--dry-run`: 現行世代との差分を期間別・列別に表示（§1.2(a) と同じ形式）。**書き込まない**
4. `--apply`: `market_signals` だけ新しい行で差し替えた新世代を publish（他テーブルは同世代へコピー。`recompute_parquet_ranks.py:121-149` と同じ）→ ポインタ更新

**`id` 列と `created_at` 列の扱い**は、現行 Parquet の `market_signals` スキーマ（`id, date, ..., created_at`）に合わせる。

### 3.3 再構築手順（`scripts/update_pipeline.py`）

| | 現行 | 変更後 |
|---|---|---|
| 1 | Parquet で T3 | Parquet で T3（T3 指定時） |
| 2 | Parquet で T4 | Parquet で T4 |
| 3 | SQLite 復元 | **Parquet で T5** |
| 4 | **`run_pipeline(rebuild_from="T5")` → SQLite で T5 → rotate** | SQLite 復元（`market_signals` も Parquet から入る。`parquet_cache_manager.py:709-712`） |

- **`[4/4]` の `run_pipeline` 呼び出しは撤去する。** T5 も Parquet で完結し SQLite は復元済みなので、rotate（SQLite→Parquet のマージ）は不要。`rebuild_from=None` で `run_pipeline` を回す案は採らない — `--skip-fetch` が無いと、yfinance で SPY の最新日を確認したうえで最新日のデータをクリアして取り直す自動モード（`orchestrator.py:777-808`）に入りうるため。`deploy_after_merge` は `--skip-fetch` を付けるので入らないが、手で `--rebuild-from` を回す経路では入る
- `--rebuild-from T5` 単独も委譲する（`update_pipeline.py:156` の条件に `T5` を加え、T3/T4 をスキップして T5 だけ Parquet で再計算 → 復元）

### 3.4 遡り不足ガード（`pipeline/phases/t5_signals.py`）— §4-1

`gap_dates` のうち、SQLite にある SPY の本数がその日までに **220本**（`sma_200` の200本＋`spy_sma200_rising` の20日前比較）に満たない日付があれば、**例外で止める**。メッセージで `--rebuild-from T5`（Parquet 基点）を案内する。

- **220 の根拠**: `market_signals.py:111-117` のコードから導出（`rolling(200)` と `shift(20)`）。決め打ちではないが、MTS の窓を変えたら追随が要るので、定数化してコードの近くに置く
- デイリーは `gap_dates` が最新日だけで遡りは約503本あるため、**止まらない**
- 止まるのは `--rebuild-from T2 --category` / `--re-calculate` の全削除経路だけ

### 3.5 ルールの明記

- `.claude/skills/pipeline-debugging/SKILL.md:30` — 「`--rebuild-from T3/T4/T5` は Parquet 基点で全期間を再計算する」「SPY の値が変わる変更は T3 以降のリフレッシュ必須」を書き足す。編集後 `tools/sync_skills.py --apply`
- `doc/architecture.md` / `doc/backend_specification.md` の T5 の記述（`backend_specification.md:482` 等）

### 3.6 再計算スクリプトの隔離（§7-1・5-6b）

対象3本（`recompute_parquet_signals.py` / `recompute_parquet_indicators.py` / `recompute_parquet_ranks.py`）は、いずれも `config.toml` の `db_path` を直読みして Parquet ディレクトリを決めている。これを**パイプライン本体と同じ解決順**に揃える。

| | 現行 | 変更後 |
|---|---|---|
| パス解決 | `config["system"]["db_path"]` をそのまま使う | `paths.resolve_db_path_for_init("stocktool", config["system"]["db_path"])`（`db/database.py:35` の `init_db` と同じ。環境変数 `STOCKTOOL_DB_PATH` → ワークツリーの `config.local.toml` → config.toml の順に解決する） |
| 書き込み前の防御 | なし（`ensure_writable` は `init_db` でしか呼ばれない） | `--apply` の書き出し前に `paths.ensure_writable(parquet_dir)` |

**これにより `deploy_after_merge` の作業領域（環境変数で指定）と、ワークツリーの sandbox（`config.local.toml`）が、T3/T4/T5 の再計算でも効くようになる。**

> [!IMPORTANT]
> **副作用: `--dry-run` の参照先も解決結果に従う**（2026-09-12 検収で実測・§7-2）。修正前はワークツリーからでも `config.toml` 経由で**本番**を読んでいたが、修正後は**ワークツリーの sandbox 世代**を読む。
> - **5-6 の反証は修正前の挙動（本番を直接読む）で実施済み**なので、その結果は有効
> - **本番を対象にした再計算・差分確認は、本体チェックアウトから実行する**（5-14）。`deploy_after_merge.ps1` はワークツリーからの実行を precheck で弾く作りなので、運用上も本体から実行される

## 4. ユーザー確認事項

**2026-09-11 に全件ユーザー判断済み。** 未解決の確認事項はなし。

| # | 論点 | 推奨 | **判断** |
|---|---|---|---|
| **4-1** | 残る全削除経路（`--rebuild-from T2 --category` / `--re-calculate`）の防御 | 遡り不足の日付があれば例外で止める（§3.4）。足りない分を Parquet から自動で補うと、SQLite 経路に別経路の値を黙って混ぜることになる | **OK** |
| **4-2** | 本番 MTS の修復方法とタイミング | merge 後、API サーバを止めて `tools/deploy_after_merge.ps1 -RebuildFrom T5`（health check・NG 時ロールバック付き）。**実行前に dry-run の差分を提示して確認を取る**。変わるのは P3 の38日（MTS 最大 15.5pt）と 2018-04〜05 の33日（最大 18.5pt）、他は 0.11pt 以内 | **OK** |
| **4-3** | 型3 シナリオの再評価 | 修復後に再実行して before/after を記録する（成績は差し戻し基準にしない） | **実行する。**「型3 は Parquet しか見ないので SQL のずれは影響ないと思う」とのユーザー見立て。**コード確認で見立ては支持された**（理由は §4.1）。したがって**期待値は「変化なし」**で、再実行は影響が無いことの答え合わせとして行う |
| **4-5** | 再計算スクリプトの隔離の穴（§7-1。検収で発見） | 本計画に含める（5-6b） | **本計画で実施する。**「抜け漏れると他のタスクに影響しそう」 |
| **4-4** | SPY の自前計算（`market_signals.py:111-112,127`）を `min_periods=window` に揃えるか（① から持ち越し） | 揃える。変わるのは 2010-04〜2011-02 の先頭219本だけ。`distribution_days` の `.astype(int)` と `spy_above_sma200` の NaN 潰しも併せて直す | **OK（揃える）** |

### 4.1 型3 が ② の影響を受けない理由（4-3 のコード確認・2026-09-11）

**補足: 壊れは SQLite だけでなく Parquet の `market_signals` にも入っている**（rotate のマージで SQLite 側が採用されるため。§1.2(a) の38日は Parquet を読んで測った値）。したがって「Parquet しか見ない」だけでは影響の有無は決まらず、**型3 が `market_signals` のどの列をどこから読むか**で決まる。

| 型3 が使う値 | 取得元 | ② の影響 |
|---|---|---|
| SPY 由来の MTS（phase・スコアの SMA 成分） | `MarketTrendScorer` が **T3 の `sma_50`/`sma_200`/`atr_14` から自前計算**（`scenario_market_score.py:77-79,113-114`）。`market_signals` の `market_phase` 等は読まない | **なし**（T3 は Parquet 基点で正しい） |
| `breadth_sma50` / `vxv_vix_ratio` | **SQLite の `market_signals`**（`scenario_runner.py:293-299`）。SQLite に無い日付（730日より前）は Parquet から自前で計算して補う（`scenario_runner.py:308-338`） | **なし**。SQLite の範囲（P3・P4）ではこの2列は壊れていない（§1.2(a) で P3 不一致 0日）。2018-04〜05 の壊れた breadth は SQLite に無いので自前計算で補われ、正しい値になる |

**修復後に変わりうるのは P4 の breadth の微差（最大 0.004）だけ**なので、型3 の成績は実質変わらない見込み。**大きく変わった場合は想定外の読み取り経路があることを意味するので、原因を調べる。**

## 5. 実装順序と進捗チェックリスト

影響の小さい順・テスト先行（TDD）。

- [x] **5-1** ワークツリーを作成し `--mode write` でプロビジョニング（`tools/provision_worktree_data.py <worktree> --mode write`）。本計画書をワークツリーへ移してコミット
- [x] **5-2** breadth 純関数のテスト（red）— 既存の SQLite 経路と同じ入力で同じ出力になること
- [x] **5-3** `t5_signals.py:58-82` を純関数に切り出す（green。SQLite 経路の挙動不変）
- [x] **5-4** `recompute_parquet_signals.py` の計算部分のテスト（red）
- [x] **5-5** `recompute_parquet_signals.py` を実装（`--dry-run` / `--apply`）
- [x] **5-6** **§6.1 の反証**（2026-09-12 オーケストレーター実施。**§1.2(a) と全セル一致で合格**） — ワークツリーから本番 Parquet を読み取り専用で `--dry-run` し、差分が §1.2(a) のベースライン（P3 の SPY 由来列 38/15/58/22日、他期間 0日）と一致するか照合。**一致しなければ実装に進まず原因を調べる**
- [x] **5-6b** **再計算スクリプト3本の隔離を直す**（§3.6・§7-1）— パス解決を `paths.resolve_db_path_for_init()` に揃え、`--apply` の書き込み前に `paths.ensure_writable()`。テストで「ワークツリーから `--apply` すると `ProductionWriteError`」「`--dry-run` は本番を読める」を担保。**5-11・5-14 の前提**
- [x] **5-7** 遡り不足ガード（§3.4）のテスト → 実装（§4-1）
- [x] **5-8** `update_pipeline.py` の再構築手順を変更（§3.3）+ テスト
- [ ] **5-9**（§4-4 が「揃える」なら）SPY 自前計算を揃え、`distribution_days` / `spy_above_sma200` の NaN 対策 + テスト
- [ ] **5-10** pytest 全件パス
- [ ] **5-11** sandbox で `--rebuild-from T3` を実行 → **sandbox の `market_signals` の SPY 由来6列が Parquet 全期間計算と全期間で完全一致**すること、`db_health_check.py --all --check-nulls` が通ること
- [ ] **5-12** ルールの明記（§3.5）+ `sync_skills.py --apply`
- [ ] **5-13** 仕様書の T5 記述を更新
- [ ] **5-14** **本体チェックアウトから実行する**（ワークツリーからは sandbox を指すため — §7-2）。先に 5-16 の「修復前」の型3 シナリオを実行して記録しておく → merge → **dry-run の差分をユーザーに提示して確認** → API サーバ停止 → `tools/deploy_after_merge.ps1 -RebuildFrom T5` → API サーバ再起動（§4-2）
- [ ] **5-15** 本番の修復確認 — 成功条件 2・3（Parquet 全期間計算と完全一致 / 8/29 世代と 8/29 以前で完全一致）
- [ ] **5-16** 型3 シナリオの再評価（§4-3）— 修復前（5-14 の前）と修復後で同じ条件で実行し before/after を記録。**期待値は「変化なし」**（§4.1）。大きく変わったら想定外の読み取り経路を調べる
- [ ] **5-17** 後片付け — issue ② をクローズ、memory `t5-sqlite-rebuild-freeze` を削除（MEMORY.md の索引も）、① の §4-6 を「前提充足」に更新
- [ ] **5-18** 計画書を `doc/completed/` へ移動

### 作業中メモ

**現在地: 5-2〜5-8 実装済み。5-9 以降に着手可能。**

- **5-8 の実装**: `update_pipeline.py` の `_rebuild_t3_or_t4_from_parquet()` を `_rebuild_from_parquet()` に改名し、手順を「Parquet で T3（T3 指定時のみ）→ Parquet で T4（T3/T4 指定時のみ）→ Parquet で T5（`recompute_parquet_signals.run(dry_run=False)`。常に実行）→ SQLite 復元」の4段に変更（`market_signals` も Parquet から復元されるので追加の rotate は不要）。旧来の「`[4/4] T5 を再計算して rotate`」というログだけで実処理の無かった箇所と、`rebuild_from = "T5"` に差し替えて `run_pipeline()` を続行していた撤去対象の処理を、新設の `_run_rebuild_or_pipeline()` に置き換えた。`_run_rebuild_or_pipeline()` は `rebuild_from` が T3/T4/T5 のいずれかなら `_rebuild_from_parquet()` に委譲して**そのまま return**し（`run_pipeline()` を一切呼ばない）、それ以外（`T2` や指定なしの通常実行）は従来どおり `run_pipeline()` を呼ぶ。`--rebuild-from T5` 単独も同じ分岐（`("T3", "T4", "T5")` の条件）に入り、`_rebuild_from_parquet("T5", logger)` 内で T3/T4 の再計算だけがスキップされる（`level in ("T3","T4")` の判定で T4 も回さない）
- **これにより §7-3 で確認された「5-8 が入るまで `--rebuild-from T3/T4/T5` は最後の `[4/4] T5` で意図的に `RuntimeError` になる」状態は解消された。** T5 は SQLite ではなく Parquet 全期間から計算されるため、5-7 の遡り不足ガード（`sync_phase_t5_signals()` 側）を通らない
- テスト: `backend/tests/scripts/test_update_pipeline_rebuild.py`（新設・7件）。`recompute_parquet_indicators.run` / `recompute_parquet_ranks.run` / `recompute_parquet_signals.run` / `run_production_restore.run_production_restore` / `pipeline.orchestrator.run_pipeline` をそれぞれモックし、`_run_rebuild_or_pipeline()` を直接呼んで呼び出し順序・`run_pipeline` が呼ばれないことを検証（実パイプライン・実再計算は一切実行しない）

- **5-7 の実装**: `indicators/market_signals.py` に定数 `SPY_LOOKBACK_MIN_BARS = 220` を追加（`sma_200` の `rolling(200)` ＋ `spy_sma200_rising` の `shift(20)` で 200+20=220 本必要という根拠をコメントに明記。MTS の窓を変えたら追随が要る旨も明記）。`t5_signals.py` の `sync_phase_t5_signals()` で `spy_df['date'] = pd.to_datetime(...)` の直後・`calculate_market_signals()` を呼ぶ前に、`gap_dates` の**最古日付**について「その日までの SQLite の SPY 本数」を数え、220本未満なら `RuntimeError` で止める（既存の `t2_prices.py:65` / `orchestrator.py:794` と同じ型・同じ「日本語の理由＋対処法を1つのメッセージに入れる」スタイル）。メッセージには不足日付・実本数・必要本数・原因（SQLite はホット期間のみ）・対処（`--rebuild-from T5` など Parquet 基点の手順）を含める
- **「最古の gap 日付だけ見れば十分」の根拠**: SPY の日付は連続して増える一方なので、ある日付までの SQLite 行数はその日付が新しいほど多い（単調非減少）。したがって `gap_dates` の中で最も遡りが浅い＝最も条件が厳しいのは必ず最古の日付であり、そこが220本以上ならそれより新しい gap 日付は全て220本以上を満たす
- テスト: `backend/tests/pipeline/test_t5_lookback_guard.py`（新設・4件）— 遡り不足で例外・境界（220本ちょうどで通過／219本で例外）・日次相当（最新日のみ gap・503本）で通過、を担保。既存の `test_pipeline_idempotency.py::test_sync_phase_t5_backfills_null_score` は、過去250日ぶんを「未計算」のまま（`MarketSignal` レコードなし）にしていたため gap_dates が250日全部に広がり、最古日付では SPY の遡りが1本しかなく新ガードに引っかかっていた。テストの主眼（既存レコードの NULL スコアを埋める）とは無関係な副作用なので、過去250日ぶんに完了済みダミー `MarketSignal`（`market_trend_score=50.0`）を追加し、gap_dates が対象日1件だけになるよう修正した（フィクスチャの是正であり、ガードの仕様やテスト対象ロジックは変えていない）

- 5-2〜5-5 の実装: `indicators/market_signals.py` に `compute_breadth_momentum(raw_df)` を追加し `t5_signals.py` から呼ぶ形に置換。`scripts/recompute_parquet_signals.py` を新設（`build_market_signals_frame()` / `compare_by_period()` を I/O から分離）。テスト `tests/indicators/test_market_signals_breadth.py` / `tests/scripts/test_recompute_parquet_signals.py`
- 検収で発見: **§7-1（再計算スクリプトが本番 Parquet に書き込める）** → **5-6b で対応済み**
- **5-6b の実装**: `recompute_parquet_signals.py` / `recompute_parquet_indicators.py`（`run()`・`verify()` の両方）/ `recompute_parquet_ranks.py` の3本で、`db_path = config["system"]["db_path"]` を `db_path = paths.resolve_db_path_for_init("stocktool", config["system"]["db_path"])` に置換。`--apply` の書き込みガード（`paths.ensure_writable(parquet_dir)`）は**「dry-run 早期リターンの直後・書き出しループの直前」**に置いた（計画の「書き出す直前」を、実データを読み込む前ではなく読み込んだ直後・重い書き込み処理の直前、という位置で満たす形。`recompute_parquet_indicators.py` はこれにより「本番へ書けない状態で全銘柄のチャンク再計算を実行してから拒否される」無駄を避けられる）。`verify()`（`--verify`、読み取り専用）はパス解決のみ直し、`ensure_writable` は呼んでいない
- テスト: `tests/scripts/test_recompute_parquet_signals.py::TestRunPathIsolation`（3件）/ `tests/scripts/test_recompute_parquet_indicators.py`（新設、4件）/ `tests/scripts/test_recompute_parquet_ranks.py`（新設、3件）。いずれも `paths.get_repo_root` を偽の worktree にモンキーパッチし、実行環境の実際の worktree/本体判定に依存しない決定的なテストにしてある（main へ merge 後に実行しても同じ結果になる）
- **判明した環境依存の注意点（コードは無関係、テスト実行時のみ）**: `recompute_parquet_ranks.py` の出力に含まれる `≈`（U+2248）が、非対話コンソールへのリダイレクト時に Python が cp932 にフォールバックすると `UnicodeEncodeError` になる（`doc/agent_execution_rules.md` §該当の既知事象）。pytest 実行時は `PYTHONIOENCODING=utf-8`/`PYTHONUTF8=1` を付けること。スクリプト自体は変更していない（対象外）
- sandbox の Parquet は最新世代 `20260911_145621` のみ（ハードリンク）。**2026-08-29 世代は sandbox に無い**ので、5-15 の検算は本番 `data/parquet_master/market_signals_20260829_144251.parquet` を読み取り専用で読む
- **2026-08-29 世代 `market_signals_20260829_144251.parquet` を prune しないこと**（5-15 の検算に使う）
- ベースライン測定スクリプトは本体の `tmp/` にある（`t5_parquet_baseline.py` / `t5_breadth_population_check.py` / `spy_mts_window_check.py` / `spy_mts_generation_check.py`）

## 6. 検証プラン / 結果

| 検証 | 方法 | 期待値 |
|---|---|---|
| 単体 | pytest（5-2〜5-9 で追加したもの含む全件） | 全件パス |
| breadth 純関数 | 切り出し前後で SQLite 経路の出力を比較 | 完全一致 |
| dry-run 差分 | 本番 Parquet に対して `--dry-run`（5-6） | §1.2(a) と一致（P3 の SPY 由来列のみ不一致） |
| sandbox 再構築 | `--rebuild-from T3` 後の sandbox `market_signals`（5-11） | SPY 由来6列が Parquet 全期間計算と完全一致 |
| ガード | SPY の遡りが220本未満の `gap_dates` で T5 を回す | 例外で止まる / デイリー相当では止まらない |
| 隔離 | ワークツリーから再計算スクリプトを `--apply`（5-6b） | `ProductionWriteError` で拒否。`--dry-run` は従来どおり本番を読める |
| 本番修復 | 修復後の本番 `market_signals`（5-15） | 成功条件 2・3 |
| 型3 再評価 | 修復前後で型3 シナリオを同条件で実行（5-16） | **変化なし**（P4 breadth の微差程度）。§4.1 |
| データ整合 | `db_health_check.py --all --check-nulls` | NG なし |

### 6.1 反証 — この結論が誤りだとしたら、何が観測されるはずか

- **この結論が誤りだとしたら観測されるはず**:
  結論は「本番 MTS の誤りは SQLite 窓の SPY に由来し、Parquet 全期間から計算し直せば直る」。これが誤りなら:
  1. **壊れていないはずの期間でも、Parquet 再計算が本番と食い違う**（計算ロジック自体が本番と違う）
  2. **P2 の breadth の食い違いが 2018-04〜05 に集中しない**（① の旧起点では説明できず、別の原因がある）
  3. **8/29 世代（リフレッシュ前の値）とも食い違う**（Parquet 再計算の方が誤っている）

- **独立経路での確認**: **着手前に実施済み（2026-09-11）**
  1. → **否定。** SPY 由来6列は P1・P2・P4 で本番と**完全一致**（§1.2(a)）
  2. → **否定。** breadth |差|>0.01 は 2018-04-02〜05-29 の33日だけ、2018-07 以降は MTS 最大 0.110pt（§1.2(c)）。生存者バイアス仮説も否定済み
  3. → **否定。** 8/29 世代と SPY 由来6列が**全期間で完全一致**（§1.2(b)）。**8/29 世代は問題の経路（SQLite 全削除）を通る前に、別の時点・別の経路で作られた値**なので、Parquet 再計算とは独立した情報源になっている
  
  残る検証は「実装が §1.2 の手作業の再現と同じ結果を出すか」で、これは 5-6（dry-run）と 5-15（本番修復後）に置いた。

### 6.2 転記の完全性

- **転記元**: `doc/issue_list.md` の ②（2026-09-11 起票）
- **元の件数**: 見出し **10項目**（CAUTION / 事象 / 発生条件 / 原因 / 実測 / いつ壊れたか / 影響 / 対応案 / 修復の手がかり / 関連）。うち対応案のサブ項目 **4件**（T5 用の Parquet 再計算を用意 / SPY 変更時は T3 以降リフレッシュ必須 / `pipeline-debugging/SKILL.md:30` の書き足し / SPY 自前計算を揃える場合はこの issue で扱う）
- **本計画書の件数**: **10項目**（CAUTION→冒頭 / 事象→§1 / 発生条件→§1.1 / 原因→§1 / 実測→§1.2(a) / いつ壊れたか→§1.2(d) / 影響→§4-3 / 対応案→§2.1・§3 / 修復の手がかり→§2.2・作業中メモ・5-15 / 関連→ヘッダー）＋ **対応案サブ 4件**（1→§3.2 / 2→§3.5 / 3→§3.5 / 4→§4-4）
- **差分の説明**: なし。**issue 起票後の追加調査で3件増えた**（issue に無く本計画書にだけあるもの）: (1) T5 全削除の経路が5つあること（§1.1）(2) P2 の breadth 食い違いと、その原因が ① の旧起点であること（§1.2(c)）(3) 8/29 世代との全列比較（issue は `market_phase` のみ）（§1.2(b)）。5-17 で issue をクローズする際に要約して残す

## 7. 途中発生した課題

### 7-1. 再計算スクリプトが sandbox 隔離を経由せず、本番 Parquet に書き込める（2026-09-12・5-2〜5-5 の検収で発見）✅ 5-6b で対応済み

- **事象**: `recompute_parquet_signals.py`（新設）は、姉妹スクリプト `recompute_parquet_indicators.py`（L125-127, L244-247）・`recompute_parquet_ranks.py`（L65-68）と同じく **`config.toml` の `db_path`（本番の絶対パス）を直接読んで Parquet ディレクトリを決める**。環境変数 `STOCKTOOL_DB_PATH` もワークツリーの `config.local.toml` も見ない。Parquet の書き込みには `paths.ensure_writable()` が掛かっていない（`ensure_writable` は `db/database*.py` の `init_db` でしか呼ばれない）
- **発見の経緯**: ワーカー（implementer）が完了報告で「計画の『同じ形で作る』に従った結果、ワークツリーから `--apply` すると本番に書ける」と自己申告。オーケストレーターがコードで確認した
- **影響（コード上の経路。過去の実行で実際に書き込まれたかは未確認）**:
  1. **`tools/deploy_after_merge.ps1 -RebuildFrom T3/T4` が、作業領域での再生成の段階で本番 Parquet に T3/T4 の新世代を publish し、ポインタを書き換える。** `deploy_after_merge.py:98-99` は環境変数で作業領域を指すが、`update_pipeline.py` → `_rebuild_t3_or_t4_from_parquet` → `recompute_parquet_*.run()` が環境変数を無視するため。**health check と NG 時ロールバックが T3/T4 には効いていない**（2026-09-04 の `ae77596` 以降）
  2. **ワークツリーから `--rebuild-from T3/T4` を実行すると本番 Parquet に書き込む。** 本計画の **5-11（sandbox で `--rebuild-from T3`）はこのままでは実行できない**
  3. 本計画の 5-14（`deploy_after_merge -RebuildFrom T5`）と、① の昇格（`-RebuildFrom T3`）も 1 と同じ経路を通る
- **原因**: 計画書 §3.2 が「`recompute_parquet_ranks.py` と同じ形」を指示し、姉妹スクリプトの隔離の穴を引き継いだ（計画の穴）
- **直し方の候補**: 再計算スクリプト3本のパス解決を `init_db` と同じ `paths.resolve_db_path_for_init("stocktool", config_db_path)` に揃え、`--apply` の書き込み前に `paths.ensure_writable()` を掛ける
- **判断（2026-09-12 ユーザー）**: **本計画に含める。**「抜け漏れると他のタスクに影響しそう」——① の昇格も同じ経路を通るため。対応は **5-6b** として 5-7 の前に置く

### 7-2. 隔離修正の副作用: `--dry-run` の参照先が本番から sandbox に変わった（2026-09-12・5-6b の検収で実測）

- **事象**: 5-6b でパス解決を `paths.resolve_db_path_for_init()` に揃えた結果、**ワークツリーから `--dry-run` を実行すると sandbox の世代を読む**ようになった（実測: 現行世代として sandbox の `market_signals_20260911_145621.parquet` を表示。本番の最新は `market_signals_20260912_145123.parquet`）
- **計画との差**: §3.6 には「`--dry-run` は従来どおり本番を読めるようにする」と書いていたが、パス解決を一本化する以上そうはならない。**隔離としてはこちらが正しい**ため、計画側の記述を実態に合わせて修正した
- **影響**: (1) **5-6 の反証は修正前に実施済み**で、本番を直接読んだ結果なので有効。(2) **5-14（本番の修復）は本体チェックアウトから実行する**。(3) 5-11（sandbox での確認）はむしろこの修正で安全に実行できるようになった

### 7-3. 5-7 のガード投入で、5-8 が入るまで `--rebuild-from T3` は意図的に落ちる（2026-09-12・5-7 の検収時に確認）

- **事象**: 5-7 の遡り不足ガードは `sync_phase_t5_signals()` に入れたため、**5-8（再構築手順を Parquet 基点にする）が入るまでの間、`--rebuild-from T3/T4/T5` は最後の `[4/4] T5` で `RuntimeError` で止まる**。SQLite の SPY は約503本しかなく、全削除後の最古 gap 日付では 220本に届かないため
- **これは設計どおり**（壊した値を書くより止める。§4-1）。5-8 で T5 が Parquet 基点になれば、この経路自体が無くなる
- **引き継ぎ上の注意**: このブランチの途中状態で `--rebuild-from` を試すと落ちるが、不具合ではない。そもそも本計画では ② が直るまで本番で `--rebuild-from` を実行しない方針（冒頭の CAUTION）
- **副作用として既存テストを1件修正した**: `test_pipeline_idempotency.py::test_sync_phase_t5_backfills_null_score` は過去250日を未計算のまま投入しており、ガードに掛かるようになった。過去分を「計算済み」として投入する形に直した。**テストの主眼（NULL スコアが埋まること）の assert は変えていない**ことを検収で確認済み

### 7-4. `run_production_restore()` も本番を決め打ちする（2026-09-12・5-8 の検収で発見）🔴

- **事象**: 再構築手順のステップ4で呼ぶ `run_production_restore()` は**引数なし**で、その場合の復元先は
  `<チェックアウトのルート>/data/stocktool.db` の**決め打ち**（`run_production_restore.py:14,123`）。
  さらに環境変数 `STOCKTOOL_DB_PATH` を**意図的に解除する**（同 L127。「parquet は A・書き込み先は B」の
  不整合を防ぐための既存の措置）。`paths.py` は使っていない。復元先 DB の隣の `parquet_master/` を読む
  （`parquet_cache_manager.py:626` の `get_parquet_master_dir(db_path)`）ため、**復元先の決め方がそのまま
  参照する Parquet を決める**
- **5-8 が入れたものではない**。旧コードもステップ3で同じ呼び方をしていた。§7-1 と同じ家系の穴で、
  5-6b では再計算スクリプト3本しか塞いでいなかった
- **影響**:
  1. **本体チェックアウトから実行すると、`deploy_after_merge` が作業領域を環境変数で指定していても
     本番 SQLite を削除して復元する**（health check 前に本番が書き換わる）
  2. **ワークツリーから実行すると、プロビジョニング済みの sandbox（`data/sandbox/stocktool.db`）ではなく
     `<worktree>/data/stocktool.db` を対象にする** → **5-11 の sandbox 確認が意図した場所に効かない**
- **直し方の案**: 5-6b と同じ方針。`_rebuild_from_parquet()` から**解決済みの db_path を明示的に渡す**
  （`run_production_restore(db_path=paths.resolve_db_path_for_init("stocktool", ...))`）。関数側の
  「環境変数を解除する」防御は**そのまま残す**（引数で明示されたパスを正とする設計と整合する）
- **状態**: 本計画に含めるか（**5-8b** とする）、別 issue にするか、**ユーザー判断待ち**。
  **5-11・5-14 の前提**なので、判断が出るまでそこには進まない

## 8. スコープ外・残作業

- **`--rebuild-from T2 --category` の T3 が SQLite 基点のまま** — T2 のカテゴリ再取得後の T3 も、2026-08-29 の事故と同じ構図の可能性がある。T5 は §3.4 のガードで止まるが、T3 は未確認。別 issue 候補
- **`has_breadth = date >= '2018-04-01'` のハードコード** — 旧起点に由来。① §8 の「既存ガードの撤去」で扱う
- **日次の T5 でも `market_trend_score` が NULL の過去日付は `gap_dates` に入る**（`pipeline-debugging/SKILL.md:32`「NULL 欠損は過去に遡ってバックフィル」）。SQLite 窓の先頭付近の日付が NULL だと遡りが足りない。§3.4 のガードで止まるので黙って壊れはしないが、止まったときの復旧手順（`--rebuild-from T5`）を SKILL.md に書く
