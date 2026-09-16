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

> [!IMPORTANT]
> **2026-09-13 に方針変更（§7-7・§4-8・5-7c）**: 例外で止めるのではなく、**遡りが足りない日付を書き込み対象から外し、`logger.error` で警告する**。
> 理由: 例外で止める形だと、ホット期間の古い日付に `market_trend_score` NULL が1件あるだけで日次更新が毎晩落ち、rotate に到達せず Parquet の更新が止まる。
> **以下の記述は変更前のもの**（経緯として残す）。

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

### 3.7 復元先の明示（§7-4・5-8b）

`_rebuild_from_parquet()` のステップ4で呼ぶ `run_production_restore()` は**引数なし**だと
`<チェックアウトのルート>/data/stocktool.db` を決め打ちする（`run_production_restore.py:14,123`）。
5-6b で再計算スクリプト3本のパス解決を揃えたので、**その解決結果を復元にも渡す**。

| | 現行 | 変更後 |
|---|---|---|
| 復元先 | `run_production_restore()`（引数なし → 決め打ち） | `run_production_restore(db_path=<5-6b と同じ解決結果>)` |
| 環境変数の解除（同 L127） | あり | **そのまま残す**（引数で明示されたパスを正とする設計と整合） |

## 4. ユーザー確認事項

**2026-09-11 に全件ユーザー判断済み。** 未解決の確認事項はなし。

| # | 論点 | 推奨 | **判断** |
|---|---|---|---|
| **4-1** | 残る全削除経路（`--rebuild-from T2 --category` / `--re-calculate`）の防御 | 遡り不足の日付があれば例外で止める（§3.4）。足りない分を Parquet から自動で補うと、SQLite 経路に別経路の値を黙って混ぜることになる | **OK** |
| **4-2** | 本番 MTS の修復方法とタイミング | merge 後、API サーバを止めて `tools/deploy_after_merge.ps1 -RebuildFrom T5`（health check・NG 時ロールバック付き）。**実行前に dry-run の差分を提示して確認を取る**。変わるのは P3 の38日（MTS 最大 15.5pt）と 2018-04〜05 の33日（最大 18.5pt）、他は 0.11pt 以内 | **OK** |
| **4-3** | 型3 シナリオの再評価 | 修復後に再実行して before/after を記録する（成績は差し戻し基準にしない） | **実行する。**「型3 は Parquet しか見ないので SQL のずれは影響ないと思う」とのユーザー見立て。**コード確認で見立ては支持された**（理由は §4.1）。したがって**期待値は「変化なし」**で、再実行は影響が無いことの答え合わせとして行う |
| **4-5** | 再計算スクリプトの隔離の穴（§7-1。検収で発見） | 本計画に含める（5-6b） | **本計画で実施する。**「抜け漏れると他のタスクに影響しそう」 |
| **4-6** | `run_production_restore()` の本番決め打ち（§7-4。5-8 の検収で発見） | 本計画で直す（5-8b） | **本計画で直す（2026-09-12 ユーザー判断）** |
| **4-7** | `DashboardResponse.market_phase` が `Optional` でない（§7-5 の付随・§8） | 1行ガードを足すか、残作業に留めるか | **実施する（5-9b）** |
| **4-8** | 遡り不足ガードを「例外で止める」から「**除外＋警告**」に変えるか（§7-7。§4-1 の決定の修正） | 除外＋警告にする | **推奨手段で実施（2026-09-13 ユーザー判断）→ 5-7c** |
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
- [x] **5-9**（§4-4 が「揃える」なら）SPY 自前計算を揃え、`distribution_days` / `spy_above_sma200` の NaN 対策 + テスト
- [x] **5-10** pytest 全件パス（2026-09-12 オーケストレーター実測: **1736 passed / 0 failed**）
- [x] **5-8b** **`run_production_restore()` の復元先を明示する**（§3.7・§7-4・§4-6）— `_rebuild_from_parquet()` から解決済みの db_path を渡す。関数側の「環境変数を解除する」防御は残す。テストで「ワークツリーからは sandbox を対象にする」「引数なしの従来挙動を変えていない」を担保。**5-11・5-14 の前提**
- [x] **5-9b** **`dashboard_router.py:78` に `market_phase` の None ガードを足す**（§4-7）— `portfolio_logic.py:179` と同じ `if market_phase else "UNKNOWN"` の書き方に揃える
- [x] **5-11** sandbox で `--rebuild-from T3` を実走 → **合格**（2026-09-12。①sandbox Parquet vs 独立計算が SPY 由来6列・全期間4,136日で一致 ②sandbox SQLite vs Parquet が 502日で全列一致（復元の確認）③ログで新手順を確認（T3 22:45→T4 22:51→T5 22:53→復元先が **sandbox のパス**）④**本番は無傷**（世代・ポインタとも変化なし、新世代は sandbox 側）⑤`db_health_check --all --check-nulls` は本番ベースラインと**同種のみ**で回帰なし）
- [x] **5-12** ルールの明記（§3.5）+ `sync_skills.py --apply`
- [x] **5-13** 仕様書の T5 記述を更新
- [x] **5-6c** dry-run の差分に**日付集合の対称差**を表示する（§7-6(3)）— `compare_by_period()` の inner merge を直す。**5-14 のゲートそのもの**
- [x] ~~**5-7b** 遡り不足ガードの判定を Parquet 起点比較に変える~~ → **検収不合格**（`729bf19`。核心のケースが直っていない。§7-7）
- [x] **5-7c** 遡り不足ガードを「除外＋警告」に変える（§7-7）→ **検収合格**（`e2f883b`。例外は完全に排除。テスト 1747 passed）。**ただし表現を 5-7d で修正**
- [x] **5-7d** 遡り不足の日付も**行として書く**（除外をやめる）＋警告は維持（§7-8(1)）— 仕様書・Parquet 経路・`/available_dates` の連続性を揃える。**5-14 の前提** → **実装完了（`6c95dfe`）**
- [x] **5-9c** フロントで**判定不能を明示**する（§7-8(2)）— `MarketPhaseMeter.tsx` が `"UNKNOWN"` を BEAR に倒さないようにする → **実装完了（`98afefd`）**
- [x] **5-7e** T5 の収束（遡り不足で既に書いた日付を gap から外す）（§7-9(1)）— **検収合格**（`439b02f`）
- [x] **5-9d** `phase()` に `spy_sma200_rising` の None ガードを追加（§7-9(2)）— **検収合格**（`e8733a6`）
- [ ] **5-9e** ダッシュボードの NULL→0 潰しを直す（§7-9(3)）— **ユーザー判断待ち**
- [x] **5-8c** 復元の後に `run_pipeline(rebuild_from=None, skip_fetch=True)` を呼び、T1/FX/仮想指数/rotate/purge/整合監査を通す（§7-6(2)）— `refresh_T3Table.bat` のコメントと SKILL.md の記述も実態へ
- [ ] **5-14** **本体チェックアウトから実行する**（ワークツリーからは sandbox を指すため — §7-2）。先に 5-16 の「修復前」の型3 シナリオを実行して記録しておく → merge → **dry-run の差分をユーザーに提示して確認** → API サーバ停止 → `tools/deploy_after_merge.ps1 -RebuildFrom T5` → API サーバ再起動（§4-2）
- [ ] **5-15** 本番の修復確認 — 成功条件 2・3（Parquet 全期間計算と完全一致 / 8/29 世代と 8/29 以前で完全一致）
- [ ] **5-16** 型3 シナリオの再評価（§4-3）— 修復前（5-14 の前）と修復後で同じ条件で実行し before/after を記録。**期待値は「変化なし」**（§4.1）。大きく変わったら想定外の読み取り経路を調べる
- [ ] **5-17** 後片付け — issue ② をクローズ、memory `t5-sqlite-rebuild-freeze` を削除（MEMORY.md の索引も）、① の §4-6 を「前提充足」に更新
- [ ] **5-18** 計画書を `doc/completed/` へ移動

