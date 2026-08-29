# Structure Pivot (LL-HL) のチャート表示 計画書

- **ステータス**: 🚧 進行中（計画レビュー済み 2026-08-23）
- **実施者**: AI エージェント (Claude Opus 5) — オーケストレーター
- **開始日**: 2026-08-23 / **完了日**: —
- **作業ブランチ**: `worktree-structure-pivot-chart`（`.claude/worktrees/structure-pivot-chart` / main 基点）
- **対象 issue / 関連ドキュメント**: `doc/issue_list.md` P3「チャート画面へのポジション情報統合」（描画方式が同型）、
  TradingView 公開スクリプト `Structure Pivot (LL-HL / HH-LH)`（scriptAccess: open_no_auth）

## 1. 背景と目的

チャート画面に **LL→HL の押し目構造とブレイクアウト水準（ピボット）** を描画し、
手動でエントリー価格と損切りラインを決める作業を目視で支援する。

### なぜスクリーナーではなくチャート表示なのか（実測に基づく判断）

2026-08-22〜23 に本番 Parquet（個別・active 2,882銘柄 / 2021-03-26〜2026-03-26 /
流動性床 $2M）で読み取り専用の実測を行い、**スクリーン条件としては採用しない**と判断した。
実測スクリプトは `tmp/probe_sp_*.py`、移植本体は `tmp/sp_core.py`。

| 測定 | 結果 |
| :--- | :--- |
| 状態（構造成立・ピボット未達）の20日リターン | **+0.74%** / 勝率50.0%（ヌル +0.86% / 50.8% を下回る） |
| ゆるいゲートでのピボット上抜け | +0.80%（63日ローリング最大の上抜け +0.46% より**上**） |
| **VCP 土台条件の上でブレイク判定だけ差し替え** | 大陽線 +1.70% / ローリング最大 +1.26% / **構造ピボット +0.54%**（自分のヌル +0.74% 未満） |
| 判別可能性 | 決定に関わる 0.3〜0.7件/日 の頻度帯では 5年でも標準誤差 0.5〜0.6pp。差が統計的に決着しない |

さらに、**SP 特徴量8種 × 既存指標12種の組み合わせ 4,449 通りを総当たり**し、
学習（2021-03〜2024-03）で選抜 → 検証（2024-03〜2026-03）で答え合わせした
（`tmp/probe_sp_combo2.py`。**期間ごとのヌルからの超過**で評価。学習期ヌル +0.16% に対し
検証期ヌル +1.09% と地合いが1pp違うため、素の平均では比較にならない）。

| | 学習上位10 の超過 | → 検証 | 検証で正だった割合 |
| :--- | ---: | ---: | ---: |
| SP を含む群（4,449通り） | +1.91pp | **+1.23pp** | 80% |
| SP を使わない対照群（660通り） | +0.78pp | **+0.85pp** | 100% |

SP 群は**学習期の当てはまりだけが高く（+1.91 vs +0.78）、検証期の劣化が大きい**
（上位50では +1.27→+0.44pp、正の割合56% まで落ちる。対照群は +0.37→+0.49pp / 84%）。
探索の自由度が増えた分ノイズに当てているという典型的な兆候で、
**対照群の最良を検証期に上回った SP 組み合わせは 0/50 だった。**

学習・検証の両方で勝率60%以上かつ超過が正だったのは 5,109 通り中3件のみ。
うち SP を含むのは1件だけだった:

```
SP:構造ピボットまで2%以内 × 52週高値10%以内 × VCR収縮(<=0.7) × 買い集め(ud>=1.5)
  学習 0.41件/日 +1.18pp 勝率60.7% | 検証 0.55件/日 +2.37pp±1.06 勝率65.0%
```

この1件について、**土台3条件を固定して近接判定だけを入れ替える A/B** を行った
（`tmp/probe_sp_ab.py`）。結果は **1勝2敗**:

| 土台 | 近接なし（検証超過） | SP 2%以内 | 63日高値2%以内 |
| :--- | ---: | ---: | ---: |
| A: 52週高値10% × VCR収縮 × 買い集め | +1.57pp | **+2.37pp** | +1.61pp |
| B: 52週高値10% × VCR収縮 × 出来高膨張 | +1.01pp | +1.17pp（学習 +4.24pp から崩落） | **+1.45pp** |
| C: 52週高値10% × VCR収縮 | +1.02pp | +0.82pp | **+1.47pp** |

**土台A でしか効かず、土台を1つ動かすと消えるか逆転する。** さらに決定的なのは、
土台A における検証超過 +2.37pp の95%信頼区間 **[+1.31, +3.43] が、土台A そのものの
+1.57pp を含んでしまう**こと — すなわち**「SP を足したことによる改善」は検証期で有意ではない**。

> [!NOTE]
> 構造ピボット近接と 63日高値近接は **Jaccard 3.8%** でほとんど重ならず、
> 別物の信号を指しているのは確か。「構造から導いた水準のほうが正確」という
> 見立て自体は間違っていない。問題は、その差が**成績として取り出せるほど大きくない**こと。

**結論**: T3 に列を足して Parquet 7年分を再計算する投資は見送る。
一方、**目で見て水準を確認する用途では現行の近似より正確**なので、描画だけを入れる。

### 成功条件

チャート画面で対象銘柄の LL / HL / ピボット水準が視認でき、
ピボット価格と HL 価格（損切り候補）が数値で読めること。

## 2. スコープと設計判断

### 2.1 変更すること

- `backend/indicators/structure_pivot.py`（新規・純関数）— LL-HL 構造の検出
- `GET /api/chart/{symbol_id}/structure_pivot`（新規エンドポイント）
- `ChartWidget` に構造ピボットの描画を追加、`ChartPage` に表示トグルを追加

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| T3 (`indicators`) へのカラム追加 | **しない** | §1 の実測でスクリーン条件として採用しないため。列を足すとサンドボックス検証＋Parquet 7年分の再計算が必要になり、進行中のバックテスト作業（Parquet がデータソース）と直接衝突する |
| スクリーナー／バックテストへの結線 | **しない** | 同上。`screener_registry` への登録も行わない |
| ショート側（HH-LH） | **やらない**（ユーザー判断 2026-08-22） | 本ツールの目的は買い候補探し |
| Priority Mode の3種 | **Tightest 固定**（API パラメータでは受けるが UI からは出さない） | 「最も近い抵抗＝最初に当たる水準」が目視用途に最も素直。Longest/Shortest は列挙しても判断材料が増えない |
| カウンタートレンドライン | **移植しない** | TV 版の装飾機能。構造の判定には関与せず、実装量に対して得るものが無い |
| ピボット水準の計算場所 | **API でオンザフライ** | 1銘柄あたり最大2,000本程度。numpy で 1ms 未満。事前計算する理由が無い |
| 確定遅延の扱い | **TV 準拠（先読みなし）** | `ta.pivotlow(low, L, L)` は L 本先まで確定しない。構造は HL 確定バー以降にのみ描画する。「あとから過去に線が生える」表示は将来バックテストに転用したときに先読みの温床になるため、最初から確定ベースで作る |

> [!NOTE]
> **確定遅延は目視でも影響する。** 実測では、構造が確定した時点で既に終値がピボットを
> 超えていたケースが **27〜28%**（帯域によらずほぼ一定）。この割合は UI 上で
> 「確定済み」と分かる形にする必要はないが、仕様として認識しておく。

## 3. 変更内容

### 3.1 `backend/indicators/structure_pivot.py`（新規）

`tmp/sp_core.py` の検証済み実装を本番へ移す。**pandas 非依存の純 numpy 関数**とし、
`indicators/` の「純粋な計算モジュール」という位置づけを守る。

| 関数 | 内容 |
| :--- | :--- |
| `pivot_strength_low(low) -> np.ndarray[int]` | 各足について `ta.pivotlow(low, L, L)` が成立する**最大の L**。単調性（±L の最安値なら ±j (j<L) の最安値）を使い、単調スタックで O(n) 1パス。**TV 版の「9本の並列スキャン」がこの1本に畳める** |
| `structure_for_len(high, low, close, strength, L)` | 長さ L の状態機械。Pine の `PivotState.update()` ＋ HL 割れ無効化に対応 |
| `find_structures(high, low, close, min_len=2, max_len=10) -> list[Structure]` | 帯域を走らせ Tightest で勝者を選び、**構造の区間リスト**を返す（描画用） |

