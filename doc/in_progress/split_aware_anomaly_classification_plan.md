# 価格アノマリー分類に分割メタデータを渡す 計画書

- **ステータス**: 🚧 進行中（§4 は 2026-09-10 に全件合意済み）
- **実施者**: AI エージェント（Claude Opus 5）＋ ユーザー
- **開始日**: 2026-09-10 / **完了日**: —
- **作業ブランチ**: `feat/split-aware-anomaly`（`.claude/worktrees/split-aware-anomaly`・`--mode read`）
- **対象 issue / 関連ドキュメント**:
  - `doc/issue_list.md` P1 🔴「`scan_price_anomalies.py` の分類が分割を隠している（2026-09-09 発見）」
  - 先行: `doc/completed/split_consistency_scan_plan.md`（第1・2段階）
  - `doc/completed/split_anomaly_noise_reduction_plan.md`（現行分類器の設計経緯）
  - `.claude/skills/upstream-data-diagnosis/SKILL.md` §6.2

## 1. 背景と目的

### 事象

`classify_price_jump()` は **分割メタデータを一切見ていない**。比の大きさと同日件数だけで
判定するため、分割と実際の値動きを区別できない。2026-09-10 に補正した本物の破損2件は
**どちらもレポートに入っていたのに捨てられていた**:

```
IESC  2026-08-24  ratio=0.4731  same_day_count=4  → market_wide
WLFC  2026-07-20  ratio=0.3294  same_day_count=1  → real_move
```

`MARKET_WIDE_MIN_SYMBOLS = 4` は「同日に4件の分割が重なると全部を市場全体の動きとして消す」。
2026-08-24 に飛んだ4銘柄のうち `AVB`（大型REIT・2026-08-17 に ×2.793）と `IESC`（2026-08-24 に ×2.0）は
無関係な分割が重なっただけで、AvalonBay が1日で63%下落したわけではない。

同じ型の失敗は 2026-09-04 の `auto_adjust=False` 事故でも起きている（実際に -2.2% だった
2018-04-02 が `market_wide` に分類され、409銘柄の系統的な段差を素通りさせた）。

### 着手前のベースライン（2026-09-10 実測）

`data/maintenance_reports/price_anomalies.csv`（2026-09-04 生成・IESC/WLFC 補正前）より:

| 指標 | 値 |
| :--- | ---: |
| アノマリー総数 | 1,357 件 / 635 銘柄 |
| 期間 | 2017-01-06 〜 2026-09-01 |
| `split_suspect`（要対応として出るもの） | **1 件** |
| `real_move` | 475 件 |
| `market_wide` | 220 件 |
| `low_liquidity` | 613 件 |
| `virtual` | 48 件 |
| **極端比（<0.6 / >1.8）かつ `market_wide`/`real_move`** = 救済候補 | **659 件 / 482 銘柄** |
| うち直近1年 | 116 件 / 91 銘柄 |
| 仮想テーマを除く実在銘柄（照合対象になりうる） | 629 銘柄 |

分割記録は**手元のどこにも保存されていない**（`grep -rn "Stock Splits\|actions=True" backend/`
のヒットは `scan_split_consistency.py:262` の1箇所のみ。DB にも Parquet にも splits 列は無い）。

### 完了時の状態

- 記録された分割日と一致する段差は、`market_wide` / `real_move` / `low_liquidity` より
  **優先して `split_suspect` になる**。
- 既知の正解2件（`IESC` / `WLFC`）が要対応として出る。
- 分割記録が無い・カバー期間外のアノマリーは、**その事実が件数付きで報告される**（沈黙しない）。

## 2. スコープと設計判断

### 2.1 変更すること