### 作業中メモ

**現在地: main へマージ済み。昇格（`deploy_after_merge.ps1 -RebuildFrom T3`）をユーザーが実行するのを待っている状態。その後 5-15 検算 → 5-16 型3（after のみ）→ 5-17 issue クローズと freeze 解除 → 5-19 completed へ。**

- **テスト実測: backend 1753 passed / 0 failed、frontend 10ファイル54テスト passed**、`sync_skills.py --check` 差分ゼロ
- **ワークツリー撤収時の注意**: `frontend/node_modules` が本体へのジャンクションになっている。`tools/remove_worktree.ps1` はこのケースを想定してリパースポイントをリンクだけ切り離す作りなので、**必ずこのスクリプトを通すこと**（素の削除・`git worktree remove` は本体の node_modules を巻き込む）
- **main が先行している**（zone_break 等）。ブランチと両方が触ったファイルは `doc/backend_specification.md` の1件のみ（節が異なるため自動マージできる見込み。5-17 で対処）
- **未反映の小さな残り**: `pipeline-debugging/SKILL.md` に 5-7e の収束挙動（一度書けば次回以降 gap から外れる）が未記載

**5-7e の実装（2026-09-16・実装ワーカー）**: `sync_phase_t5_signals()` の完了判定が
`market_trend_score` の非 NULL だけを見ていたため、5-7d で NULL 行として書いた
遡り不足日付が毎回 `gap_dates` に戻り、T5 が収束しない不具合（§7-9）を修正した。
- `gap_dates_candidate`（従来の `gap_dates` と同じ計算）から
  `_find_insufficient_lookback_dates()` で遡り不足日付を検出し、そのうち
  **既に `MarketSignal` 行が存在する日付**（`MarketSignal.date.in_(...)` で
  存在確認）を `gap_dates` から除外する。遡りが十分なのにスコアが NULL の日付
  （NULL バックフィル対象）は `insufficient_set` に入らないため、この除外の
  対象外のまま毎回再計算対象に残る（回帰防止）
- 除外後に `gap_dates` が空になったら、書き込みを一切行わず
  `logger.info("No gaps or missing scores detected in Phase 5 (...)")` で早期 return
  （delete/insert もクエリも発生しない）
- 警告（`logger.error`）は「**今回新たに** NULL 行として書き込む日付」
  （`newly_insufficient_dates` = `insufficient_dates` − 既に書き込み済みの日付）が
  あるときだけ出す。これにより同じ日付で毎晩警告が出続けることがなくなる
- `logger.error` の文面を「NULL になる列を断定しない」表現に修正（5-9d の指摘も
  同時に解消。どの列が NULL になるかは遡り本数により異なるため）
- `_find_insufficient_lookback_dates()` の docstring を「収束のための除外判断は
  この関数ではなく呼び出し側が持つ」ことが分かるように更新
- テスト追加（`test_t5_lookback_guard.py`）:
  - `TestConvergesOnSecondRun`: 1回目は NULL 行が書かれ警告が出ることを確認した後、
    2回目を実行して**警告が出ない・`logger.info` の早期 return メッセージが出る・
    MarketSignal 行の `id`（autoincrement）が変わっていない**（= delete/insert が
    一切走っていない証拠）ことを確認。**これが指摘の核心を担保するテスト**
  - `TestSufficientLookbackNullScoreNotFilteredAcrossRuns`: 遡りが十分（220本）だが
    既に NULL 行が存在する日付を用意し、実行後に**遡り不足の警告に含まれず・
    実際に値が入る**（= gap から除外されず再計算されたことの証拠）ことを確認
    （NULL バックフィルの回帰防止）
  - 既存5テストは無変更で通過
- pytest 全体: **1753 passed / 0 failed**（1747 + 5-7e 2件 + 5-9d 4件）
- コミット `439b02f`

**5-9d の実装（2026-09-16・実装ワーカー）**: `market_signals.py` の `phase()` の
判定不能ガードが `spy_sma200_rising` の None を見ていなかった問題を修正した。
- `spy_sma200_rising` は `sma_200.shift(20)` 由来のため、`sma_200` が算出され
  始めた直後の20本（200〜219本目、0-indexed 199〜218）で None になる。この帯で
  `spy_above_sma200 == 0` かつ `follow_through_day != 1` のとき、既存の
  `row.get('spy_sma200_rising') == 0` は `None == 0` が False になるため
  `RALLY_ATTEMPT` を捏造していた（本来は判定不能）
- 修正: `spy_above_sma200 == 0 and follow_through_day == 1`（FTD で確定する枝）の
  **次の** `elif row['spy_above_sma200'] == 0:` 分岐にのみ、`spy_sma200_rising`
  が `None` なら `None`（判定不能）を返すチェックを追加した。FTD で確定する枝
  （`follow_through_day == 1`）と BULL/CORRECTION 側（`spy_above_sma200 == 1`）は
  `spy_sma200_rising` を全く参照しないコード経路のため、このチェックの影響を
  受けない（コードレビュー観点で「巻き込んでいないこと」を担保）
- `sma_50`/`sma_200` 付近のコメントの、5-7b で入れて 5-7d で撤去済みの
  「Parquet にも同等以上の履歴が無いと判断して通した場合」という古い記述
  （§9-3 の指摘）を、現行の除外なし方式（§7-8(1)・5-7d）に合わせて修正した
- `t5_signals.py` の `logger.error` 文面修正は 5-7e のコミットに含めた
  （同じブロックを編集したため。5-9d の指摘内容そのもの）