`Structure` の項目: `ll_index` / `ll_price` / `hl_index` / `hl_price` /
`pivot_index` / `pivot_price` / `length` / `confirmed_index`（HL 確定バー）/
`valid_until_index`（HL 割れで無効化されたバー。生存中は `None`）。

numba は使わない（`moving_averages.py` / `volatility.py` は使っているが、
1銘柄・単発呼び出しでは JIT のウォームアップのほうが高くつく）。

### 3.2 `GET /api/chart/{symbol_id}/structure_pivot`（新規）

| 項目 | 内容 |
| :--- | :--- |
| クエリ | `min_len`（既定2）/ `max_len`（**既定5**。2026-08-26 に 10 から変更）/ `full_range`（既定 false。`/chart` と揃える） |
| データ源 | `daily_prices`（T2）の `high` / `low` / `close`。`/chart` と同じ期間 |
| レスポンス | `{"structures": [Structure...], "current": Structure \| null}` — `date` は ISO 文字列に変換して返す（フロントは index を持たない） |
| 異常系 | 銘柄が存在しない → 404。データ不足（30本未満）→ `{"structures": [], "current": null}` |

**別エンドポイントにする理由**: `/chart` のレスポンスは全指標を含む重い配列で、
表示トグルが OFF のときに構造まで計算・転送する必要が無い。既存レスポンスの
形を変えないため後方互換の心配も無い。

### 3.3 フロントエンド

> [!WARNING]
> **計画時の前提が誤っていた。** `ChartWidget.tsx` はどこからも import されていない
> **デッドコード**で、実際に描画しているのは `ChartPage.tsx` 自身（自前で
> `createChart` を呼び、`updateLineSeries` 等でシリーズを管理している）。
> 実装先を `ChartPage.tsx` に変更した。`ChartWidget.tsx` の扱いは §8 参照。

- `src/api/structurePivot.ts`（新規）— 取得と、描画用データの組み立て
  - `fetchStructurePivot()` / `buildStructureSegments()` / `buildStructureMarkers()`
  - 描画そのものは ChartPage に残し、**組み立てだけを純関数へ出してテスト可能にする**
  - **両端の日付がローソク足のデータに無い線分は捨てる**。lightweight-charts は
    データに無い時刻を渡すと描画が壊れるため（`full_range` 切り替え直後に効く）
- `ChartPage.tsx`
  - **構造線**: LL→HL を結ぶ破線 — 2点だけの `addLineSeries`
  - **ピボット線**: ピボット足から構造の終端まで水平線 — 同じく2点の `addLineSeries`
    （`createPriceLine` は時間方向に区切れないので使わない）
  - **LL / HL マーカー**: 既存の `markers` 配列へ相乗り（TD9・ATR 乖離と同じ配列）。
    lightweight-charts v4 にラベル描画 API は無いため `setMarkers()` を使う
  - 履歴はピボット水準だけを点線で最大30本（`STRUCTURE_HISTORY_LIMIT`）
- 表示トグル「構造」をツールバーへ、「構造ピボット (LL-HL)」を指標設定パネルへ追加。
  ON のときだけ `/api/chart/{id}/structure_pivot` を fetch する

## 4. ユーザー確認事項

**すべてユーザー判断済み（2026-08-23）。**

| # | 確認事項 | 判断 |
| :--- | :--- | :--- |
| 1 | 過去の構造も描くか | **過去も描く**（最新は色付き・過去はグレー）。TV 版の既定と同じ |
| 2 | 長さ帯 | **2〜5 固定**（当初 2〜10。2026-08-26 にスクリーナー側と揃えて変更。`structure_pivot_screener_plan.md` §2.2）。UI からの変更は v1 では出さない |
| 3 | 作業ブランチ | **専用ワークツリー** `worktree-structure-pivot-chart`。進行中4タスクのコミットを引き継がないよう **main を基点**にした |

