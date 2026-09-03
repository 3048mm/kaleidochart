# カウンタートレンドラインのチャート描画 計画書

- **ステータス**: 🚧 計画レビュー中
- **実施者**: AI エージェント（Claude Opus 5）
- **開始日**: 2026-09-03 / **完了日**: —
- **作業ブランチ**: `worktree-counter-trend-chart`（`.claude/worktrees/counter-trend-chart`）
- **対象 issue / 関連ドキュメント**:
  - `doc/completed/structure_pivot_chart_plan.md` §8（本項目が残作業として記載されている）
  - `doc/completed/structure_pivot_screener_plan.md` §5.6（`sp_counter` の移植と Pine 原文の照合）
  - 並行タスク: `doc/in_progress/trend_line_break_optimization_plan.md`（別ワークツリー）

## 1. 背景と目的

カウンタートレンドライン（作者の `rt_cnt_break` の土台）は 2026-08-29 に実装され、
`sp_counter` として T3 に載り、スクリーナー条件 `is_structure_trend_line_break` も動いている。
**しかしチャート上に線を引く実装だけが無い。**

現状:

| 経路 | 状態 |
| :--- | :--- |
| T3 カラム `sp_counter` | ✅ 本番稼働中（非NULL率 28.6%） |
| スクリーナー条件 | ✅ `is_structure_trend_line_break` |
| DATA VIEW の数値表示 | ✅ 値は見える |
| **チャート描画** | ❌ **未実装（本タスク）** |

1st / 2nd / TP1 / TP2 の4水準は `5b692b3` で描画済みなので、
**カウンター線だけが「値はあるのに見えない」状態**になっている。

### 完了条件

チャート画面で、構造が成立していない期間に引かれるカウンタートレンド線が
**傾いた線分として**表示され、その値が T3 の `sp_counter` と一致すること。

## 2. スコープと設計判断

### 2.1 変更すること

1. `backend/indicators/structure_pivot.py` — アンカー座標を返す `find_counter_trends()` を新設
2. `backend/api/chart_router.py` — `/chart/{id}/structure_pivot` のレスポンスに追加
3. `frontend/src/api/structurePivot.ts` — 線分生成にカウンター線を追加
4. `frontend/src/types.ts` — 型を追加
5. テスト（pytest / vitest）

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| `counter_trend_series()` の計算ロジック | **1バイトも変えない** | 2026-08-29 に Pine 原文と照合して確定し、作者の 8/26 出力を 2/2 再現・T3 と Parquet の経路一致 100% を確認済み。ここを触ると §5.6 の検証がすべて無効になる。**新関数は既存関数と完全に同じ値を出すことをテストで縛る** |
| T3 に新カラムを足す | **足さない** | 1st/2nd/TP と同じくチャート API でオンザフライ計算する。1銘柄あたり最大2,000本で 1ms 未満。Parquet 7年分の再計算を避ける（`structure_pivot_chart_plan.md` §2.2 と同じ判断） |
| ショート側（HH-LH）のカウンター線 | **やらない** | 買い候補探しという本ツールの目的 |
| Priority Mode の切り替え UI | **やらない** | 2026-09-03 ユーザー判断 |

### 2.3 なぜ「傾いた線分」がそのまま載るか