| # | 何を | 対象 |
| :--- | :--- | :--- |
| 3-A | 分割記録との照合を行う純関数の追加 | `backend/indicators/price_anomaly.py` |
| 3-B | `classify_price_jump()` に照合結果を渡す経路 | 同上 |
| 3-C | 分割記録の永続化（既存フェッチの副産物） | `backend/scripts/scan_split_consistency.py` |
| 3-D | 分類器への供給とカバレッジ報告 | `backend/scripts/scan_price_anomalies.py` / `backend/scripts/weekly_maintenance.py` |
| 3-E | 2つの呼び出し側が共有する読み込み・照合・カバレッジ集計 | `backend/data_collection/split_records.py`（新規）＋ `price_anomaly.py` |

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 |
| :--- | :--- |
| `scan_price_anomalies.py` から yfinance を直接叩くか | **叩かない**。このスクリプトの設計価値は「SQLite にも外部にも触らず 4.7秒で全期間を見る」こと（冒頭 docstring）。ネットワークを入れると週次監査の実行時間と失敗経路が変わる。分割記録は**既にフェッチしている `scan_split_consistency.py` の副産物**として渡す |
| 分割記録を DB / Parquet のテーブルにするか | **しない**。スキーマ変更は種別 B になりサンドボックス手順が要る。用途はレポート専用で、再生成可能な JSON で足りる |
| 分割を「自動で適用」するか | **しない**。`scan_split_consistency.py` の原則をそのまま踏襲する（2026-09-04 の事故は「自動で上流へ合わせた」ことが原因）。本計画は**分類の優先順位だけ**を直す |
| `MARKET_WIDE_MIN_SYMBOLS = 4` を引き上げるか | **引き上げない**。閾値をいじっても「分割かどうか」の情報は増えない。COVID の実データ（2020-03-09 に34銘柄）で校正した値であり、根拠のある側を壊すことになる |
| 既存の分類ラベルを増やすか | **増やさない**。`split_suspect` に寄せる。読み手（週次レポート）が見るバケツを増やさない |
| `undecided` の扱い | 変更しない。分割記録と一致すれば `split_suspect` が優先されるので自然に減る |

## 3. 変更内容

### 3-A. `find_matching_split()` — 純関数の追加

- **何を**: `(ticker, date, ratio)` と分割記録から、「この段差は記録された分割で説明できるか」を返す。

  ```python
  def find_matching_split(ticker, date, ratio, split_records, *,
                          window_days=SPLIT_DATE_WINDOW_DAYS,
                          tolerance=SPLIT_JUMP_TOLERANCE) -> dict | None
  ```

  判定は2条件の **AND**:
  1. 記録された分割日が段差の日付から `±window_days` 以内
  2. 比が `1/factor` に `tolerance` の相対許容で一致
- **なぜ AND か**: 日付だけでは足りない（分割日に本物の急落が重なりうる）。比だけでも足りない
  （1/2 前後の比は暴落と区別できない — 現行 `SPLIT_RATIO_LO/HI` がその帯を避けているのと同じ理由）。
- **なぜ日付に幅が要るか**: `AVB` は段差が 2026-08-24、上流の分割記録が 2026-08-17 で **7日ずれる**。
  厳密一致では取り逃す。→ 幅の値は §4-2 で確定する。
- **識別できない領域では判定しない**: `factor ≈ 1.0`（株式配当）は `1/factor ≈ 1.0` で
  「未適用」と「適用済み」が重なる。`scan_split_consistency.py` の `MIN_DISCRIMINABLE_GAP = 0.15`
  と同じガードを入れて `None` を返す（同書の実測で、検出18件中16件がこの誤検出だった）。
- **影響範囲**: 新規の純関数。既存の呼び出しに影響なし。

### 3-B. `classify_price_jump()` の優先順位に組み込む

- **何を**: `row` に `split_match`（3-A の戻り値）があれば、**`virtual` の次**で `split_suspect` を返す。
  与えられなければ**現状と完全に同じ挙動**（後方互換。呼び出し側を段階的に移せる）。
- **新しい優先順位**: `virtual` > **`split_match`** > `market_wide` > `low_liquidity` > `split/undecided/real`
- **影響範囲**: `low_liquidity` より上に置くため、低位株の未調整分割も要対応に上がる（§4-3 で確認）。

### 3-C. 分割記録の永続化（`scan_split_consistency.py`）

- **何を**: 既に `fetch_batch()` が取得している `rec["splits"]` を、検出の有無に関わらず
  `data/maintenance_reports/split_records.json` に全件書き出す。

  ```json
  {"generated_at": "...", "start": "2024-09-11", "years": 2,
   "tickers_fetched": 2956, "failed": [],
   "splits": {"IESC": [["2026-08-24", 2.0]], "AVB": [["2026-08-17", 2.793]]}}
  ```