## 5. 実装順序と進捗チェックリスト

- [x] `backend/tests/indicators/test_structure_pivot.py` を先に書く（TDD red）— 18件
- [x] `backend/indicators/structure_pivot.py` を実装（green）
- [x] 本番 Parquet でプロトタイプ（`tmp/sp_core.py`）との一致を検証
      — 30銘柄 / 58,034バーで強度と Tightest 勝者が完全一致（`tmp/verify_structure_pivot_port.py`）
- [x] `backend/tests/api/test_chart_api.py` にエンドポイントのテストを追加 — 7件
- [x] `chart_router.py` に `/chart/{symbol_id}/structure_pivot` を追加
- [x] `frontend/src/types.ts` に `StructurePivot` 型を追加
- [x] `frontend/src/api/structurePivot.ts` と そのテスト7件
- [x] `ChartPage` に描画とトグルを追加
- [x] `doc/frontend_specification.md` / `doc/backend_specification.md` §5.1.2 に追記
- [x] **TradingView との突合**（§6）— **完了（2026-08-27）**。目視より強い形で確定した:
      作者が X に公開したスクリーナー出力（8/20・8/25・8/26）と照合し、
      **1st 5/5・2nd 22/22 の計27銘柄が完全一致**（`structure_pivot_screener_plan.md` §5.1）。
      ピボット検出・確定遅延・fib 0.618 の水準・1st/2nd の排他条件すべてが作者の実装と一致。
      あわせてユーザーが画面で確認し、1st / TP1 / TP2 の描画追加（`5b692b3`）と
      履歴ピボット線の扱い（`d67a7c9` → `b93d863` で差し戻し）を確定
- [ ] `tmp/` の使い捨てスクリプト整理（本体 `tmp/probe_sp_*.py` / `sp_core.py` は
      実測の再現用に残すか削除するかユーザー判断）— **雑務。残作業には数えない**

### 作業中メモ

**本タスクは完了。** チャート描画（LL/HL マーカー・構造線・1st/2nd/TP1/TP2 の4水準・
履歴ピボット線）は本番稼働中。`tmp/` の整理を除いて残りは無い。

TP1 上限の扱いだけ、後から効いた発見がある: `is_structure_2nd_break` の実装時に
**作者は `close <= tp1_price` の排他条件を使っていない**ことが判明した
（BEAM がピボットを9%上抜けた状態で作者の 2nd リストに入っていた）。
チャート描画側は水準を描くだけなので影響しないが、Pine 版との差異として認識しておくこと。

構造ピボット関連の残作業は `structure_pivot_screener_plan.md` §5.2 に集約している。

## 6. 検証プラン / 結果

```powershell
# バックエンド
$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/indicators/test_structure_pivot.py backend/tests/api/test_chart_api.py -v
# 全体（コミット前の義務）
$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/ -v

# フロントエンド
cd frontend; npm test; npm run build
```

### 結果（2026-08-23）

| 検証 | 結果 |
| :--- | :--- |
| `backend/tests/indicators/test_structure_pivot.py` | **18件 green** |
| `backend/tests/api/test_chart_api.py` | **8件 green**（うち構造ピボット7件） |
| `frontend/src/api/__tests__/structurePivot.test.ts` | **7件 green** |
| バックエンド全体 `pytest backend/tests/` | **1,033件 green / 1件 fail**（下記） |
| フロントエンド `npm test` | **19件 green / 2件 fail**（下記） |
| `npm run build`（`tsc -b` 込み） | **成功** |
| 本番 Parquet でプロトタイプとの一致 | **30銘柄 / 58,034バーで完全一致** |

**失敗3件はいずれも本タスクと無関係。**

