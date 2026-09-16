# Direction via Zone Break 指標移植 計画書

- **ステータス**: ✅ コード側は完了（2026-09-13）。フロントエンド表示（成功条件4）まで完了。
  単体では明確なエッジ無し（§6参照。Optuna投入の可否はユーザー判断待ち、この計画のスコープ外）。
  **残るのはユーザー実施の2ステップのみ**: ①本番データ昇格（DBスキーマ追加＋Parquetバックフィル、
  APIサーバー・日次更新を止めてから）②本ブランチのmainへのマージ（いずれもエージェントからは
  実行不可の運用ルール。§8参照）
- **実施者**: AI エージェント (Claude Sonnet 5) — オーケストレーター（2026-09-12、Remote Control切断した
  stocktool-fe セッションから本セッションが引き継ぎ。会話ログ・本計画書のみを引き継ぎ情報源とする）
- **開始日**: 2026-09-11 / **完了日**: 2026-09-13（コード側。本番昇格・マージはユーザー実施待ち）
- **作業ブランチ**: `worktree-zone-break`（`.claude/worktrees/zone-break` / main 基点。提案、§4 で確認）
- **対象 issue / 関連ドキュメント**:
  TradingView 公開スクリプト `Direction via Zone Break [by rukich]`
  （`https://jp.tradingview.com/script/jUIvzPrb-direction-via-zone-break-by-rukich/`、
  Pine Script v6、MPL 2.0、scriptAccess: open_no_auth）。
  進め方の先例: `doc/completed/structure_pivot_chart_plan.md`（チャート先行実装の判断根拠）、
  `doc/completed/structure_pivot_screener_plan.md`（T3 追加〜型1判定〜3箇所登録パターン、
  特に §5.6 Trend Line Break の移植ミスと教訓）、`doc/completed/counter_trend_chart_plan.md`。

## 1. 背景と目的

TradingView の ICT/SMC 系公開インジケータ「Direction via Zone Break」を stocktool の指標として
移植する。3本足フラクタルで SSL（直近安値）/ BSL（直近高値）を検出し、終値がそれを超えて確定した
時点でトレンド転換（BOS）と判定、各トレンド内で FVG（Fair Value Gap）ゾーンを検出・無効化判定する
状態機械。ユーザー指示は「最適化バックテスト用の指標として実装しつつ、フロント表示も考えておく」。

### なぜ今やるか

前回の会話で「表示は面白そうだが単体では薄商い銘柄でノイズが多そう。RS/Trend Template で絞った後の
確認フィルタとして型1バックテストで有意性を検証してから採否判断すべき」という方針で合意した。
本計画はその「型1で判定できる状態にする」ところまでが範囲（採否の結論は出さない）。
`structure_pivot_screener_plan.md` が同じ役割（判定できる状態にする）を果たした先例であり、
本計画はその進め方を踏襲する。

### このインジケータ特有のリスク（着手前に明記しておく）

`structure_pivot`（LL-HL）は「現在の構造」が直近 max_len 本程度の局所窓で決まるため、
SQLite（直近730日）と Parquet（全期間）のどちらから計算しても収束後は同じ値になった。
一方 `counter_trend`（`sp_counter`）は候補点の探索窓が無制限だったために**履歴の長さで値が変わる**
バグを本番昇格後に踏んだ（`structure_pivot_screener_plan.md` §5.6。最終バーの12%・直近60本の44%が不一致）。

Direction via Zone Break の状態（`ssl_bl`/`bsl_bl`/`isBull` 等）は **確定した反転（BOS）ごとに
リセットされる**ため、1つのトレンドレッグ内では局所的だが、レッグの長さ自体に上限が無い
（強いトレンド銘柄は反転せず何年も伸びうる）。したがって `counter_trend` と同種の「計算開始点に
状態が依存する」リスクを本質的に抱えている。**§6.1 で独立に収束性を検証してから T3 に載せる**。

### 成功条件

1. Python 移植（`backend/indicators/zone_break.py`）が元の Pine ロジックと同一の状態遷移をし、
   TradingView の実チャートと数銘柄で突合できること
2. `is_zone_break_bull` / `zb_ssl` / `zb_bsl` / `is_zone_break_weak` が T3 の正式カラムとして
   SQLite・Parquet 全期間に存在し、スクリーナー・最適化バックテスト・シナリオテストの3経路で
   同じフィルタが評価できること
3. `backtest_config.toml` に候補戦略が1本あり、**非最適化の単発実行**で Alpha 等の一次指標が
   確認できる状態になっていること（Optuna 投入の可否をユーザーが判断できる材料が揃う）
4. ChartPage で SSL/BSL ライン・FVG ゾーン・トレンド背景色が表示できること

## 2. スコープと設計判断

### 2.1 変更すること

- `backend/indicators/zone_break.py`（新規・純関数）— 状態機械の移植。T3 用の時系列関数と、
  チャート描画用の「ゾーン/ラインの区間リスト」を返す関数の両方を持つ（`structure_pivot.py` の
  `structure_pivot_series()` / `find_structures()` の二本立てと同型）
- `db/models.py` の `Indicator` に4カラム追加（`is_zone_break_bull` / `zb_ssl` / `zb_bsl` /
  `is_zone_break_weak`）
- `indicators/calculate.py` に算出ステップを追加
- 仮想カラム2種（`zb_dist_ssl_pct` / `zb_dist_bsl_pct`）と、特殊フィルタ4種
  （フリップ: `is_zone_break_bull_flip` / `is_zone_break_bear_flip`、継続ブレイク:
  `is_zone_break_bull_breakout` / `is_zone_break_bear_breakout`、§3.4 参照）を
  3箇所（レジストリ／SQL／pandas）に登録