- テスト追加（`test_market_signals_lookback_nan.py::TestPhaseUndeterminedWhenSpySma200RisingIsNone`）。
  下降トレンド（`spy_above_sma200=0` 固定）と上昇トレンド（`spy_above_sma200=1` 固定）
  の合成 SPY データで実際に `calculate_market_signals()` を実行し、以下を確認:
  - 200〜219本目・FTD 無し → `market_phase` が `None`
  - 同じ帯で FTD あり（+2%急騰＋出来高増を1日差し込む）→ `market_phase` が
    `RALLY_ATTEMPT`（確定できるので判定不能にしない）
  - `spy_above_sma200 == 1` 側は同じ帯でも `market_phase` が `BULL`（従来どおり）
  - 220本目以降は既存の分類（下降トレンドで `BEAR`）が回帰していないこと
- pytest 全体: **1753 passed / 0 failed**（5-7e と合わせて計6件追加、内訳は上記）
- コミット `e8733a6`

**5-7d の実装（2026-09-16・実装ワーカー）**: `_filter_insufficient_lookback_dates`（除外＋警告）を
`_find_insufficient_lookback_dates`（検出のみ・警告用）に置き換えた。
- `gap_dates` は一切フィルタしない。遡り不足の日付も他の gap 日付と同様に
  `calculate_market_signals()` に渡り、`ms_df` に行として現れ、そのまま `t5_recs` に入って書き込まれる
- 5-9 の `sanitize_numeric` ガード（既存）が `spy_above_sma200`/`distribution_days`/`market_trend_score` を
  None として保存する。`market_phase` は `calculate_market_signals()` 側で既に Python `None` を返すため
  そのまま代入で NULL になる
- 警告文言を「除外します」→「NULL（判定不能）として書き込みます」に変更。件数・範囲・対処
  （`--rebuild-from T5`）は維持。「全て除外された場合の早期 return」ブロックは削除（除外という概念が
  無くなったため）
- `SPY_LOOKBACK_MIN_BARS`（`indicators/market_signals.py`）のコメントを「書き込み対象に含める最小本数」から
  「`logger.error` で警告するかどうかの閾値」に修正
- `test_t5_lookback_guard.py` を新方針に合わせて書き直した。実装の実際の挙動を確認しながら修正:
  - **核心のケース**（ホット期間先頭1件の NULL 修復・`TestHotWindowNullScoreRepair`）: 例外なし・行が書かれる・
    `spy_above_sma200`/`distribution_days`/`market_phase`/`market_trend_score` が全て NULL・警告に
    「1 件」「日付」「`--rebuild-from T5`」「除外 という語は含まれない」ことを確認
  - **全期間再構築相当**（`TestFullRebuildLikeGap`）: 実装を実行して確認した結果、`market_trend_score` が
    実際に NULL になるのは先頭 **199 本**（`sma_200` の `min_periods=200`）で、`SPY_LOOKBACK_MIN_BARS`（220）は
    警告の閾値であって NULL 判定の閾値ではない（200〜219本目は `sma_200` は算出できるため
    `market_trend_score` に値が入るが、`spy_sma200_rising` の遡り20日分の安全マージンとして警告対象になる）。
    テストはこの実挙動に合わせて「先頭199本は NULL・200本目以降は値が入る・全 gap_dates が行として
    書かれる（消失なし）・警告件数は219件」を検証する形にした
  - **全 gap 日付が遡り不足**（`TestAllGapDatesInsufficientLookback`）: 10本しかない場合は全て200本未満なので
    全日付が NULL 行として書かれる（0行書き込みではない）ことを確認
  - **境界**（`TestLookbackBoundary`）: 220本ちょうど・219本ともに `sma_200` 算出は成立する範囲
    （200本以上）のため、市場スコアの null/not-null では区別できない。gap_dates をこの2日だけに絞り、
    「219本は警告対象（1件・日付が本文に出る）・220本は警告対象外（日付が本文に出ない）・どちらも
    行として書き込まれる」ことを検証する形に設計した
  - 日次相当（`TestDailyUpdateLikeGap`）は変更なし
- pytest 全体: **1747 passed / 0 failed**（PYTHONPATH=backend PYTHONIOENCODING=utf-8 PYTHONUTF8=1、
  206秒）。5-7c までと総数は変わらず（既存5テストを新5テストに置き換え）
- `tools/sync_skills.py --check` 差分ゼロを確認（`pipeline-debugging/SKILL.md` の対応行を新挙動に更新済み、
  `.agents/skills/` へも同期済み）
- コミット `6c95dfe`

**5-9c の実装（2026-09-16・実装ワーカー）**: `MarketPhaseMeter.tsx` の
`const activeIndex = currentIndex === -1 ? 0 : currentIndex;`（BEAR フォールバック）を撤去。
- `isUnknown = currentIndex === -1` を導入し、`activeIndex` はフォールバックせずそのまま
  `currentIndex`（`-1`）を保持する
- セグメント（バー）・ラベルの `isActive`/`isPast` 判定に `!isUnknown &&` を追加。`isUnknown` のときは
  どのセグメントもハイライト・着色されず、全ラベルが非強調（グレー・非ボールド）のまま —
  既存の「非アクティブ」表現をそのまま流用した中立表示
- ヘッダー行（`Market Trend Phase`）の右側に、`isUnknown` のときだけ `appConfig.colors.neutral`
  （既存の中立グレー `#aaaaaa`）で「判定不能」という文言を追加表示する。新しい色は導入していない
- 既知4フェーズ（BULL/CORRECTION/RALLY_ATTEMPT/BEAR）の分岐・配色・レイアウトは変更していない
  （`isUnknown` が false のときは従来と同じ式になる）
- テスト新設 `MarketPhaseMeter.test.tsx`: `"UNKNOWN"` で「判定不能」表示・Bear Market 非ハイライト・
  全ラベル非ハイライトを確認。BEAR/BULL/CORRECTION の既存表示（ハイライト・色）が回帰していないことも確認
- frontend node_modules がこのワークツリーに存在しなかったため、本体チェックアウトの
  `frontend/node_modules`（`package-lock.json` は本体と完全一致を確認済み）へのジャンクションを作成して
  `npm test` を実行した。**このワークツリーを `tools/remove_worktree.ps1` で撤収する前に、この
  ジャンクション（`frontend/node_modules`）を先に削除しておくこと**（ジャンクション越しに本体の
  `node_modules` を辿って削除してしまうリスクがあるため）
- `npm test -- --run`: **10 test files / 54 tests passed**（新設1ファイル4テスト含む）
- コミット `98afefd`