| 失敗 | 原因 | 確認方法 |
| :--- | :--- | :--- |
| `test_scenario_comparison.py::test_run_comparison_generates_outputs` | **ワークツリーに本番データが無い**（`data/` は gitignore のため Parquet マスターが存在せず、`FileNotFoundError: Parquet master cache files not found`）。本タスクは `backend/backtest/` を一切変更していない | `git diff --stat main..HEAD` で変更ファイルを確認。**merge 前に本体チェックアウトで再実行して確認すること** |
| `RsLineChart.test.tsx` の2件 | `chart.addHistogramSeries is not a function`。lightweight-charts のモック不足 | 変更を `git stash` して実行し、**着手前から失敗していることを確認済み** |

### main マージ後の再検証（2026-08-26 / `636f7aa`）

main を取り込み（49コミット・競合なし）、全テストを再実行した。

| | 結果 |
| :--- | :--- |
| バックエンド `pytest backend/tests/` | **1,123件 green / 1件 fail**（下記） |
| フロントエンド `npm test` | **8ファイル 33件すべて green** |
| `npm run build`（`tsc -b` 込み） | 成功 |

- **`RsLineChart.test.tsx` の失敗2件は解消済み。** main 側の `489c007`
  「RsLineChart テストのモック漏れを修正」で直っていた
- **`test_scenario_comparison.py::test_run_comparison_generates_outputs`** は
  ワークツリーでは失敗するが、原因は**このワークツリーに本番データが無いこと**。
  `data/parquet_master/` が空で、`backtest_runner.py:92` が
  `FileNotFoundError: Parquet master cache files not found` を投げる。
  戦略ロジックに到達する前に落ちており、本ブランチが触ったコードは1行も実行されない。

  > **本体チェックアウト（`main` / 本ブランチ取り込み済み）で確認済み: 1 passed / 31.04s**（2026-08-26）。
  > `data/` は gitignore なので、**新規ワークツリーではこのテストは常に失敗する**。
  > 本テストの検収は本体チェックアウトで行うこと。

### 残: TradingView との目視突合（ユーザー確認）

同じ銘柄・同じ設定（Min 2 / Max 10 / Tightest）で TradingView のインジケータを表示し、
**LL / HL の位置とピボット価格が一致すること**を数銘柄で確認する。
一致しない場合は確定遅延の扱いか勝者選択の順序を疑う。

> [!NOTE]
> **意図的な差異**: TV 版は履歴のピボット線を現在バーまで延長し続ける
> （`line.set_x2(hl.l, bar_index + 5)`）。本実装は**構造が死んだバーで打ち切る**。
> LL / HL の位置とピボット価格は一致するが、線の右端は一致しない。

## 7. 途中発生した課題

| # | 事象 | 原因 | 解決 |
| :--- | :--- | :--- | :--- |
| 1 | 実装先の想定が誤り | `ChartWidget.tsx` はどこからも import されていないデッドコードで、描画しているのは `ChartPage.tsx` 自身だった | 実装先を `ChartPage.tsx` へ変更（§3.3）。`ChartWidget.tsx` 自体は触っていない（§8） |
| 2 | API のテストが本番 Parquet を読んでいた | FastAPI のルータ関数を直接呼ぶと `Query(False)` が **Query オブジェクトのまま渡り truthy** になり、`if full_range:` が常に真になっていた | ロジックを素の引数を取る純関数 `build_structure_pivot_response()` へ出し、ルータは委譲のみに。テストは純関数を呼ぶ |
| 3 | 恣意的な `MIN_BARS = 30` ガードが確定遅延テストを潰した | 短い系列を一律で弾いていたため、「確定バーの前後で構造の有無が変わる」ことを検証できなかった | 探索範囲から導く `_min_bars_required(min_len) = 2*min_len + 3` に置き換え |
| 4 | ワークツリーが進行中タスクのコミット4件の上に乗っていた | `worktree.baseRef` が `head` で、本体が `worktree-objective-quality-first` に居たため | 固有コミットが無い時点で `git reset --hard main` し、main 基点に付け替えた |

## 8. スコープ外・残作業

- ~~**`frontend/src/components/ChartWidget.tsx` の扱い**~~ — **削除済み（2026-08-23）**。
  初期コミットで追加されて以降、一度も import されたことのないデッドコード（181行）だった
  （`git log --follow` で確認）。CLAUDE.md のコンポーネント一覧からも外し、
  「ChartPage のチャートはページ内で直接組んでいる」旨を明記した