- Parquet 全期間バックフィルスクリプト（`structure_pivot` の `backfill_structure_pivot.py` 方式）
- `backtest_config.toml` に候補戦略を1本追加（初期パラメータは素朴な値。最適化はしない）
- `GET /api/chart/{symbol_id}/zone_break`（新規、オンザフライ計算）
- `ChartPage` に SSL/BSL ライン・FVG ボックス・トレンド背景色の描画とトグル

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| FVG ボックス・SSL/BSL ラインの T3 永続化 | **しない** | チャート描画専用。`structure_pivot.find_structures()` と同じくオンザフライ計算（1銘柄・数千本を numpy で計算しても numba 不要な速度感） |
| Pine の全履歴 `for` 再スキャン（`detector_bsl/ssl_last_fractal`）をそのまま移植 | **しない** | 反転直後にのみ発火する稀なフォールバックだが、素直に移植すると最悪 O(n²)。境界を設けた代替探索にし、**無制限版との出力一致をテストで担保**する（実装判断は着手後、§5 参照） |
| Optuna 全体最適化（約12時間）の実行 | **この計画には含めない** | まず非最適化の単発バックテストで Alpha 等の一次指標を見て、投資判断をユーザーに委ねる。`structure_pivot` も同じ順序で進めた |
| `exit_type` の差し替え・戦略別出口の導入 | **しない** | `structure_pivot_screener_plan.md` §5.5 で一度実装して中止した前例がある。`[exit_rules]` は全戦略共通のまま |
| T4（相対ランク）への追加 | **しない** | 銘柄内の絶対水準・状態であり、横断パーセンタイルに意味が無い（`structure_pivot` §2.2 と同じ理由） |
| `zb_is_weak` を独立の特殊フィルタ関数にする | **しない** | 状態そのものが T3 の bool カラムなので、`is_trend_template` と同じ「素の bool_column」として汎用機構に乗る。専用関数が要るのはフリップ（前日比較が要る）だけ |
| ショート/ロングの区別 | **該当なし** | この指標は方向を状態として持つ一体の状態機械で、`structure_pivot` のような「ロング側だけ移植」という選択肢が無い（両方向が最初から実装に含まれる） |

## 3. 変更内容

### 3.1 `backend/indicators/zone_break.py`（新規）

Pine 原文（会話ログに全文取得済み・MPL 2.0）の状態機械を素直に移植する。主要な状態変数の対応:

| Pine | Python 実装での扱い |
| :--- | :--- |
| `ssl_bl` / `bsl_bl` | 現在の SSL/BSL 価格。T3 カラム `zb_ssl` / `zb_bsl` |
| `isBull` / `isBear` | 排他的な2状態。T3 カラム `is_zone_break_bull`（bool） |
| `int_ssl_bl` / `int_bsl_br` | 内部フラクタル候補。状態機械の中間変数（永続化しない） |
| `isBreak_bl` / `isConf_bl` | ブレイク/コンファーム中フラグ（中間変数） |
| `isWeakOF` | 直近ゾーンが無効化されたか。T3 カラム `is_zone_break_weak`（bool） |
| SSL/BSL の `line` オブジェクト、FVG の `box` 配列 | チャート描画用関数が返す区間リスト（T3 には持たない） |

提供する関数（`structure_pivot.py` の二本立てに揃える）:

```python
def zone_break_series(high, low, close) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """各バーの (is_bull, zb_ssl, zb_bsl, is_weak) を返す。T3 用。"""

def find_zone_break_zones(high, low, close) -> ZoneBreakState:
    """SSL/BSL ラインの区間列 + FVG ボックスの区間列を返す。チャート描画用。"""
```

両者は同じ内部スキャン関数を共用し、検出ロジックを二重に持たない
（`_scan_for_length` を `structure_pivot_series`/`find_structures` が共用するのと同じ構成）。

#### 3.1.1 Pine 原文の状態遷移の要点（2026-09-12、原文入手・解析済み。原文: `tmp/direction_via_zone_break.txt`）

原文には `isBreak_bl`（反転側）と `isConf_bl`（継続側）という**明確に別のイベント**が最初から
実装されており、§3.4 で追加した「フリップ」「継続ブレイク」の区別はこの2イベントにそのまま対応する
（ユーザーレビューの直感が原文の設計と一致していたことを裏付ける）。

- **`isBreak_bl`（= `is_zone_break_*_flip` の元）**: Bull 中に `close < ssl_bl` で確定 → 直後の
  3本足安値フラクタルで確定・反転（`isBull=False, isBear=True`）。新しい BSL は
  `detector_bsl_last_fractal()`（**全履歴を遡る無制限スキャン**、原文 L31-39）で決める。
  Bear→Bull も対称（`detector_ssl_last_fractal()`）。**§1 で懸念していた「全履歴依存」の実体はここ**。
- **`isConf_bl`（= `is_zone_break_*_breakout` の元）**: Bull 中に `close > bsl_bl` で確定 → 直後の
  3本足高値フラクタルで確定・**トレンド継続のままゾーンだけ更新**
  （`bsl_bl := 新しい高値`, `ssl_bl := int_ssl_bl`）。`isBull`/`isBear` は変化しない。
  Bear の対称イベントも同様（`int_bsl_br` を使う）。
