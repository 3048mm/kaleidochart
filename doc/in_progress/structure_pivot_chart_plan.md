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
| クエリ | `min_len`（既定2）/ `max_len`（既定10）/ `full_range`（既定 false。`/chart` と揃える） |
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
| 2 | 長さ帯 | **2〜10 固定**。UI からの変更は v1 では出さない |
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
- [ ] **TradingView との目視突合**（§6。ユーザー確認が必要）
- [ ] `tmp/` の使い捨てスクリプト整理（本体 `tmp/probe_sp_*.py` / `sp_core.py` は
      実測の再現用に残すか削除するかユーザー判断）

### 作業中メモ

実装は完了。残りは **TradingView との目視突合**のみ（バックエンド／フロントエンドを
起動し、同一銘柄で TV のインジケータ（Min 2 / Max 10 / Tightest）と
LL / HL の位置とピボット価格を突き合わせる）。

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