**5-7c の実装（2026-09-16・実装ワーカー）**: `t5_signals.py` の遡り不足ガードを
「例外で止める」から「**除外＋警告**」に変更した。
- `_get_parquet_spy_min_date()` を削除（Parquet 読み込み・起点比較・フォールバック分岐も全て削除）。
  新しいガードは Parquet を一切参照しない
- 新設 `_filter_insufficient_lookback_dates(gap_dates, spy_df)`: `spy_df`（日付昇順）に対し
  `np.searchsorted(..., side='right')` で各 gap 日付までの SPY 本数を一括計算し、
  `SPY_LOOKBACK_MIN_BARS`（220本）未満の日付を `excluded` として分離する（日付ごとのフルスキャンなし）
- 除外が発生したら `logger.error` で1回警告（除外件数・範囲（最古〜最新）・残り対象日数・
  `--rebuild-from T5` の案内を含む）。除外後に `gap_dates` が空になったら、書き込まずに
  別の `logger.error` を出して早期 `return`（例外は投げない）
- `SPY_LOOKBACK_MIN_BARS` は削除せず、コメントを「例外の閾値」から
  「書き込み対象に含める最小遡り本数」に書き換えた
- `test_t5_lookback_guard.py` を新方針で書き直した（旧5テスト→新5テスト、件数は変わらず）:
  日次相当（警告なし）/ ホット期間の古い日付1件だけの NULL 修復ケース（**核心のケース**。
  例外なし・その日付だけ除外・他の gap 日付は書き込まれる）/ 全期間再構築相当（先頭219日除外・
  220本目以降は書き込まれる）/ 全 gap 日付が遡り不足（1行も書き込まれず警告）/ 境界値（219本除外・
  220本書き込み）。旧テスト `TestTruncatedWindow` と `TestParquetUnreadableFallback` は
  Parquet 起点比較の仕組み自体が無くなったため削除
- `pipeline-debugging/SKILL.md` の対応表2行を新挙動に書き換え、`sync_skills.py --apply` 済み
  （`--check` 差分ゼロ確認済み）
- `doc/backend_specification.md` は確認したが、旧ガードの例外挙動を説明した記述は見つからなかった
  （261行・484行は Parquet 経路の NULL 化＝5-9 の話で、SQLite 経路の遡りガードには触れていない）
  ため**変更なし**
- pytest 全体: **1747 passed / 0 failed**（旧5テスト削除・新5テスト追加で総数は変わらず）

**5-8c の実データ確認（2026-09-13・sandbox で `--rebuild-from T3 --skip-fetch` を実走）**: 完了。
- **T5 は二重に走らない**（ログ 11:01:04 `No gaps or missing scores detected in Phase 5`）＝ ワーカーが
  「モックでの構造的保証のみ」と申告した点を実データで確認
- 後処理も到達: Phase 3（仮想テーマ指数171件）→ Phase 4（6日）→ Phase 5（gap なし）→
  **rotate**（`data_version_20260913_110104` を publish）→ 旧世代の整理。FX 同期も実行済み
- **5-11 の判定を再実施して再び合格**（`tmp/verify_sandbox_t5.py`。rotate 後の世代
  `market_signals_20260913_110104` に対し、SPY 由来6列が独立計算と全期間4,136日で一致、
  SQLite vs Parquet も 502日で全列一致）

**5-14 の承認材料（2026-09-13・本番に対する `--dry-run`。5-6c の対称差込み・確定版）**:

| 列 | P1 2010-04〜2018-03 | P2 2018-04〜2024-09-02 | P3 2024-09-03〜2025-06-30 | P4 2025-07〜 |
|---|---:|---:|---:|---:|
| `market_phase` | 199 | 0 | **38** | 0 |
| `spy_above_sma200` | 199 | 0 | **15** | 0 |
| `spy_sma200_rising` | 199 | 0 | **58** | 0 |
| `distribution_days` | 24 | 0 | **22**（最大6） | 0 |
| `market_trend_score` | 199 | 1,336（最大 18.5） | **197（最大 15.5）** | 148（最大 0.21） |
| `breadth_sma50` | 312 | 1,613（最大 0.424） | 0 | 148（最大 0.005） |
| **日付集合の対称差** | **0 / 0** | **0 / 0** | **0 / 0** | **0 / 0** |

**対称差が全期間0**（日付の消失・追加なし）なので、この表は上書き判断の材料として信頼できる。
P1 の199日は 5-9 による先頭の NULL 化（設計どおり）、P3 が修復対象の壊れ、P2 は 2018-04〜05 の breadth 修復。

**運用上の注意（2026-09-13 に踏んだ）**: `recompute_parquet_signals.py` は `pipeline_lock` を取るため、
**再構築・日次更新と同時に `--dry-run` を投げると `PipelineLockBusy` で失敗する**。ロックを取らない
読み取りスクリプト（`tmp/verify_sandbox_t5.py` 等）だけが並行実行できる。

**sandbox の世代整理について**: 上記 rotate 後の cleanup で sandbox 側の旧世代
（`20260911_145621` 等のハードリンク）が削除された。**検算用の 2026-08-29 世代は本番に残っており、
コピーも `tmp/verify/` にあるため影響なし。**

- 5-8c の「T5 が二重に走らない」はモックテストでの構造的保証のみ（ワーカー申告）。**5-11 相当の実走で再確認する**

- 検収済み: 5-1 / 5-2〜5-5（`dce43e8`）/ 5-6 反証・全セル一致 / 5-6b（`fc0303d`）/ 5-7（`9591796`）/
  5-8（`acb3842`）/ 5-9（`8b54a34`）/ 5-10 / 5-8b（`e4fca95`）/ 5-9b（`dd8bafa`）/ 5-11 実走・合格 /
  5-12・5-13（`0eb163b`）
- 5-13 の検収で**仕様書の既存の誤りを1件修正**（`588b3d7`）: `market_trend_score` の説明が
  「5項目の等価20%合計（⑤Distribution Days を含む）」だったが、実装は **4成分×25%**で
  **Distribution Days はスコアに含まない**（`indicators/market_signals.py` L149,304-317。
  breadth が無い期間は3成分×1/3）