- **`is_zone_break_weak`（`isWeakOF`）**: FVG ボックス配列（`fvg_bl_array`/`fvg_br_array`）を
  `close` と比較し、無効化（invalidate）が1件でも起きたら true。**リセット条件は
  `isConf_bl and not isConf_bl[1]`（isConf_bl が今バーで新規に true になった瞬間）で配列を
  丸ごとクリア** — つまりリセットは「継続ブレイクの close 確定バー」で起き、フラクタル確定を待たない。
  Bull 側は `box.get_bottom` = 生成時の `high[2]`（固定値）と `close` の比較、Bear 側は
  `box.get_top` = 生成時の `low[2]`（固定値）との比較。
- **既知の実装上の癖（原文 L325-343 / L363-381、忠実移植するか要検討）**: 無効化ループが
  `array.remove` で配列を縮めながら同じ添字 `i` を進めるため、無効化が起きた回では次の1要素の
  チェックを事実上スキップする（Pine 原文自体のバグ疑い）。tmp/ の素朴な参照実装ではこれも
  忠実に再現し、本実装でこの癖を保持するか意図的に修正するかは TDD 時に判断してこの節に追記する。

この節の内容は原文を1行ずつ確認して得たものであり、要約からの再翻訳ではない
（`counter_trend` 移植時に要約経由で2箇所取り違えた前例への対策として、原文ファイルへの直接参照を
`tmp/direction_via_zone_break.txt`（本体）/ `.claude/worktrees/zone-break/tmp/`（ワークツリー内）
に保存済み）。

> [!IMPORTANT]
> **確定遅延は無い（Pine 原文どおり）。** この指標は `structure_pivot` と違い、当日の終値だけで
> 状態が確定する（3本足フラクタルの確定は1本遅れるが、ブレイク判定自体は先読みしていない）。
> ただし「今日時点で isBull か」は当日終値まで使うため、**日中に値は変わりうる**（Pine のリアルタイム
> バーと同じ）。日足バッチは確定足のみを扱うため問題にならないが、意図として記録しておく。

### 3.2 T3 カラム

| カラム | 型 | 内容 |
| :--- | :--- | :--- |
| `is_zone_break_bull` | Boolean | 現在のトレンド方向（True=Bull, False=Bear） |
| `zb_ssl` | Float | 現在の SSL 価格 |
| `zb_bsl` | Float | 現在の BSL 価格 |
| `is_zone_break_weak` | Boolean | 直近のトレンド内で最も近い FVG ゾーンが無効化済みか（弱さのシグナル） |

命名は `structure_pivot`（`sp_*`）に倣い `zb_*` を接頭辞にし、bool 列は他の bool 指標
（`is_trend_template` 等）と同じ `is_` 接頭辞規約に合わせる。

### 3.3 仮想カラム（`close` から導出。T3 カラムは増やさない）

| 名前 | 式 | 用途 |
| :--- | :--- | :--- |
| `zb_dist_ssl_pct` | `(close - zb_ssl) / close * 100` | SSL（損切り候補）までの距離 |
| `zb_dist_bsl_pct` | `(zb_bsl - close) / close * 100` | BSL（ブレイク水準）までの距離。ブレイク済みなら負値 |

### 3.4 特殊フィルタ（フリップイベント）

前日との比較が要るため `screener_filters.py` に純関数を追加し、`screener_registry.EXPLICIT_SPECS`
に登録する（`is_rs_macd_hist_rising_21` と同型: `requires=('is_zone_break_bull',)`,
`prev_requires=('is_zone_break_bull',)`）。

```python
def filter_is_zone_break_bull_flip(merged: pd.DataFrame) -> pd.Series:
    """前日 Bear → 当日 Bull に転換した銘柄を通過させる。"""

def filter_is_zone_break_bear_flip(merged: pd.DataFrame) -> pd.Series:
    """前日 Bull → 当日 Bear に転換した銘柄を通過させる。"""
```

**追加（2026-09-12、ユーザーレビュー反映）**: フリップ（転換初日）だけだと「継続中かどうか」の判定に寄りすぎるため、
トレンド継続中に SSL/BSL がさらに更新される「継続ブレイク」も別の特殊フィルタとして用意する。
フリップと継続ブレイクは意味が異なる（初動 vs 継続）ため、**1つの合成フィルタにせず2種を独立に定義**し、
§3.5 で別々の候補戦略として型1評価にかける（`backtest_config.toml` に OR 条件を書く機構が無いことも確認済み）。

```python
def filter_is_zone_break_bull_breakout(merged: pd.DataFrame) -> pd.Series:
    """前日・当日とも Bull 継続中（フリップ当日は除く）かつ、当日 zb_bsl が前日から更新（上昇）された銘柄を通過させる。"""

def filter_is_zone_break_bear_breakout(merged: pd.DataFrame) -> pd.Series:
    """前日・当日とも Bear 継続中（フリップ当日は除く）かつ、当日 zb_ssl が前日から更新（下落）された銘柄を通過させる。"""
```

`screener_registry.EXPLICIT_SPECS` には以下を追加登録する:

```python
'is_zone_break_bull_breakout': FilterSpec(
    key='is_zone_break_bull_breakout', kind='special', column=None, op=None,
    requires=('is_zone_break_bull', 'zb_bsl'),
    prev_requires=('is_zone_break_bull', 'zb_bsl'),
),
'is_zone_break_bear_breakout': FilterSpec(
    key='is_zone_break_bear_breakout', kind='special', column=None, op=None,
    requires=('is_zone_break_bull', 'zb_ssl'),
    prev_requires=('is_zone_break_bull', 'zb_ssl'),
),
```

> [!NOTE]
> 「前日から zb_bsl/zb_ssl が更新された」の判定境界（浮動小数点の許容誤差、フリップ当日をどう除外するか
> ＝ `prev is_zone_break_bull == True` を必須にすることで自然に除外される想定）は、TDD フェーズ
> （§5, `test_zone_break.py` および `screener_filters` 側のテスト）で具体的なケースを書きながら確定する。
> ここでは意図（継続中の新ゾーンブレイクを検出する）のみを確定事項とする。