- **なぜ**: 追加のネットワーク費用がゼロ。既存の取得を捨てているだけの状態を直す。
- **影響範囲**: 出力ファイルが1つ増えるだけ。既存の検出ロジック・CSV は不変。
  `data/` は git 管理外なので、新規チェックアウトには**存在しない前提**で扱う。

### 3-D. 供給とカバレッジ報告

- **`scan_price_anomalies.py`**: `--splits <path>`（既定は上の標準パス）。読めたら各行に
  `split_match` を付けて分類する。レポート冒頭に必ず出す:
  - 分割記録の生成時刻・カバー期間・銘柄数
  - **カバー期間外のアノマリー件数**（2017 年まで遡るので必ず出る）
  - 記録が読めない場合は「分割記録なしで分類した」と明示し、従来の分類にフォールバック
- **`weekly_maintenance.py`**: 同じキャッシュを読む。恒常的な監査経路はこちらなので、
  ここを直さないと修正が運用に届かない。
- **要対応の内訳表示（4-3）**: `split_suspect` の一覧は「**要対応 N件（うち低流動 M件）**」と
  内訳を出し、低流動ぶんは一覧の後段にまとめる。`adv21` と価格を列に出して読み手が
  優先度を判断できるようにする。
- **なぜ「沈黙しない」か**: 記録が古い / 無い状態で従来分類を返すと、
  **ゲートがあるように見えて何も見ていない**という最悪の形になる（`doc/issue_list.md` P2 で
  自ら書いた形骸化の型）。

### 3-E. 共通化（4-5）

2つの呼び出し側（Parquet 全期間 / SQLite 730日）が**別々に JSON を読んで別々に照合する形にはしない**。
D-2 リファクタリング（スクリーナー特殊フィルタの二重実装解消）と同じ規律を適用する。

| 置き場 | 何を | なぜそこか |
| :--- | :--- | :--- |
| `backend/indicators/price_anomaly.py` | `find_matching_split()`・`summarize_split_coverage()`（**純関数**） | 既存の分類器と同居。副作用を持たせない |
| `backend/data_collection/split_records.py`（新規） | `save_split_records()` / `load_split_records()`（JSON I/O・既定パス解決） | 取得・永続化は data_collection の責務。`indicators/` は純計算モジュールなので I/O を置かない |

呼び出し側に残るのは「行 dict を組んで渡す」だけにする。

- **影響範囲**: 新規モジュール1つ。既存の `data_collection/` 内の他モジュールには触れない。

## 4. ユーザー確認事項

**2026-09-10 に全件合意済み。** 以下は判断結果。

| # | 論点 | 判断 |
| :--- | :--- | :--- |
| 4-1 | 分割記録の入手経路 | **3-C（`scan_split_consistency.py` の副産物）を採用**。`scan_price_anomalies.py` のオフライン性（SQLite にも外部にも触らず4.7秒）を保つ。代案（同スクリプトから 629銘柄ぶん yfinance を叩く）は不採用 |
| 4-2 | 日付の許容幅 `SPLIT_DATE_WINDOW_DAYS` | **±10 暦日を暫定値として採用**。`AVB` の実測ずれが7日。**この数値に根拠は無い**（`MARKET_WIDE_MIN_SYMBOLS` と同じ扱い＝運用しながら調整する前提）。チェックリスト2の実測でずれの分布を出し、外れていれば見直す |
| 4-3 | `low_liquidity` より上に置くか | **置く（隠さない）＋レポートで内訳を出す**。理由: 未調整分割は「取引しないから無害」ではなく、**Parquet マスタが壊れたまま残り T4 の横断ランクにも乗る**。`low_liquidity` は「破損かどうか」ではなく「**対応する価値があるか**」の足切り（$5 / $1M日）であって、破損の判定を上書きしてよい根拠にならない。ただし要対応が数百件になると読まれなくなるため、レポートは「**要対応 N件（うち低流動 M件）**」と内訳を出し、低流動ぶんは一覧の後段にまとめる。**新しい分類ラベルは増やさない**（§2.2 維持）。同列に出すか後段に回すかの最終決定はチェックリスト2の実測後 |
| 4-4 | 分割記録のカバー期間 | **まず 2年（既定）**。カバー期間外の件数をレポートに出して可視化し、必要なら後日 `--years 10` で1回だけ長期版を作る |
| 4-5 | `weekly_maintenance.py` も直すか | **直す。かつ共通化できるところは共通化する**（下記 3-E）。両呼び出し側が別々に JSON を読んで別々に照合する形にはしない |
| 4-6 | 作業環境 | **ワークツリー＋`--mode read`**（種別 A）。本体チェックアウトは別セッション（`workflow_feedback_loop_plan.md`）が使用中で、`tools/hooks/run_related_tests.ps1` と `backend/indicators/structure_pivot.py` に未コミットの一時変更がある。`pytest backend/tests/` 全体は `--mode write` が要る（rules §10.3）ので、検収時の全体実行は本体で行うか write でプロビジョニングする |