- **コードレビューの指摘3件（§7-6）を実装ワーカーが対応（未検収）**:
  - **5-6c**: `compare_by_period()` に `date_diff`（`only_in_old` / `only_in_new` の件数＋先頭5件の日付例）を
    期間ごとに追加。0件でも必ず表示する。`_print_diff()` に `[3b]` の表を追加。既存キー（`days`/`columns`）は
    変えていないため既存アサーションは無傷
  - **5-7b**: 判定を「本数不足（220本未満）を検知した場合に限り、Parquet マスタの SPY 起点と比較する」
    2段構成にした（`t5_signals.py` の `_get_parquet_spy_min_date()` + ガード本体）。
    **本数が十分な場合（デイリー相当）は Parquet 比較に到達しない**——これが実装上の要点。
    もし常に「SQLite 全体の起点 vs Parquet 起点」を無条件比較する素朴な実装にすると、SQLite は
    ホット期間（約730日）しか保持しないため**日次更新が毎晩 RuntimeError になる**（起点は常に
    Parquet より後ろなため）。本数不足を外側のゲートにすることで、日次は「十分な本数があるので
    比較に進まない」→ 通る。切り詰められた窓は「本数不足 かつ Parquet に追加の履歴がある」→ 止まる。
    ホット期間の古い日付の単発 NULL 修復も「本数不足だが Parquet も同じ起点」→ 通る。
    Parquet 読めない場合は本数判定にフォールバック（従来どおり raise）。
    テスト4本 + フォールバック1本を `test_t5_lookback_guard.py` に実装（旧4テストは置き換え）
  - **5-8c**: `_rebuild_from_parquet()` の SQLite 復元（ステップ4）の後に `run_pipeline(rebuild_from=None,
    skip_fetch=True, skip_sync=<呼び出し元の値>, categories=None, skip_t3=False,
    recalculate_all=False)` を追加（ステップ5）。復元済みのため T5 の `gap_dates` は空になり
    SQLite 基点の T5 は走らない——`test_update_pipeline_rebuild.py` で `run_pipeline` をモックし
    kwargs を検証（`rebuild_from is None` / `skip_fetch is True`）。**T5 が実際に二重計算されない
    ことの直接証拠は 5-11 相当の実走（Parquet に real データを使う統合テスト）でしか取れない**ため、
    5-14 前の実走確認で再確認するのが望ましい。`refresh_T3Table.bat` のコメントと
    `pipeline-debugging/SKILL.md` の該当行も実態に合わせて更新、`sync_skills.py --apply` 済み
- テスト全体: **1747 passed / 0 failed**（1741 + 5-6c 3件 + 5-7b 差分2件 + 5-8c 差分2件）、
  `sync_skills.py --check` 差分ゼロ
- 5-14 の注意: **本体チェックアウトから実行**（ワークツリーからは sandbox を指す — §7-2）。
  実行前に dry-run の差分をユーザーへ提示して確認を取る。先に 5-16 の「修復前」型3 シナリオを記録しておく
- sandbox の Parquet は `--rebuild-from T3` 実走後の世代 `20260912_225403`。本番は `20260912_145123`
- **検算用**: 本番 `market_signals_20260829_144251.parquet`（prune 禁止）と、そのコピーを
  ワークツリーの `tmp/verify/` に退避済み（gitignore 対象）。判定スクリプトは `tmp/verify_sandbox_t5.py`

## 6. 検証プラン / 結果

| 検証 | 方法 | 期待値 |
|---|---|---|
| 単体 | pytest（5-2〜5-9 で追加したもの含む全件） | 全件パス |
| breadth 純関数 | 切り出し前後で SQLite 経路の出力を比較 | 完全一致 |
| dry-run 差分 | 本番 Parquet に対して `--dry-run`（5-6） | §1.2(a) と一致（P3 の SPY 由来列のみ不一致） |
| sandbox 再構築 | `--rebuild-from T3` 後の sandbox `market_signals`（5-11） | SPY 由来6列が Parquet 全期間計算と完全一致 → **達成（4,136日で一致）** |
| ガード | SPY の遡りが220本未満の `gap_dates` で T5 を回す | 例外で止まる / デイリー相当では止まらない |
| 隔離 | ワークツリーから再計算スクリプトを `--apply`（5-6b） | `ProductionWriteError` で拒否 → **達成**。`--dry-run` の参照先は sandbox になる（§7-2） |
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
- **判断（2026-09-12 ユーザー）**: **本計画で直す（5-8b）。** 5-11・5-14 の前提であるため。
  なお**より本質的な対策として「DB 系の呼び出しは基本ラッパー経由にし、切り替えをラッパー内で完結させる」方針**が
  ユーザーから提示されている（§8）。5-8b の「呼び出し側から解決済みパスを明示的に渡す」対応は、
  その方針を採る場合に置き換わる可能性がある**暫定対応**である

### 7-5. 5-9 で Parquet の dtype を2列変更した（§3.2 の制約からの逸脱・2026-09-12 検収で確認）

- **事象**: §3.2 では「出力スキーマは現行 Parquet の `market_signals` に合わせる（dtype も現行世代に揃える）」としていたが、
  5-9 で `spy_above_sma200` / `distribution_days` を **int64 → float64** に変えた
- **理由**: 両列が `None`（判定不能）を取りうるようになったため。**int64 は NaN を表現できず `astype` で落ちる**。
  既に `None` を取りうる `spy_sma200_rising` が float64 だったので、その慣習に揃えた（pandas の nullable `Int64` は導入していない）
- **消費側への影響は無し（検収で実測確認）**:
  - Parquet の `signals` を直接読むのは `backtest/etf_single_runner.py:164` の1箇所のみで、使うのは `date` と
    `market_trend_score` だけ（`pd.isna` ガード付き・失敗時は動的計算にフォールバック）。変更した2列は読んでいない
  - API・フロントは SQLite から読む。SQLite 書き込み側（`t5_signals.py`）は `int(...) or None` を維持しているため、
    SQLite の列は従来どおり整数のまま
  - rotate のマージ（`merge_timeseries_table`）は旧世代 int64 と新世代 float64 の concat になるが、float64 に統一されるだけ

### 7-6. コードレビューで3件（2026-09-13・`/code-review high main...HEAD`）🔴

**オーケストレーターが `.bat` を読んで前提を確認済み。3件とも妥当と判断した。うち2件は既に検収した項目の欠陥。**

**(1) [高] 遡り不足ガードの判定基準が違い、正当な全期間再構築を必ず止める**（`t5_signals.py:40` / 5-7）
- ガードは「最古 gap 日付より前に SPY が220本あるか」を見る。**全期間再構築では最古日付の遡りは必ず1本**なので、
  SQLite にフル履歴があっても例外になる。`run/tool/refresh_All.bat`（`--re-calculate`。8.8年分を再取得）が該当
- **止まる位置が悪い**: `orchestrator.py:861` の `MarketSignal` 全削除の**後**、`rotate`（同 :936）の**前**。
  例外で `market_signals` が空のまま rotate に到達せず、**再取得した数時間分が Parquet に保存されない**。
  `refresh_All.bat` は L36 で errorlevel を見ていないため、そのまま `COMPLETED` と表示する（実測確認済み）