### 3.5 候補戦略（`backtest_config.toml`）

初期値は最適化前の素朴な組み合わせ（型1の単発実行でベースラインを取るため）。名称は仮称。
フリップ（初動）と継続ブレイクは意味が異なるため、**2本の独立した候補戦略**として型1評価にかけ、
どちらが有意か（あるいは両方か）をユーザーが単発実行の結果を見て判断する。両戦略に `min_market_cap`
を追加（ユーザー指定、2026-09-12）。

```toml
[[strategy]]
name = "I1_zone_break_flip"
description = "Direction via Zone Break: 強気転換初日、直近ゾーンがまだ無効化されていない"
is_zone_break_bull_flip = true
is_zone_break_weak = false
min_market_cap = 1e8
max_hits_per_day = 10

[[strategy]]
name = "I2_zone_break_breakout"
description = "Direction via Zone Break: 強気トレンド継続中に新しいゾーンをブレイク（無効化されていない）"
is_zone_break_bull_breakout = true
is_zone_break_weak = false
min_market_cap = 1e8
max_hits_per_day = 10
```

### 3.6 Parquet 全期間バックフィル

`structure_pivot_screener_plan.md` §3.5 と同じ懸念がそのまま当てはまる
（`deploy_after_merge.py` は直近730日しか埋めない）。専用バックフィルスクリプトを新設し、
**既存カラムの値が変わっていないことを検査してから昇格**する（同計画の安全弁をそのまま踏襲）。

### 3.7 フロントエンド

- `GET /api/chart/{symbol_id}/zone_break`（新規、オンザフライ計算。`/chart/{id}/structure_pivot`
  と同型）— SSL/BSL ラインの区間列、FVG ボックス（有効/無効化済みの2状態）、現在の方向を返す
- `frontend/src/types.ts` に型追加、`frontend/src/api/zoneBreak.ts`（取得＋描画データ組み立ての
  純関数、単体テスト付き）
- `ChartPage.tsx` に直接実装（`ChartWidget.tsx` は import されていないデッドコードとして
  既に削除済み。`structure_pivot_chart_plan.md` §7-1 参照）
  - SSL/BSL ライン（現在＋トグルで過去分）
  - FVG ボックス（`addLineSeries` ではなく矩形描画が必要。lightweight-charts での表現方法は
    実装時に確認 — 既存に前例が無いため技術検証が要る）
  - トレンド背景色（SSL/BSL 間の `fill()` 相当。既存の markers 配列に相乗りできない要素）
- 表示トグルを指標設定パネルへ追加。ON のときだけ fetch する

## 4. ユーザー確認事項

| # | 確認事項 | こちらの推奨案 |
| :--- | :--- | :--- |
| 1 | 作業ブランチ | 専用ワークツリー `worktree-zone-break`（main 基点）。T3 スキーマ追加＋Parquet バックフィルを含むため変更種別 B、`--mode write` で確保 |　**REV** OK
| 2 | T3 カラム名 | `is_zone_break_bull` / `zb_ssl` / `zb_bsl` / `is_zone_break_weak` の4列で確定してよいか |  **REV** OK
| 3 | 全履歴依存リスク（§1 の「特有のリスク」） | 実装後、T3（SQLite直近730日）経路と Parquet 全期間バックフィル経路の収束性を検証する（§6.1）。乖離が大きければ設計変更（ウォームアップ期間の導入など）が必要になりうる — その場合はこの計画書 §7 に記録して再度相談する、という進め方でよいか | **REV** OK
| 4 | FVG ボックスのチャート描画方式 | lightweight-charts に矩形描画の標準APIが無いため、実装方式（複数の line series を組み合わせる等）は実装時に技術検証してから確定する。この計画書では「描画する」ことだけを確定事項とし、具体手段は §7 に随時記録する運用でよいか | **REV** OK
| 5 | Optuna 最適化（約12時間）の実施タイミング | この計画には含めない。単発バックテストで一次指標を確認した後、実施するかどうかは別途ユーザー判断（§2.2） | **REV** OK

## 5. 実装順序と進捗チェックリスト

- [x] §4 のユーザー確認事項に回答をもらう（2026-09-12、全項目 OK。§3.4/3.5 に継続ブレイク特殊
      フィルタ2種・`min_market_cap` 追加のレビュー指摘あり、反映済み）
- [x] **TDD**: `backend/tests/indicators/test_zone_break.py` を先に書く（初回 SSL/BSL 確定、
      内部フラクタル追従、終値ブレイクでの反転、FVG 生成/無効化、境界を設けたフォールバック探索の
      正しさ）— 2026-09-12コミット`4061575`、4テストケース。ImportErrorでの赤を確認後、
      実装完了後に境界探索の一致テストを1件追加（計5件、全pass）
- [x] `backend/indicators/zone_break.py` を実装（green、2026-09-12コミット `2c1c8d5`）。
      `detector_bsl/ssl_last_fractal` の無制限バックスキャンは、確定済みフラクタルを
      前向きに積み末尾参照するO(1)方式に置き換え（`fractal_high(j,0)`との数式的同値性で正当化）
- [x] `tmp/` に Pine 直訳に忠実な素朴な参照実装を作り、本実装と出力が一致することを検証
      （`tmp/zone_break_naive_port.py`。オーケストレーターが作成し400本のランダム合成データで
      一致をテスト化済み。`structure_pivot` の `tmp/sp_core.py` 方式と同じ位置づけ）