- **`frontend/src/components/__tests__/RsLineChart.test.tsx` の失敗2件** —
  `chart.addHistogramSeries is not a function`。**本タスク着手前から失敗している**
  （変更を stash して確認済み）。lightweight-charts のモックに
  `addHistogramSeries` が無いのが原因。修正は別タスク
- **`chart_router.py` の DB パス解決の重複** — 本タスクで `_resolve_active_db_path()` を
  切り出したが、既存の2箇所（`get_chart_data` 内）は同じ処理をインラインで持ったまま。
  動いているコードなので今回は触らず、寄せるのは別途
- **改良版「Advanced Structure Pivot」のスクリーナー採用 — 見送り（2026-08-25 実測）**

  作者が note.com で公開した改良版（`https://note.com/oratnek_ill/n/nbca4d1b8c3e1`）を評価した。
  Pine Screener 対応を謳い、エントリー候補を自動算出する。核心の式:

  ```pine
  fib_range     = w.break_val - w.curr_p        // ピボット価格 − HL
  fib_1st_price = w.curr_p + fib_range * 0.618  // 早いエントリー
  tp1 = w.curr_p + fib_range * 1.764
  tp2 = w.curr_p + fib_range * 2.618
  min_len = 2, max_len = 5                       // 既定が 2-10 から変更
  ```

  スクリーン条件になるのは `rt_1st_break`（fib 0.618 上抜け）と
  `rt_2nd_break`（本ピボット上抜け）の2つだけ。他の5信号（TP1/TP2 ヒット・
  ストップヒット・LL 割れ・カウンター抜け）は**出口管理**であり、本プロジェクトは
  出口を `[exit_rules]` で固定している（§6.4）ため対象外。

  実測（`tmp/probe_advanced_sp.py` / `tmp/probe_advanced_sp_exits.py`。
  個別・active・流動性床 $2M・学習/検証分割・期間ごとのヌルからの超過）:

  | 判定 | 結果 |
  | :--- | :--- |
  | 本プロジェクトの出口（-8%ストップ/20日）での超過 | 4ゲート × 2信号 × 2帯域の **16通りすべてで ±0.25pp 以内**、検証期はほぼ全て負 |
  | 検出頻度 | ゲートなしで 57〜80件/日、TT×高値10%でも 15〜25件/日。**希少なシグナルではない** |
  | Advanced 版自身の出口（TP1/HL損切り/60日）での期待値 | `rt_2nd_break` は**勝率 63.8〜65.4%** だが平均リターンは**ヌルを下回る**（TT×高値10%: +0.46% vs ヌル +0.80%） |
  | 値幅設計 | `rt_2nd_break` は TP1 まで中央値 3.20% / 損切りまで 7.00% ＝ **設計リスクリワード 0.46** |
  | 本プロジェクトの固定 -8% ストップとの整合 | 構造上の損切りが -8% より深い entry が `rt_1st_break` で 24.1% / `rt_2nd_break` で 40.7% |

  **単独では採用しない**: 「勝率65%」の正体はリスクリワード 0.46（小さく何度も勝ち、
  たまに大きく負ける）で、平均リターンはヌル以下。Advanced 版自身の出口で見ると
  `rt_2nd_break` は 平均勝ち +5.79% / 平均負け -9.91% ＝ **ヌルに並ぶだけで勝率 68.6% が必要**
  なのに実績 64.4%。組み合わせで学習期は 70.7% まで上がるが検証期は 64.4% に戻り、
  ちょうど損益分岐に張り付く。出口管理システムを採ることは全戦略共通の
  `[exit_rules]` の変更であり、スクリーナーの変更ではない。