- **同じ機構で日次更新が死ぬ**: ホット期間の古い日付に `market_trend_score` NULL が1件あるだけで
  gap に入り、遡り220本未満なら毎晩 T5 で落ちて rotate に到達しない（SKILL.md が「再実行でバックフィルされる」と
  案内している正常な修復ケース）。**オーケストレーターは 5-7 の検収で「止まるだけなので安全」と評価したが誤り**
- **対応方針（決定）**: 判定を「**SQLite の SPY 系列の起点が Parquet の起点より後ろか（＝窓が切り詰められているか）**」に変える。
  切り詰められていれば従来どおり `RuntimeError` で Parquet 基点へ誘導し、一致していれば通す（先頭の遡り不足日は
  5-9 により既に NULL になるため、偽の値は書かれない）。**5-9 が入った今、ガードの役割は「偽値の防止」ではなく
  「切り詰めた窓で再構築しようとしていることの通知」**であり、この判定の方が意図に合う → **5-7b**

**(2) [中] Parquet 委譲で T1 同期・FX・仮想指数・rotate・purge・整合監査が落ちた**（`update_pipeline.py:116` / 5-8）
- `run_pipeline()` を呼ばなくしたため、旧経路が実行していた T1（universe.db → symbols）・FX 同期・仮想テーマ指数の
  再合成・rotate・purge・`verify_pipeline_integrity` が全て実行されなくなった
- `run/tool/refresh_T3Table.bat` は `--rebuild-from T3 --skip-fetch` のみで **`--skip-sync` を渡していない**（実測確認済み）。
  従来は銘柄編集が反映・永続化されていたが、現在は反映されず、さらに `restore_sqlite_cache_from_parquet()` が
  `symbols`/`theme_constituents` を旧世代 Parquet のスナップショットへ巻き戻す
- **オーケストレーターの指示（§3.3）が rotate のことしか考えておらず、他の後処理を見落としていた**
- **対応方針（決定）**: ステップ4（復元）の**後に** `run_pipeline(rebuild_from=None, skip_fetch=True)` を呼んで
  後処理を通す。復元済みなので **T5 の `gap_dates` は空**になり SQLite 基点の T5 は走らない。
  `skip_fetch=True` 固定で yfinance の自動モード（`orchestrator.py:777-808`）にも入らない。
  `refresh_T3Table.bat` のコメントと SKILL.md の該当記述も実態に合わせる → **5-8c**

**(3) [中〜低] dry-run の差分が inner merge で、日付の消失・追加を検出できない**（`recompute_parquet_signals.py:159`）
- `compare_by_period()` が `how="inner"` のため、片側にしかない日付は「不一致 0 日」に見える。
  **この表は本番上書き（5-14）の唯一のゲート**なので、日付集合の対称差の件数を必ず表示する → **5-6c**

**→ 3件すべてを直してから 5-14 に進む。** 特に (3) は 5-14 の判断材料そのものなので、
現時点で取得した本番 dry-run の差分は**暫定値として扱う**。

### 7-7. 5-7b は指摘1の核心を直せていない（2026-09-13 検収で判明）🔴 **ユーザー確認待ち**

- **まず §7-6(1) の私（オーケストレーター）の指示が誤っていた**: 「SQLite の SPY 起点が Parquet 起点より後ろか」で
  判定せよと書いたが、**ホット期間は常に切り詰められているのが正常状態**（SQLite 起点 2024-09 / Parquet 起点 2010-04）。
  無条件比較は毎晩落ちる。ワーカーがこの矛盾を申告してきたのは正しい
- **ワーカーの2段構成（本数不足を検知したときだけ起点比較）も、核心のケースを直せていない**:
  `t5_signals.py` の `elif sqlite_spy_min_date > parquet_spy_min_date: raise` により、
  **ホット期間の先頭220本の中に gap 日付が入ると必ず例外**になる。これは
  ①ホット期間の古い日付に `market_trend_score` NULL が1件ある正常な修復ケース（SKILL.md が案内している）
  ②`--rebuild-from T2 --category` / `--re-calculate` で `market_signals` を全削除した後
  のいずれでも起きる。**指摘1が問題にした「日次が毎晩死に、rotate に到達せず Parquet が更新停止する」状態は残っている**
- **テストが通っているのに直っていない**: `test_does_not_raise_when_origin_matches_parquet` は
  SQLite 起点 = Parquet 起点という**実運用では成立しない前提**のフィクスチャ。まさに「実装もテストも通るが
  意図を満たしていない」型
- **`parquet_spy_min_date is None` のフォールバックも無条件 raise** なので、Parquet が読めない環境では日次が死ぬ

**オーケストレーターの推奨（§4-1 の決定の修正が必要）→ 5-7c**:
**例外で止めるのをやめ、「遡りが足りない gap 日付を書き込み対象から外し、`logger.error` で大きく警告する」形にする。**
- 保護の意図は保たれる（計算できない行は書かない）
- **日次が死なない**（rotate/purge/整合監査まで到達する）＝ 指摘1の実害がなくなる
- 実装が単純になる（SQLite 経路から Parquet 読み込み・起点比較・フォールバック分岐が消える）
- 5-9 により偽の値は既に書かれないので、残るリスクは「NULL を黙って書くこと」だけ。警告がそれを塞ぐ
- 警告には「除外した日付数・範囲・対処（`--rebuild-from T5`）」を含める

> [!IMPORTANT]
> これは §4-1 でユーザーが明示的に選んだ「補わずに例外で止める」の変更にあたるため確認を取った。
> **2026-09-13 ユーザー判断: 「5-7c は推奨手段で」＝ 除外＋警告で実施する**（§4-8）。

### 7-8. コードレビュー2回目で2件（2026-09-16）🔴

**前回の指摘3件のうち②③は解消を確認。①（ガード）は 5-7c で核心が直ったが、表現に1点ずれが残っていた。**
**オーケストレーターが2件とも実コードで裏取り済み。**

**(1) [高] 「除外」だと行が書かれず、仕様書・Parquet 経路と食い違う**（`t5_signals.py` / 5-7c）
- 5-13 で書いた仕様書 §3.6 は「遡り不足の行は**判定不能として NULL** になる」。Parquet 経路
  （`recompute_parquet_signals.py`）も **NaN 行を必ず書く**。ところが 5-7c の SQLite 経路は
  **行そのものを書かない**ので、両者が食い違う
- **実害（裏取り済み）**: `/available_dates` は **MarketSignal テーブルから日付一覧を作る**
  （`dashboard_router.py:21-30`）。`--rebuild-from T2 --category` 等で全削除した後に T5 を回すと、
  ホット期間の先頭219営業日の行が**恒久的に欠落**し、日付選択肢から消える。`chart_router` の
  SQLite フォールバック経路では MTS 系列に穴が空き、行インデックス基準の rolling が穴をまたいで平滑化する