- [x] **TradingView の実チャートとの突合**（§6.1 の独立経路確認。数銘柄）
      — 2026-09-12、ユーザーがAAPL/SPY/NVDAの3銘柄で確認、完全一致
- [x] T3（SQLite直近730日）経路 vs Parquet 全期間バックフィル経路の収束性を検証（§1 のリスク）
      — 2026-09-12、AAPL/SPY/NVDAの3銘柄で実施、直近60本・最終バーは完全一致（§6.1参照）。
      広い銘柄セットでの確認は後続のサンドボックス検証時に追加で行う
- [x] sandbox-workflow に従いサンドボックスで一連の検証を行う（`--mode write`でプロビジョニング、
      実装・バックフィル・スクリーナー登録すべてサンドボックスで実施し本番は無変更のまま。
      **ただし収束性検証はAAPL/SPY/NVDAの3銘柄のみ**（§1のリスクに対して薄商い銘柄を含む
      広い銘柄セットでの検証は未実施。2026-09-16 code-review Angle B/C/altitudeで指摘。
      §9「既知の制限」参照）
- [x] `db/models.py` の `Indicator` に4カラム追加（2026-09-12コミット`8e74e8e`。
      implementerがレート制限で途中停止したため、models.pyの4カラム定義まではimplementerの
      成果を採用、`calculate.py`統合とサンドボックスDBへのALTER TABLE反映はオーケストレーターが
      引き継いで完了）
- [x] `indicators/calculate.py` に算出ステップを追加（同上コミット。AAPL/SPY/NVDAで
      `calculate_indicators()`経由の値がTradingView突合済みの値と一致することを確認済み）
- [x] 仮想カラム2種を3箇所（`screener_registry.VIRTUAL_COLUMNS` / `screener_router._VIRTUAL_COLUMNS`
      / `backtest_screener.apply_filters_to_df`）に登録（2026-09-12コミット`9e97db8`）
- [x] 特殊フィルタ4種（フリップ2種＋継続ブレイク2種、§3.4）を `screener_filters.py` と
      `screener_registry.EXPLICIT_SPECS` に登録（同上コミット）
- [x] `backend/tests/api/test_screener_parity.py` の `PARITY_CASES` に境界値ケースを追加
      （同上コミット。special種は機械導出で自動登録されるため、実際の作業はフィクスチャ
      データ側にzone_break用のprev/today値を追加することだった。4フィルタとも
      API経路・バックテスト経路のパリティを確認。`backend/tests/` 全体1699 passed）
- [x] Parquet 全期間バックフィルスクリプトを実装、既存カラム不変を検査
      （`backend/scripts/backfill_zone_break.py`、2026-09-12コミット`43a375a`。
      `backfill_structure_pivot.py`と同じ設計）
- [x] サンドボックスでバックフィルを実行し検査（同上コミット。6,764,686行/3,279銘柄、
      SSL/BSL確定済み99.7%、既存66列不変を確認。`backend/tests/`全体1699 passed維持）
- [x] `backtest_config.toml` に候補戦略を2本追加（§3.5: フリップ版 I1 / 継続ブレイク版 I2、
      2026-09-12コミット`2777df0`）
- [x] 型1バックテストを非最適化で単発実行し、Alpha 等の一次指標を確認（§6 検証プラン。
      結果は§6参照 — 単体では明確なエッジ無し、採否は現時点で否定的）
- [x] バックエンド全体 `pytest backend/tests/` 全件パス（1700 passed、この時点まで都度確認済み）
- [ ] **本番昇格**（ユーザー実施。エージェントは実行しない — API サーバー・日次更新の停止が
      必要で、実行対象が本番 `data/stocktool.db` / `data/parquet_master/` になるため）。
      手順は §8「本番昇格・マージの手順（ユーザー実施）」参照。スクリプト・ALTER TABLE文は
      準備済み・サンドボックスで検証済み
- [x] `GET /api/chart/{symbol_id}/zone_break` を実装、テスト追加（2026-09-13コミット`4eb1a69`。
      `build_zone_break_response`、テスト4件、`backend/tests/`全体1704 passed）
- [x] `frontend/src/types.ts` / `frontend/src/api/zoneBreak.ts` とテスト（2026-09-13コミット`9775074`。
      FVGボックスの描画方式は「lightweight-charts v4に矩形描画APIが無いため上端/下端の
      2本の線分（輪郭のみ）で表現」に決定。§4-Q4への回答）
- [x] `ChartPage` に描画とトグルを追加（コミット`614d907`。既定は非表示 — バックテストで
      単体のエッジが確認できなかったため。Playwrightで実ブラウザ動作確認済み、
      BSLライン描画・コンソールエラー無しを確認）
- [x] `frontend/src/api/__tests__` を含むフロントエンド全体 `npm test` / `npm run build`
      （62 passed、tsc型チェック・buildとも成功）
- [x] `tools/db_health_check.py --all --check-nulls`（サンドボックスで実施。全3264銘柄中
      要対応5銘柄・上流待ち20銘柄は全て既存の無関係な問題（`rs_ratio_e21`等のNULL・
      上流データの遅延）。`CRITICAL_COLUMNS`（sma_200/ema_21/rs_value/rs_ratio_e21/
      rs_momentum_e21）に zone_break 列は含まれない — `sp_pivot`/`sp_hl` と同じく
      未確定時は正当にNULL/0になる状態カラムのため、意図的に対象外）
- [ ] `doc/backend_specification.md` / `doc/frontend_specification.md` に追記
- [x] 本計画書を `doc/completed/` へ移動（型1の一次判定・フロントエンド表示まで完了したため。
      本番昇格・マージはユーザー実施、手順は§8.1に記載。Optuna 投入自体は別計画でよい）