### `low_liquidity` の内訳（4-3 の判断根拠・2026-09-10 実測）

| | 件数 |
| :--- | ---: |
| `low_liquidity` 合計 | 613 件 / 224 銘柄 |
| 価格 ≤ $5 で落ちた | 335 |
| 価格は OK だが 売買代金 ≤ $1M で落ちた | 278 |
| **うち極端比（<0.6 / >1.8）＝ 要対応へ移りうる上限** | **578 件 / 217 銘柄**（直近1年 55件） |
| `adv21` の中央値 | $30,156 /日（戦略のハード床 $2M の 1/67） |

**手動操作が必要な項目**: 3-C の実行（`scan_split_consistency.py` は yfinance を叩くため
ネットワークが要る。2,956銘柄 × 2年で数分）。**破壊的変更・非互換はなし**（既存データを一切書き換えない）。

## 5. 実装順序と進捗チェックリスト

**影響の小さい順**。1〜2 は実装前の実測で、ここで §4-2 / §4-3 が確定する。

- [ ] 1. `scan_split_consistency.py --years 2` を実行し、分割記録を取得して JSON 化（3-C）
- [ ] 2. **照合だけを行う使い捨てスクリプト**（`tmp/`）で、救済候補659件と分割記録を突き合わせる。
      日付ずれの分布・一致件数・`low_liquidity` からの流入件数を出す → §4-2 / §4-3 を確定
- [ ] 3. `find_matching_split()` のテストを書く（red）— `backend/tests/indicators/test_price_anomaly.py`
      に追加。`IESC` / `WLFC` / `AVB` の実データを固定値ケースにする
- [ ] 4. `find_matching_split()` を実装（green）（3-A）
- [ ] 5. `classify_price_jump()` の優先順位変更のテスト → 実装（3-B）。
      **`split_match` を渡さない既存ケースが全て不変であることを含める**
- [ ] 6. `data_collection/split_records.py` を切り出す（3-E）。テストは
      `backend/tests/data_collection/test_split_records.py`
- [ ] 7. `scan_price_anomalies.py` に `--splits`・カバレッジ報告・要対応の内訳表示を追加（3-D）
- [ ] 8. `weekly_maintenance.py` を同じ共通関数に繋ぐ（3-D / 3-E）
- [ ] 9. 全期間スキャンを再実行し、§6 の before/after を記入
- [ ] 10. `doc/issue_list.md` の該当項目を解決済みに更新、`.claude/skills/upstream-data-diagnosis/SKILL.md`
      §6.2 に分類の優先順位を追記
- [ ] 11. 本計画書を `doc/completed/` へ移動

### 作業中メモ

未着手（計画レビュー中）。

## 6. 検証プラン / 結果