- **検知は `logger.error` 1行のみ**で、日次ジョブは終了コードしか見ないため、誰かが
  `--rebuild-from T5` を回すまで復旧しない
- **対応（決定）→ 5-7d**: **除外をやめ、遡り不足の日付も行として書く**（5-9 により
  `spy_above_sma200`/`distribution_days`/`market_phase` は NULL、スコアは NaN になるので偽値は入らない）。
  **警告（`logger.error`）は残す**。これにより Parquet 経路・仕様書・日付一覧の連続性がすべて揃う
  - **§4-8 で選んだ「除外＋警告」の“除外”部分だけを修正するもの**。ユーザーが選んだ「例外で止めない・警告する」は維持する

**(2) [中] `"UNKNOWN"` をフロントが解釈できず、判定不能が「Bear Market」と表示される**（5-9b の帰結）
- `frontend/src/components/MarketPhaseMeter.tsx:16-17`:
  `const activeIndex = currentIndex === -1 ? 0 : currentIndex; // Default BEAR if unknown`
  → `"UNKNOWN"` は**赤の「Bear Market」として断定表示**される
- 同じ応答の `distribution_days` は NULL → `or 0` で「0日」＝最も健全な値として表示され、**逆方向に誤読させる**
- テストは 200 と `"UNKNOWN"` しか見ないので通る
- **対応（決定）→ 5-9c**: フロントで**判定不能を明示**する（メーターを中立表示にし、BEAR に倒さない）。
  5-9b で API 側に `"UNKNOWN"` を入れたのは本計画の変更なので、その帰結は本計画で塞ぐ

### 7-9. コードレビュー3回目で3件（2026-09-16）🔴

**オーケストレーターが3件とも実コードで裏取り済み。うち(2)は 5-9 の検収での見落とし。**
なお重点確認した「Parquet 経路と SQLite 経路で日付集合・NULL 表現が一致するか」はレビューで
**一致を確認**（`/available_dates` の連続性も保たれる）。5-7d の方向自体は正しかった。

**(1) [高] T5 が収束しない → 5-7e で対応済み**
- 5-7d で NULL 行を書くようにしたが、完了判定は `market_trend_score IS NOT NULL` のままだったため、
  **NULL で書いた日付が毎回 gap に戻る**。`--rebuild-from T2 --category` の後は毎晩 `logger.error` と
  delete/insert が走り続け、さらに `min_gap_date` が最古日付に固定されて breadth クエリの日付フィルタ
  （5.8M行の全履歴ロードを防ぐ最適化）が**恒久的に無効化**される
- **対応**: 「遡り不足」かつ「既に MarketSignal 行が存在する」日付を gap から外す。
  遡りが十分なのにスコアが NULL の日付（NULL バックフィル）は対象外のまま残す。
  警告は「今回新たに書く日付」があるときだけ。2回連続実行で収束することをテストで担保（`439b02f`）

**(2) [中] `phase()` が `spy_sma200_rising` の None を見ていない → 5-9d で対応済み**
- `spy_sma200_rising` は `sma_200.shift(20)` 由来で **200〜219本目の20本が None**。
  `None == 0` が False になるため、`spy_above_sma200 == 0` かつ FTD 無しのとき **`RALLY_ATTEMPT` を捏造**していた
  （BEAR か RALLY_ATTEMPT かはその NULL 列だけで決まるので本来は判定不能）。
  しかも `market_trend_score` は非 NULL なので「計算済み」扱いになり**二度と直らない**
- **これは 5-9 の検収での見落とし**。`SPY_LOOKBACK_MIN_BARS=220` と docstring の「偽の値は入らない」が、
  この20本帯で実装と食い違っていた
- **対応**: `spy_above_sma200 == 0` かつ FTD 無しの枝でのみ None ガードを追加（FTD で確定する側・
  BULL/CORRECTION 側を巻き込まない）。`t5_signals.py` の警告文面も「どの列が NULL になるか」を
  断定しない表現に修正（`e8733a6`）

**(3) [中] ダッシュボードが NULL を 0 に潰している → 5-9e（ユーザー判断待ち）**
- `dashboard_router.py` の `market_trend_score or 0.0` / `distribution_days or 0` が残っており、
  NULL 行の日付では**フェーズは「判定不能」なのに MTS は 0.0（最も弱気）・Distribution Days は 0（最良）**
  という矛盾表示になる。`trend_score_history` の折れ線もその区間だけ 0 に落ちて「暴落」に見える
- **対応案**: `DashboardResponse` の該当2フィールドを Optional 化し、フロントのメーター・折れ線を中立表示にする
- **状態**: スコープ拡大（API スキーマ＋フロント）になるため**ユーザー判断待ち**

### 7-10. 5-14 実行前に判明したこと（2026-09-16）

**(1) 🔴 `recompute_parquet_signals.py --apply` 単独では修復が定着しない**
- Parquet だけを直しても、**SQLite のホット期間には旧い（壊れた）`market_signals` が残る**。
  次に日次更新が走ると `rotate_and_archive_to_parquet()` が SQLite→Parquet をマージし、
  **同一日付は SQLite 側を採用する**（`parquet_cache_manager.py:207` の `keep='last'`）ため、
  **修復した P3（2024-09-03〜2025-06-30）がホット期間と重なる範囲で元に戻る**
- **したがって修復は「Parquet の T5 再計算 → SQLite を新 Parquet から復元」まで一体で行う。**
  `tools/deploy_after_merge.ps1 -RebuildFrom T5` はこの順序（§3.3 の手順）を満たす。**単独 `--apply` は使わない**

**(2) 本番の日次更新が 2026-09-16 07:46 に中断している**
- `Get-ScheduledTaskInfo` の `LastTaskResult = 3221225786`（`0xC000013A` = Ctrl+C / コンソール終了による中断）。
  `LastRunTime 2026/09/16 7:00`、`NextRunTime 2026/09/17 7:00`（**現在は 07:00 の1日1回**）
- ログは銘柄取得の途中（1267/3124）で止まり、**rotate 未実施**。本番の現行世代は 2026-09-15 14:56 のまま
- ログ内の `ERROR` は yfinance の「possibly delisted」個別銘柄ノイズのみで、中断の原因ではない
- **5-14 の前に日次更新を完了させるかどうかはユーザー判断**（半端な状態のまま本番データを書き換えたくないため）

**(3) 5-14 の承認材料（現行世代 `market_signals_20260915_145659` に対する dry-run）**