### 作業中メモ

（未着手）

## 6. 検証プラン / 結果

```powershell
# 単体
$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/indicators/test_zone_break.py -v
# 全体（コミット前の義務）
$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/ -v

# 型1（非最適化・単発、フリップ版・継続ブレイク版の両方）
.\venv\Scripts\python.exe backend\backtest\backtest_runner.py --strategy I1_zone_break_flip
.\venv\Scripts\python.exe backend\backtest\backtest_runner.py --strategy I2_zone_break_breakout
```

### 2026-09-12 実施結果（サンドボックス、2021-03-26〜2026-03-26、5年）

| 戦略 | Trades | WinRate | PF | Expectancy | AvgGain | SPY | Alpha |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| I1_zone_break_flip（フリップ=初動） | 10,808 | 34.4% | 1.08 | +0.30% | +0.30% | +0.52% | **-0.22%** |
| I2_zone_break_breakout（継続ブレイク） | 9,818 | 37.6% | 1.14 | +0.55% | +0.55% | +0.56% | **-0.01%** |

**単体では明確なエッジが見えない**（I1はSPY平均保有比で負け、I2はほぼSPY並み）。
`min_market_cap` 以外の絞り込みを一切かけていない素朴な状態でこの結果であり、
本計画の背景（§1「前回の会話で...RS/Trend Templateで絞った後の確認フィルタとして
型1バックテストで有意性を検証してから採否判断すべき、という方針で合意した」）どおり、
**単体シグナルとしての採否は現時点で否定的**。RS/Trend Templateとの組み合わせでの
再検証、またはOptuna投入するかはユーザー判断（§2.2でスコープ外と確定済み）。

### 2026-09-13 追検証（RS/Trend Template確認フィルタとの組み合わせ、ユーザー指示）

単体でAlphaが出なかった結果を受け、当初の仮説（本計画書の背景欄「RS/Trend Templateで絞った後の
確認フィルタとして...検証してから採否判断すべき」）どおり、`is_trend_template = true` +
`min_rs_ratio_rank_e21 = 0.7`（RS上位30%）を追加した確認フィルタ版を
`backtest_config.toml` に追加（J1/J2、非最適化の単発実行、こちらもコミット済み）。

| 戦略 | Trades | WinRate | PF | Expectancy | AvgGain | SPY | Alpha |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| I1_zone_break_flip（単体） | 10,808 | 34.4% | 1.08 | +0.30% | +0.30% | +0.52% | -0.22% |
| J1_zone_break_flip_confirmed（RS/TT確認） | 4,252 | 36.3% | 1.16 | +0.56% | +0.56% | +0.52% | **+0.03%** |
| I2_zone_break_breakout（単体） | 9,818 | 37.6% | 1.14 | +0.55% | +0.55% | +0.56% | -0.01% |
| J2_zone_break_breakout_confirmed（RS/TT確認） | 7,648 | 38.2% | 1.12 | +0.42% | +0.42% | +0.43% | -0.01% |

**RS/Trend Templateで絞ってもAlphaはほぼ横ばい（誤差レベル）**。J1はI1よりわずかに改善
（-0.22%→+0.03%）したが、なお有意とは言えない水準。J2はI2から変化なし。
**「RS/Trend Templateの確認フィルタとして使う」という当初の仮説も、この非最適化の
単発実行では支持されなかった**。Optunaで最適化すれば改善する可能性はあるが、
ベースラインでここまでフラットだと最適化コスト（約12時間）に見合うかは疑わしい
——という判断材料として記録し、投資判断（Optuna投入・採否）はユーザーに委ねる。