| 対象 | 検証方法 | 期待結果 | 結果 |
| :--- | :--- | :--- | :--- |
| 3-A | 単体テスト | `IESC`(0.4731 vs ×2.0)・`WLFC`(0.3294 vs ×3.0 想定)・`AVB`(0.370 vs ×2.793・7日ずれ) が一致。`factor≈1.0` は `None` | — |
| 3-B | 既存テストの不変 | `split_match` 未指定なら現行と完全一致 | — |
| 3-B | 優先順位 | 分割一致は `market_wide` / `low_liquidity` を上書きする | — |
| 3-D | 記録が無い場合 | 従来分類 ＋「分割記録なし」の明示 | — |
| 3-D | カバー期間外 | 件数が報告に出る | — |
| 全体 | 全期間スキャン before/after | `split_suspect` 1件 → N件。**既知の正解2件が含まれる** | — |
| 全体 | `pytest backend/tests/ -q` | 着手前と同数 pass | — |

### 6.1 反証 — この結論が誤りだとしたら、何が観測されるはずか

- **この結論が誤りだとしたら観測されるはず**:
  本計画の前提は「**分割メタデータを見れば、隠れていた本物の破損が要対応に上がり、
  かつ要対応の件数は読める量に収まる**」。誤りなら次が観測される。
  - **氾濫**: 救済候補659件の大半が `split_suspect` になる。→ 要対応バケツが 1件から数百件になり、
    **読まれなくなる**（`doc/issue_list.md` P3 で自ら書いた「無いより悪い」形）。
    この場合、真因は分類ではなく「未適用分割が実際に大量にある」ことになり、対応は
    分類変更ではなく一括補正の設計に変わる。
  - **空振り**: 既知の正解2件が上がらない。→ 日付窓か比の許容幅が誤っている（§4-2）。
  - **誤検出**: 分割日に本物の急落が重なった銘柄が `split_suspect` に化ける。
    → 上がった件を個別に見て、比が `1/factor` に一致した理由が偶然かを確認する。
- **独立経路での確認（実装前に実施する — チェックリスト 2）**:
  **この検証は実装を待たずに実行できる**。分割記録を取得して照合するだけで、
  「何件が `split_suspect` に移るか」「日付ずれの分布はどうか」が**実装前に**分かる。
  §4-2 / §4-3 の数値はこの実測で決める（先に実装して後から辻褄を合わせない）。
- **独立性の限界（正直に記す）**:
  照合に使う分割メタデータ（yfinance `Stock Splits`）と、被験対象である調整済み価格系列は
  **同じ供給者**である。完全に独立した経路ではない。ただし `MNST` の件が示すとおり
  **同一供給者の中で metadata と時系列は矛盾しうる**ので、この突き合わせには検出力がある
  （`scan_split_consistency.py` の検査A がまさにこの構造）。
  **上流が分割記録自体を持たないケース（`SOXS` / `UAVS` 等299件）は本計画でも検出できない。**
  そこは第3段階（moomoo 等の独立ソース）の領分で、`doc/issue_list.md` P1 に残す。
  `split_suspect` に上がった件の最終確認は、引き続き TradingView / moomoo での実測に委ねる。

### 6.2 転記の完全性

- **転記元**: `doc/issue_list.md` P1 🔴「`scan_price_anomalies.py` の分類が分割を隠している」
  / **元の件数**: 1項目（対応案1つ）/ **本計画書の件数**: 1項目（3-A〜3-D に分解）
  / **差分の説明**: issue の対応案「`classify_price_jump()` に分割記録を渡し、分割日に一致する
  段差を優先して `split_suspect` にする」がそのまま 3-A/3-B。
  **issue に書かれていなかった論点を2つ追加した**: ①分割記録の入手経路（issue は
  「`scan_split_consistency.py` と実装を共有できる」と書くが、同スクリプトは yfinance から
  都度取得しており、共有できる**保存済みデータが存在しない**）②日付の許容幅（`AVB` の7日ずれ）。

## 7. 途中発生した課題

（未着手）

## 8. スコープ外・残作業

- **上流が分割記録を持たないケースの検出**（`doc/issue_list.md` P1 🟡 第3段階）。本計画では扱わない。
- **未適用分割の一括補正**。本計画は検出の分類だけを直す。補正は引き続き
  `adjust_symbol_split.py` による個別対応（自動化しない方針は §2.2 のとおり）。
- **2年より前のカバー**（§4-4）。必要と分かった時点で長期版を1回作る。