| 列 | P1 2010-04〜2018-03 | P2 2018-04〜2024-09-02 | P3 2024-09-03〜2025-06-30 | P4 2025-07〜 |
|---|---:|---:|---:|---:|
| `market_phase` | 199 | 0 | **38** | 0 |
| `spy_above_sma200` | 199 | 0 | **15** | 0 |
| `spy_sma200_rising` | 199 | 0 | **58** | 0 |
| `distribution_days` | 24 | 0 | **22**（最大6） | 0 |
| `market_trend_score` | 199 | 1,335（最大 18.5） | **204（最大 15.5）** | 296（最大 0.21） |
| `breadth_sma50` | 312 | 1,613（最大 0.424） | 205（最大 0.0005） | 300（最大 0.005） |
| **日付集合の対称差** | 0 / 0 | 0 / 0 | 0 / 0 | **0 / 1** |

- **対称差が1件検出された**（`2026-09-14` が再計算側にのみ存在）。**5-6c で入れたチェックが実際に機能した**。
  SPY の価格は 4,138行あるのに現行世代の `market_signals` にこの日付が無い＝**修復で1行増える**
- P3/P4 の `breadth_sma50` の小さな差（最大 0.005）は、breadth 母集団が 2,924→2,956銘柄に増えたため（`active` の現況で再計算する仕様どおり）
- P1 の199日は 5-9 による先頭の NULL 化（設計どおり）、**P3 が修復対象の壊れ**

### 7-11. マージ完了と昇格の実行待ち（2026-09-17）

- **型3 の「修復前」ベースラインは取らない**（ユーザー判断: 「MTS だけなら特に問題ない」）。
  §4-3 の型3 再評価は**修復後のみ記録**する
- **main へマージ済み**（`9fda405 Merge branch 'worktree-feat+t5-parquet-rebuild'`）。
  同時に別セッションの `bd39fe5 Merge branch 'worktree-rs-std-precision'` も入った。衝突なし
- **`--rebuild-from T3` 1回で3つのプランが同時に片付く構成になった**:
  ① rs-std-precision（T3 の RS 精度修正。`doc/in_progress/rs_rolling_std_precision_plan.md` は
  **本 T5 問題の freeze 解除待ちでブロックされていた**）② 本計画の T5 修復 ③ zone_break の指標追加分
- **昇格（5-14）はユーザー承認済みだが、`tools/deploy_after_merge.ps1` の実行が
  自動モードの権限チェックでブロックされた**（環境側の制限）。**ユーザーが本体チェックアウトで実行する**:

  ```powershell
  cd "D:\My Documents\Programing\stocktool"
  .	ools\deploy_after_merge.ps1 -RebuildFrom T3
  ```

  - 前提条件は確認済み（API サーバー停止・パイプライン実行中プロセスなし・次の日次は 09-17 07:00）
  - 検算用の 2026-08-29 世代のコピーはワークツリーの `tmp/verify/` に退避済み（本番側が prune されても検算可能）

## 8. スコープ外・残作業

- **`doc/backend_specification.md` の見出し番号が重複している** — `#### 3.6.1 為替レート`（263行）と
  `#### 3.6.1 各定性的シグナルの定義`（333行）が同じ番号。**本計画の変更前から存在する不整合**で、
  番号を振り直すと他ドキュメントからの参照を壊す恐れがあるため今回は直さない（5-13 の検収で確認）
- **方針: DB 系の呼び出しは基本ラッパー経由にし、切り替えをラッパー内で完結させる**（2026-09-12 ユーザー提示）
  — §7-1（再計算スクリプト3本）・§7-4（`run_production_restore`）はどちらも**呼び出し側が個別にパスを解決している**
  ことが原因で、同じ穴が別の場所に再発しうる。`paths.py` / `db/database.py` のラッパーを必ず通す形にし、
  sandbox・作業領域・本番の切り替えをラッパー内で完結させるのが本質的な対策。
  **本タスクで扱うか課題リストへ起票するかは別途議論**（ユーザーの明示。5-8b・5-6b はそれまでの暫定対応）。
  **規模感の調査（2026-09-12 実測）**: `config["system"]["db_path"]` を自前で読むファイルは **31本**。
  うち Parquet マスタ・ポインタ・復元に触るものが **10本**で、`paths.py` を経由しているのは **3本のみ**
  （いずれも 5-6b で直した再計算スクリプト）。**つまりマスタに書き込む経路のうち6本が自前解決のまま**:
  `pipeline/parquet_maintenance.py` / `scripts/backfill_price_history.py` / `scripts/purge_orphan_symbol.py` /
  `scripts/restore_fx_from_generation.py` / `scripts/restore_truncated_symbol_history.py` /
  `scripts/truncate_symbol_history.py`。**オーケストレーターの見解**: 本計画には含めず課題リストへ起票するのが妥当
  （目的が別・6本それぞれに呼び出し経路の確認と検証が必要・T5 修復に必要な経路は 5-6b/5-8b で塞げている）
- **`DashboardResponse.market_phase` が `Optional` でない** — `schemas.py:274` は `str` 宣言で、`dashboard_router.py:78` は値をそのまま渡す（隣の `distribution_days` は `or 0` で守られている）。5-9 以降 `market_phase` は 2010-04〜2011-02 の行で `None` になりうるため、**ホットキャッシュに 2010年の行が入る復元をした場合だけ**その日付で 500 になる。`portfolio_logic.py:179` と同じ `if market_phase else "UNKNOWN"` の1行で塞げる（**2026-09-12 ユーザー判断で実施 → 5-9b**）
- **`--rebuild-from T2 --category` の T3 が SQLite 基点のまま** — T2 のカテゴリ再取得後の T3 も、2026-08-29 の事故と同じ構図の可能性がある。T5 は §3.4 のガードで止まるが、T3 は未確認。別 issue 候補
- **`has_breadth = date >= '2018-04-01'` のハードコード** — 旧起点に由来。① §8 の「既存ガードの撤去」で扱う
- **日次の T5 でも `market_trend_score` が NULL の過去日付は `gap_dates` に入る**（`pipeline-debugging/SKILL.md:32`「NULL 欠損は過去に遡ってバックフィル」）。SQLite 窓の先頭付近の日付が NULL だと遡りが足りない。§3.4 のガードで止まるので黙って壊れはしないが、止まったときの復旧手順（`--rebuild-from T5`）を SKILL.md に書く
- **`atr_14`（`market_signals.py:250` 付近）の `min_periods=1` は 5-9 で意図的に対象外にした**（オーケストレーター判断）。最後の `.ffill().fillna(1.0)` が NaN を偽の値（ATR=1ドル）に置き換えるため、`min_periods` だけ揃えても偽の値が残り効果が薄い。直すにはゼロ除算ガード（`atr_14 > 0` の `np.where`）と `atr_pct_14` の設計見直しが必要で、本計画の範囲を超える。別issue候補