# フロントエンド
cd frontend; npm test; npm run build
```

### 6.1 反証 — この結論が誤りだとしたら、何が観測されるはずか

- **この結論が誤りだとしたら観測されるはず**:
  「Python 移植が Pine 原文と同一の状態遷移をする」という結論が誤りなら、
  TradingView の実チャート上の SSL/BSL 価格・現在のトレンド方向・FVG ゾーンの生存/無効化と、
  Python 実装の出力が数銘柄で一致しないはずである。また「T3 の日次経路（SQLite 直近730日）と
  Parquet 全期間バックフィル経路が同じ値を出す」という結論が誤りなら、両経路を突合したときに
  直近バーの状態（方向・SSL/BSL 価格）が有意な割合で食い違うはずである
  （`counter_trend` の先例では最終バー12%・直近60本44%の不一致として現れた）。
- **独立経路での確認**:
  1) 数銘柄で TradingView に実際にインジケータを表示し、Python 実装の出力と目視・数値で突合する
     （コードを読んで「合っていそう」と判断するだけでは自己参照になる — `counter_trend` の移植で
     「要約から実装して2箇所取り違えた」実例があり、コード全文を読める今回もそれだけでは
     不十分。実チャートという独立ソースでの確認を必須とする）。
     **2026-09-12 ユーザーがTradingViewで確認、AAPL/SPY/NVDAの3銘柄とも完全一致
     （方向・SSL・BSL・直近フリップ日）**。
  2) T3（SQLite直近730日で計算した結果）と Parquet 全期間バックフィルの結果を `(symbol_id, date)`
     で突合し、直近バーの不一致率を測定する。**2026-09-12実施済み（`tmp/zone_break_convergence_check.py`、
     AAPL/SPY/NVDAの3銘柄、tmpのため使い捨てスクリプト）**:

     | 銘柄 | 全期間の方向不一致 | 直近60本の方向不一致 | 最終バー |
     | :--- | :--- | :--- | :--- |
     | AAPL | 8/502 | 0/60 | 完全一致 |
     | SPY  | 7/502 | 0/60 | 完全一致 |
     | NVDA | 0/502 | 0/60 | 完全一致（ただし ssl/bsl の値は全期間で30/502不一致） |

     `counter_trend` の前例（最終バー12%・直近60本44%不一致）のような致命的な乖離ではなく、
     3銘柄とも**直近60本・最終バーは完全一致**（NVDAはssl/bsl値に全期間中の不一致が残るが
     直近では収束）。730日の窓で状態機械が「忘れる」のに十分な助走期間になっていると見られる。
     **ただし検証は流動性の高い3銘柄のみ**。薄商い銘柄でも同様に収束するかは、後続の
     「サンドボックスでバックフィルを実行し検査」のチェックリスト項目でより広い銘柄セットに
     ついて確認する（§1で懸念していたリスクが「解消」ではなく「限定的な追加確認で足りそう」
     という段階の暫定結論）。

### 6.2 転記の完全性

- **転記元**: 直前の会話（ユーザーとの方針合意）/ **元の件数**: なし（新規設計であり、
  列挙されたチェックリストや issue からの転記ではない） / **本計画書の件数**: — /
  **差分の説明**: なし

## 7. 途中発生した課題

- **2026-09-12 zone_break.py実装の差分規模**: `backend/indicators/zone_break.py`(新規395行) +
  テスト追記(49行)で計444行、変更種別Aの閾値(200行)を超過。`doc/agent_execution_rules.md` §10.2の
  運用に従い、**本ブランチをmainへmergeする前に `/code-review` をブランチ単位で1回通す**必要がある
  （まだ実装途中のため、計画完了に近づいた段階でまとめて実施する想定。個別コミットのたびには行わない）。
- **2026-09-12 セッション引き継ぎ**: 計画書作成元の `stocktool-fe` セッションが Remote Control
  切断のため、本セッション（オーケストレーター）が引き継いだ。引き継ぎ情報源は本計画書と
  Remote Control 経由でユーザーが把握していた会話内容の要約のみ（`stocktool-fe` の会話ログ本体は
  未参照）。§4 の回答はユーザーから本計画書への直接書き込み（`**REV** OK` 等）で取得済みのため、
  合意内容の欠落リスクは低いと判断。
- **§3.5 候補戦略の設計変更（ユーザーレビュー指摘への対応）**: 当初案（フリップ + `is_zone_break_weak`
  のみ）だと「転換初日」しか捉えられず、強いトレンドが継続する中で新しいゾーンを何度もブレイクする
  値動きを取りこぼす、という指摘。フリップ（初動）と継続ブレイクは意味が異なるシグナルであり、
  1つの合成フィルタに混ぜると型1バックテストでの解釈可能性が落ちる（`backtest-statistical-analysis`
  skill の観点）と判断し、**継続ブレイク用の特殊フィルタ2種を新設し、候補戦略も2本に分離**した
  （§3.4/3.5）。`backtest_config.toml` に OR 条件を書く機構が無いことを確認済み（1戦略=AND条件のみ）。
  `min_market_cap` は両戦略にユーザー指定どおり追加。継続ブレイクの判定境界（フリップ当日の除外方法、
  浮動小数点誤差）は TDD フェーズで確定する（実装前のため未確定）。

## 8. スコープ外・残作業

- Optuna 全体最適化（約12時間）の実行 — 単発バックテストの結果を見てユーザーが判断
  （§6の結果はI1/I2/J1/J2いずれもAlphaがほぼ横ばいで、投資対効果は疑わしいという判断材料あり）
- ショート側の概念は無いため対象外（§2.2）
- `exit_type` の差し替え — 対象外（§2.2、`structure_pivot` の前例で中止済み）

### 8.1 既知の制限（2026-09-16、`/code-review` ultra相当の8観点並列レビューで検出）

**本番昇格前に必ず読むこと。** 8つの独立したレビュー観点（altitude/Angle A/Angle B/Angle C/
efficiency/simplification/reuse/CLAUDE.md規約）のうち3つ（altitude、Angle C、Angle A）が
**収束して同一の構造的リスク**を指摘した:

> **`zone_break` の状態（`is_bull`/`ssl_bl`/`bsl_bl`）は `structure_pivot` と異なり構造的に
> 無制限（トレンドレッグが何年も続きうる）。したがって「T3日次計算がSQLite直近730日
> ウィンドウの先頭から状態機械を再起動する」ことは、`structure_pivot`（`max_len`で構造の
> 寿命が数本〜十数本に収まる）とは違って一般には正当化されない。**
>
> §6.1 の収束性検証は **AAPL/SPY/NVDAの3流動性大型銘柄のみ**で行っており、直近60本・
> 最終バーの完全一致を確認したが、これは経験的な観測であって `structure_pivot` のときの
> ような構造的な保証ではない。**薄商い銘柄・長期トレンド銘柄（730日を超えて反転していない
> 銘柄）では、日次のT3再計算がParquetバックフィルで確定した正しい状態と乖離したまま
> 収束しない可能性がある**（`counter_trend`の前例と同じ失敗類型。詳細:
> `doc/completed/structure_pivot_screener_plan.md` §5.6）。
>
> 加えて、この乖離は**一度きりのバックフィル時点の問題ではなく毎日発生しうる**
> （`backend/pipeline/phases/t3_indicators.py` の日次差分計算も
> `backend/scripts/weekly_maintenance.py` のT2/T3不整合修復パスも、同じ730日ウィンドウで
> `zone_break_series` を再実行するため）。
>
> **対応方針（2026-09-16、ユーザー判断で確定）**: **リスクを許容し現状のまま昇格する**。
> 判断根拠: 730日（約2年）を一度も反転せずに超えるトレンドレッグは現実の個別株では稀
> という経験則、および§6.1の3銘柄での実測（全期間では方向不一致が残るものの、**直近60本・
> 最終バーは3銘柄とも例外なく収束**しており、長期間ノーリセットのまま乖離し続けるケースは
> 観測されなかった）に基づく。全銘柄でのSQLite/Parquet収束性の追加検証、または
> `current_direction`・T3のzone_break列を常にParquet全期間から計算する方式への設計変更
> （`structure_pivot`の`full_range`引数と同様の「ホットキャッシュは信頼できない」前提への
> 転換）は、Alphaが確認できOptuna投入を検討する段階になったときに改めて評価する
> （現状は単体・RS/Trend Template確認版いずれもAlphaがほぼ横ばいで、追加投資の優先度は低い）。

その他、レビューで検出し**修正済み**の項目:
- `zb_dist_ssl_pct`/`zb_dist_bsl_pct` が未確定（`zb_ssl`/`zb_bsl`=0.0）を除外するガードを
  持たず、`sp_dist_pivot_pct`のNaN伝播と違って未確定銘柄が close比±100%という尤もらしい値で
  紛れ込むバグ（Angle C）。`screener_router.py`/`backtest_screener.py`で0以下をNULL/NaNに
  修正済み
- `backfill_zone_break.py`が`--columns`でzb_ssl/zb_bslを除外した場合に`KeyError`になるバグ
  （Angle A）。`columns[0]`を参照するよう修正済み
- 本ドキュメントの`doc/in_progress/zone_break_plan.md`パス参照6箇所が、計画書移動後に
  リンク切れになっていた（Angle B）。`doc/completed/zone_break_plan.md`に修正済み

**修正しなかった項目**（意図的な判断、理由付き）:
- `zone_break.py:118`（Bear側BSL初期化で`bsl_bl = 0.0`）はPine原文L92-93の逐語訳であり
  翻訳ミスではない（Angle Aは「Bull側との非対称」を指摘したが、Pine原文自体が非対称。
  `high_fractals`が空の状態でBearへフリップする極端なケースでのみ影響し、数年分の実データでは
  事実上発生しない）。原文に忠実に、あえて未修正のまま残す
- `is_zone_break_bull_breakout`/`bear_breakout`は、docstringが述べる「isConf_bl由来の継続
  ブレイク」より実際には広い条件（`int_ssl_bl<=0`の間の素朴なBSL追従更新でも発火しうる、
  `zone_break.py:113`）で発火する（Angle A）。§6のI2/J2の結果はこの実装どおりの挙動を
  測定したものであり無効ではないが、フィルタの意味は「確定した継続ブレイク」ではなく
  より広い「トレンド継続中のBSL上昇」である。今回は docstring 側の記述を実態に合わせて
  修正するに留め、フィルタ自体の意味を狭める実装変更（要: 内部の`is_conf_bl`遷移を
  外部公開する）は将来の改善候補として残す

### 8.2 本番昇格・マージの手順（ユーザー実施）

コード側の実装・テストは完了。§8.1の既知の制限（SQLite/Parquet収束性の検証範囲）は
**ユーザー判断でリスク許容のうえ昇格する方針が確定済み**。以下2ステップは運用ルール上
エージェントが単独実行できないため、ユーザー自身が実施すること。

**① 本番データ昇格**（APIサーバー・日次更新パイプラインを止めてから）:
```powershell
# 1. 本番 stocktool.db に4カラムを追加（sqlite-wal-handling規約: WAL/busy_timeout/synchronous の
#    3プラグマ必須 + 書き込みは BEGIN IMMEDIATE。promote_structure_pivot.py と同じ手順）
python -c "
import sqlite3
conn = sqlite3.connect('data/stocktool.db')
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
conn.execute('PRAGMA synchronous=NORMAL')
conn.execute('BEGIN IMMEDIATE')
conn.execute('ALTER TABLE indicators ADD COLUMN is_zone_break_bull BOOLEAN')
conn.execute('ALTER TABLE indicators ADD COLUMN zb_ssl FLOAT')
conn.execute('ALTER TABLE indicators ADD COLUMN zb_bsl FLOAT')
conn.execute('ALTER TABLE indicators ADD COLUMN is_zone_break_weak BOOLEAN')
conn.commit(); conn.close()
"