フロントの描画モデルは既に線分ベースで、`{from, fromValue, to, toValue}` を
lightweight-charts の2点 `setData()` に渡している（[ChartPage.tsx:483-493](frontend/src/pages/ChartPage.tsx#L483-L493)）。
既存の4水準は `fromValue === toValue` の水平線として使っているだけなので、
**傾いた線を描くのに新しい描画機構は要らない**。値を変えるだけ。

### 2.4 いちばんの設計上の論点 — アンカー座標が今は取れない

`counter_trend_series()` は**各バーの線の値（スカラー）しか返さない**。
線分として描くには線の起点2点（アンカー1 / アンカー2）が要るが、これは関数内部の
ローカル変数で捨てられている（[structure_pivot.py:414-436](backend/indicators/structure_pivot.py#L414-L436)）。

そこで `find_structures()` ↔ `structure_pivot_series()` と同じ関係を作る:

```
_scan_for_length()      ← 既存: 構造検出の単一実装
  ├─ find_structures()          ← 構造オブジェクトの列
  └─ structure_pivot_series()   ← バーごとのスカラー

_counter_scan()         ← 新設: カウンター線検出の単一実装
  ├─ find_counter_trends()      ← アンカー付きオブジェクトの列（本タスクで追加）
  └─ counter_trend_series()     ← バーごとのスカラー（既存。中身を委譲に置換）
```

**`counter_trend_series()` の外形と出力値は変えない。** 中身を `_counter_scan()` への
委譲に差し替えるだけで、T3・スクリーナー・バックテストの3経路は無変更。

## 3. 変更内容

### 3.1 `find_counter_trends()`（`backend/indicators/structure_pivot.py`）

```python
@dataclass(frozen=True)
class CounterTrend:
    a1_index: int      # アンカー1（limit_idx までで価格が最大の高値ピボット）
    a1_price: float
    a2_index: int      # アンカー2（a1 < idx < limit_idx で傾きが最大の高値ピボット）
    a2_price: float
    slope: float       # (a2_price - a1_price) / (a2_index - a1_index)
    start_index: int   # このアンカー組で線が引かれ始めたバー
    end_index: int     # 同じアンカー組が続いた最後のバー
    is_current: bool   # 最終バーで生きているか
```

**「1本の線」の単位はアンカー組 `(a1, a2)` が同じ連続区間**とする。
アンカーが入れ替わったら別の線として切る。値 `y = a2_price + slope * (i - a2_index)` は
区間内で完全に直線なので、線分として正しく描ける。

### 3.2 API レスポンス（`backend/api/chart_router.py`）

`build_structure_pivot_response()` の戻り値にキーを2つ足す。既存キーは変えない。

```jsonc
{
  "metadata": { ... },
  "structures": [ ... ],
  "current": { ... },
  "counters": [                    // 履歴（新規・上限つき）
    { "a1_date": "2026-05-02", "a1_price": 12.3,
      "a2_date": "2026-05-20", "a2_price": 11.8,
      "start_date": "2026-05-25", "end_date": "2026-06-10",
      "start_value": 11.6, "end_value": 11.1,
      "is_current": false }
  ],
  "current_counter": { ... }       // 最終バーで生きている線（無ければ null）
}
```

`start_value` / `end_value` を返すのは、**フロントに傾きの再計算をさせないため**。
日付↔インデックスの対応はサーバ側にしか無く、フロントで計算すると
「営業日でない日を跨ぐと座標がずれる」種類のバグを呼び込む。

### 3.3 フロントエンド

- `types.ts`: `CounterTrend` と `StructurePivotResponse` の拡張
- `structurePivot.ts`:
  - `fetchStructurePivot()` が**レスポンス全体を返す**ように変更（現在は `structures` だけを返して `current` を捨てている）。呼び出し元は `ChartPage.tsx` のみ
  - `buildStructureSegments()` にカウンター線の線分を追加。`role: 'current-counter'` / `'history-counter'`
  - 描画は `a1_date → end_date`（＝アンカー1から線の終端まで）。**アンカー2 で切らない**。
    アンカー2 は線の傾きを決める点であって終端ではない
- `ChartPage.tsx`: 既存の `segments.forEach` がそのまま処理するため**変更不要**の見込み

### 3.4 配色

既存: 1st = 黄 `#e0b000` / 2nd・構造 = 水色 `#00bcd4` / TP = 灰 / 履歴 = 白28%。
カウンター線は「構造が無いときに出る別カテゴリ」なので、既存と混ざらない色を当てる。
→ **提案: オレンジ `#ff8c42` の実線（現在）／同色 30% の点線（履歴）**（§4-Q2）。

## 4. ユーザー確認事項

- **Q1. 履歴のカウンター線も描くか。**
  構造ピボットの履歴は「ピボット水準だけを点線で30本」に絞った経緯がある
  （2026-08-27 に線が多すぎて読めないという判断で右端への延長を取りやめ）。
  カウンター線は `sp_counter` の非NULL率が 28.6% と高く、**履歴を全部描くと相当な本数**になる。
  → **提案: 既定は「現在の線のみ」。履歴は描かない。** レスポンスには含めておき、
  後から出したくなったらフロントだけで足せる状態にする

- **Q2. 色は オレンジ `#ff8c42` でよいか。**
  既存の黄（1st）と近いのが気になるなら、紫系 `#c77dff` も候補

- **Q3. 表示トグルは既存の「構造ピボット」と共通でよいか。**
  → **提案: 共通にする。** 構造とカウンターは排他（同時には出ない）なので、
  別トグルにしても片方が常に無反応に見えるだけ

- **Q4. アンカー点にマーカー（A1 / A2）を付けるか。**
  → **提案: 付けない。** 現在の構造の LL / HL マーカーと混ざって読みにくくなる。
  線が引かれていれば起点は目視で分かる

## 5. 実装順序と進捗チェックリスト

TDD（red → green）で進める。§2.2 の「既存の値を変えない」を最初にテストで縛る。

- [ ] 計画書のユーザーレビュー（本ファイル）
- [ ] **red**: `find_counter_trends()` のテストを書く
      - `counter_trend_series()` と完全一致すること（既存の値を1つも変えない）
      - アンカー組が変わったら線が切れること
      - 履歴長に依存しないこと（`TestHistoryLengthStability` と同じ性質）
- [ ] **green**: `_counter_scan()` を切り出し、`find_counter_trends()` を実装、
      `counter_trend_series()` を委譲に置換
- [ ] 既存テスト全通過の確認（`test_counter_trend.py` / `test_screener_parity.py`）
- [ ] API レスポンス拡張 + テスト
- [ ] **本番 T3 との突合**（§6 の本命。ここが通らなければ描画しても意味が無い）
- [ ] フロント: 型・線分生成 + vitest
- [ ] 実画面での目視確認（`sp_counter` が入っている銘柄を選ぶ）
- [ ] `structure_pivot_chart_plan.md` §8 の該当行を「実装済み」に更新
- [ ] 本計画書を `doc/completed/` へ移動

### 作業中メモ

計画レビュー待ち。ワークツリーは作成・プロビジョニング済み（`--mode write`）。

## 6. 検証プラン / 結果

### 6.1 既存の値を壊していないこと（最優先）

```powershell
$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/indicators/test_counter_trend.py backend/tests/api/test_screener_parity.py -v
```

### 6.2 チャート API と T3 の突合

`sp_counter` が非NULL の銘柄を N 件抽出し、**API が返す線の最終バーの値**と
**T3 の `sp_counter`** を比較する。`structure_pivot_screener_plan.md` §6.1 で
`sp_pivot` について「20銘柄で不一致0件」を確認したのと同じ手順。

不一致が出た場合は**描画側ではなく `_counter_scan()` の切り出しを疑う**
（§5.6 の教訓: 値が履歴長に依存すると2経路が静かに食い違う）。

### 6.3 目視

構造が無い期間に線が引かれ、構造が立ち上がると線が消えて 1st/2nd/TP に切り替わること。
`sp_counter` を上抜けた直後の銘柄（`is_structure_trend_line_break` でスクリーン）で確認する。

### 6.4 結果

（完了時に追記）

## 7. 途中発生した課題

（未着手）

## 8. スコープ外・残作業

- 履歴のカウンター線の表示（§4-Q1 で見送る場合。レスポンスには含めるのでフロントだけで足せる）
- ショート側（HH-LH）
- Priority Mode の切り替え UI