#### ただし組み合わせでは効く（2026-08-25 追試・当初の「見送り」を修正）

  「勝率は他の条件と組み合わせて真価を発揮するのでは」というユーザーの指摘を受けて、
  ADV 特徴量8種 × 既存指標12種で 4,063 通りを総当たりした（`tmp/probe_adv_combo.py`）。
  **群として見ると検証期に耐えており、ADV を含まない対照群を上回る。**

  | 群 | 学習上位50 の超過 | → 検証 | 検証で正 |
  | :--- | ---: | ---: | ---: |
  | ADV を含む群（4,063通り） | +0.89pp | **+0.86pp** | 78% |
  | 対照群（660通り） | +0.37pp | +0.49pp | 84% |

  素の Structure Pivot（+1.27pp → +0.44pp / 正56%）とは対照的に**劣化していない**。
  探索の自由度が多い側が劣化しにくいのは、当てはめではなく情報がある兆候。

  効いている項は**勝率ではなく `ピボットまで<=2%`**（構造ピボットの直前）だった。
  ADV 項を抜いたアブレーション（`tmp/probe_adv_finalists.py`）:

  | | 学習 | 検証 |
  | :--- | :--- | :--- |
  | `ピボットまで<=2% × 52週高値10% × VCR収縮 × 買い集め` | 0.57件/日 **+1.11pp**±0.84 勝率62.2% | 0.71件/日 **+2.07pp**±0.90 勝率64.1% |
  | （ADV 項を抜く）`52週高値10% × VCR収縮 × 買い集め` | 2.81件/日 **-0.38pp**±0.32 勝率52.4% | 4.50件/日 +1.57pp±0.34 勝率64.5% |
  | （近接を既存列で代用）`63日高値4%` を足す | 2.01件/日 -0.50pp±0.36 勝率52.7% | 3.29件/日 +1.60pp±0.38 勝率66.9% |

  **学習期で土台がヌルを有意に下回る(-0.38±0.32)ところを、構造ピボット近接が有意に上へ
  引き上げる(+1.11±0.84)。既存列による近接（63日高値4%）では再現しない。**
  検証期は両者とも正で差は CI 内（+1.57 vs +2.07）。長さ帯 2-5 / 2-10 の両方で再現する。

  > [!WARNING]
  > **探索1位は罠だった。** `TP1まで>=10% × VCR収縮 × 買い集め × RS強い` は
  > 検証超過 +7.05pp と出るが **CI が ±11.36**（0.59件/日）。ADV 項を抜いた
  > `VCR収縮 × 買い集め × RS強い` の方が +2.85pp±1.55（4.81件/日）で支持が強い。
  > **総当たりの上位は必ず CI とアブレーションで潰すこと。**

  **結論（修正）**: 20営業日の代理指標ではこれ以上詰められない。実エンジン
  （型1バックテスト＋型3シナリオ）で検証する価値がある。必要な T3 カラムは
  **`sp_pivot` と `sp_hl` の2列だけ**で、`ピボットまで%` / `レンジ幅%` / `TP1まで%` /
  fib 各水準は `screener_registry.VIRTUAL_COLUMNS` として `close` から導出できる。
  当初想定した9列は不要。**ただし着手は進行中のバックテスト系タスクの後**
  （T3 追加は Parquet 全再計算を伴い、バックテストのデータソースと衝突する）。

- ショート側（HH-LH）の描画
- **スクリーナー／バックテストへの結線**（§1 の実測により見送り）

  > 再検討する場合に備えた記録: 唯一の候補は
  > `SP:構造ピボットまで2%以内 × 52週高値10%以内 × VCR収縮 × 買い集め` で、
  > 必要な T3 カラムは **`sp_pivot`（ピボット価格）1列だけ**（`sp_dist_pct` は
  > `screener_registry.VIRTUAL_COLUMNS` として `close` から導出できる）。
  > 損切りライン表示に `sp_hl` を足しても2列で、当初想定した9列は不要。
  > ただし §1 の通り検証期で有意な改善が出ていないため、**やるなら
  > 実際の出口ルール（-8%ストップ / EMA21 2日連続割れ / 部分利確）での
  > 型1バックテスト＋型3シナリオテストまで通して判断する**こと。
  > 20営業日の前向きリターンは代理指標に過ぎない。
- Priority Mode の切り替え UI
- カウンタートレンドライン