# 2. Parquet全期間バックフィル（--apply で本番の latest_master.json を差し替え。旧世代は削除されない）
python backend/scripts/backfill_zone_break.py --source-dir "data/parquet_master" --out-dir "data/parquet_master" --apply

# 3. SQLiteホットキャッシュ（直近730日）を新しいParquetから復元
#    restore_sqlite_cache_from_parquet（parquet_cache_manager.py）または既存の復元手順に従う
```
サンドボックスでの実施結果（2026-09-12）: 6,764,686行 / 3,279銘柄、SSL/BSL確定済み99.7%、
既存66列は不変を検査済み（`backfill_zone_break.py` の `verify()`）。AAPL/SPY/NVDAでTradingView
実チャートとの一致・SQLite/Parquet収束性（直近60本・最終バー）も確認済み（§6.1）——
**ただしこの3銘柄に限る（§8.1参照）**。

**② mainへのマージ**:
```powershell
git checkout main
git merge worktree-zone-break
```
本ブランチは444行超の新規モジュール（`zone_break.py`）を含む変更種別Bのため、
merge前に `/code-review` をブランチ単位で1回通すことを推奨する
（`doc/agent_execution_rules.md` §10.2、本計画書§7で記録済みの要件）。
**2026-09-16 に実施済み**（8観点並列レビュー、結果は§8.1に反映済み）。
