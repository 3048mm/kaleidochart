# SEC EDGAR によるティッカー変更・上場廃止の追随 計画書

- **ステータス**: 🚧 計画レビュー完了・着手待ち
- **実施者**: AI エージェント（Claude Opus 5）
- **開始日**: 2026-08-05 / **完了日**: —
- **作業ブランチ**: 未定（`worktree-sec-ticker-tracking` を想定）
- **対象 issue / 関連ドキュメント**:
  `doc/issue_list.md` P1「17銘柄の履歴消失はコーポレートアクションだった」/
  `.claude/skills/upstream-data-diagnosis/SKILL.md` §3 / §5.1 /
  `backend/scripts/rename_symbol.py` / `backend/scripts/retire_stale_symbols.py`

## 1. 背景と目的

### きっかけ

2026-08-02、17銘柄が全履歴を失い 1〜11 行になった。Yahoo の挙動（`firstTradeDate` が
最近の日付に打ち直され、時系列だけが切れる）から「**上流のデータ不具合**」と診断し、
旧 Parquet 世代からの復元・T4/T5 全期間再計算・VACUUM に**丸一日を費やした**。

2026-08-04 に SEC EDGAR で照会したところ、**15件は実際のコーポレートアクション**だった。
**数分で真因が判明した。**

```
BLD   TopBuild → QXO Insulation, LLC     Form 15-12G 2026-07-13（買収・登録抹消）
LC    LendingClub → Happen, Inc.         現ティッカー HAPN（改称）
SCVL  Shoe Carnival → Shoe Station Group 現ティッカー SHOE（改称）
```

**教訓**: 上流の挙動から原因を推測してはいけない。Yahoo だけを見ていると
「上流の不具合」に見えるが、権威あるソース（SEC）に当たれば事実で確定できる。

### 目的（成功条件）

コーポレートアクションを**週次で自動検知し、退役は自動適用・改称はガード付きで自動適用**する。
人が判断すべき残りだけをレポートに出す。**次に同じことが起きたとき、誤診に一日を費やさない。**

### ベースライン（2026-08-05 時点）

| 項目 | 現状 |
| :--- | :--- |
| コーポレートアクションの検知 | **手動のみ**。週次メンテは「履歴が短い」としか報告しない |
| 改称の適用 | `rename_symbol.py` を手で叩く（今回6件） |
| 退役の適用 | `retire_stale_symbols.py` を手で叩く（今回9件） |
| 判定根拠 | Yahoo の応答からの推測。改称と廃止を区別できない |
| 未追随の実績 | 2026年6〜7月だけで **15件**（改称6 / 廃止9）が2ヶ月放置された |

## 2. スコープと設計判断

### 2.1 変更すること

1. **SEC API クライアント**（新規）— レート制限・UA・リトライを持つ薄いラッパー
2. **`symbols_master` に SEC キーを追加** — `cik` / `sec_class_id`
3. **週次差分検知** — SEC マスタとの突合でコーポレートアクションを検出
4. **自動適用** — 退役は無条件、改称はガード3条件を満たす場合のみ
5. **レポート** — 週次メンテのレポートに節を追加。適用済みと要判断を分けて記載
6. **`sheet_importer` の replace モード修正** — 現状は DELETE→INSERT で **SEC キーを破壊する**（§3.7 問題1）
7. **SEC キーの継続解決** — 新規追加銘柄が追跡対象外のまま残らないようにする（§3.7 問題2）

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| 多段改称（A→B→C）の中間追跡 | **追わない。現在のティッカーへ直接改称する** | キーが CIK / classId なので差分は常に「現ティッカー同士」の比較になり、A→C と出る。データが在るのは C。そもそも **EDGAR はティッカー履歴を持たない**（`formerNames` は社名履歴）ので中間は原理的に復元不能 |
| 指数（`^VIX` 等）・仮想テーマ（`_..._`）の追跡 | **対象外と明示する** | SEC に実体が無い（CIK/classId が付かない）。仮想テーマは自社で定義したもの。実測で 指標3件・テーマ172件が未解決だが、**これは想定通り** |
| ETF を CIK で追う | **やらない。`classId` で追う** | ETF の CIK は**トラスト単位**で粗すぎる（`RSHO` の CIK 1944285 は Tema ETF Trust の13ファンドを含む）。`classId` がファンド単位の安定キー |
| 「ETF はティッカー変更が無い」前提 | **誤り。ETF も変わる** | 自社の実績が反証: `EDOC`→`HEAL`（Global X Telemedicine ETF）、`SIXG`→`UFOX` |
| 検知結果を `/api/system/health` の `overall_status` に反映 | **しない** | あのバッジは「今見ているデータが最新で整合しているか」を60秒周期で示すもの。要判断が1件あるだけで恒常的に `Delayed` になり、**やがて誰も見なくなる**。今日 health check で NG/STALE を分離したのと同じ問題 |
| UA の連絡先を `config.toml` に置く | **置かない。git 管理外のファイルにする** | `config.toml` は git 管理下。先々の公開可能性を踏まえメールアドレスをコミットしない |
| Yahoo 側の検出（`firstTradeDate` × 52週レンジ）を廃止 | **残す。ただし補助に降格** | 17/17 検出・誤検出ゼロで精度は高い。ただし**コーポレートアクションと Yahoo の不具合の両方を拾う**（`RSHO` `CORZZ` が後者）ため単独では判断できない |

## 3. 変更内容

### 3.1 SEC API クライアント（新規: `backend/data_collection/sec_client.py`）

**なぜ**: SEC は 10 req/sec の上限と、連絡先入り User-Agent を規約で求めている。
無制限に投げる作りだとブロックされうる。今日の調査でも20回以上叩いた。

```python
class SecClient:
    """SEC EDGAR への薄いラッパー。

    - User-Agent に連絡先（メールアドレス）を必須で載せる（SEC の規約）
    - 10 req/sec のトークンバケットで自主規制
    - 404 は「存在しない」として即返す。429/5xx はバックオフして再試行
    """
    def company_tickers(self) -> dict        # ticker → cik
    def company_tickers_mf(self) -> dict     # symbol → (cik, seriesId, classId)
    def submissions(self, cik) -> dict       # 詳細（tickers / formerNames / filings）
```

**連絡先の解決順序**（見つからなければ**起動時エラー**。黙って UA 無しで叩かない）:

```
1. 環境変数 STOCKTOOL_SEC_CONTACT
2. config.local.toml の [sec] contact   ← git 管理外（.gitignore へ追加）
3. 見つからなければ ValueError で中断
```

`config.local.toml` を新設し `.gitignore` に追加する。将来の秘匿値もここに置ける。

### 3.2 `symbols_master` への SEC キー追加

**対象**: `universe.db`（**ユーザー資産**。`agent_execution_rules.md` §10.1）
→ **バックアップ取得 → in-place マイグレーションのみ**。swap・再構築は禁止。

| 列 | 型 | 用途 |
| :--- | :--- | :--- |
| `cik` | INTEGER NULL | 個別銘柄の安定キー |
| `sec_class_id` | TEXT NULL | ETF のファンド単位の安定キー |
| `sec_checked_at` | DATE NULL | 最後に SEC と突合した日 |

**突合キーの優先順位**: `sec_class_id` があればそれ、無ければ `cik`。両方無ければ追跡対象外。

初回解決の実測カバー率:

```
個別        2,878/2,892 (99.5%)   cik
市場          21/24 classId + 3 cik  = 100%
セクタ        15/16 classId + 1 cik  = 100%
レバレッジ     12/16 classId + 4 cik  = 100%
指標           3/10 classId + 4 cik、残3件は指数（対象外）
テーマ        95/273 classId + 6 cik、残172件は仮想テーマ（対象外）
```

### 3.3 週次差分検知（`backend/scripts/sync_sec_corporate_actions.py` 新規）

**中核のアイデア**: 各銘柄の submissions を引くと3,000リクエストになるが、
**マスタファイルの差分なら3リクエストで全銘柄をスクリーニングできる。**

```
[1] SEC マスタ3ファイルを取得（3リクエスト）
[2] universe.db の (sec_key, ticker) と突合
      ├─ 同じキーが別 ticker になっている  → 改称候補
      ├─ キーがマスタから消えた            → 廃止候補
      └─ 一致                            → 正常
[3] 候補だけ submissions API で確定（通常 0〜数十件）
      ├─ tickers が変化                  → 改称
      ├─ Form 15-12G / 25-NSE がある      → 登録抹消
      └─ どちらも無い                    → Yahoo 側の問題として別枠
[4] 適用 or レポート
```

**「キーがマスタから消えた＝廃止」が成立する根拠**: 実測で `BLD` は
`company_tickers.json` から削除済みだったが、`submissions` は `tickers=['BLD']` を
返し続けていた。**マスタは登録抹消で除去され、submissions には残る。**

### 3.4 自動適用とガード

**退役は自動適用する。** 根拠が硬く（Form 15-12G / 25-NSE の提出という事実）、
かつ**可逆**（`active=1` に戻せば価格履歴もそのまま残る）。

**改称もガード3条件を満たせば自動適用する。** 誤ると今後ずっと別銘柄を追い続けるため。

```
① SEC キー（cik / class_id）が一致すること
② 新ティッカーが Yahoo で解決し、実際に履歴を持つこと   ← 今回手でやった検証
③ 旧ティッカーが死んでいる（残骸のみ）こと
→ 3つ全部満たせば自動適用。1つでも欠ければレポートのみ
```

②を入れる理由: 今回 `FLZH` 1,687行・`SHOE` 8,404行を確認してから改称した。
**空振りのティッカーへ付け替えて履歴を失う事故**を防ぐ。

適用は既存ツール（`rename_symbol.py` / `retire_stale_symbols.py`）の関数を呼ぶ。
`ticker_history` への記録と `theme_members` の連動は既存実装をそのまま使う。

### 3.5 実行タイミング

**週次メンテナンス（`weekly_maintenance.py`）に組み込む。** 独立スケジュールにしない理由:

- コーポレートアクションは日次で追う必要がない（今回も2ヶ月の放置で実害は履歴の欠落のみ）
- 週次メンテは既にロックファイル（`--lock-file`）とレポート出力の仕組みを持つ
- スケジュール登録先を増やすと、実行漏れ・重複実行の管理コストが増える

SEC へのリクエストは週あたり **3ファイル＋差分件数分**（通常0〜数十）で、
10 req/sec の上限には遠く及ばない。

### 3.6 レポート

| 出力先 | 内容 |
| :--- | :--- |
| `weekly_maintenance_report.txt` に節を追加 | 適用済み（改称・退役）と要判断を分けて記載 |
| `sec_pending_actions.csv` | `rename_symbol.py --batch` にそのまま渡せる形式 |
| `/api/system/health` に `pending_actions` フィールド（任意・後回し可） | ポップオーバーにのみ表示。**`overall_status` は変更しない** |

### 3.7 運用シナリオ検証（2026-08-05 実施）

初期登録 → デイリー → 週次の3シナリオを実コードで追跡し、**修正が Parquet まで届くか**を確認した。

#### 到達性の結論

| 修正対象 | Parquet まで届くか | 経路 |
| :--- | :--- | :--- |
| **ティッカー**（改称・退役） | **届く** | `symbols` は毎回 SQLite から**置換**される（マージではない）。翌日のデイリーで反映 |
| **直近730日の株価** | **届く** | `process_and_merge_table` が `keep='last'` で SQLite 側を優先する |
| **730日より古い株価** | **届かない** | SQLite が保持しない範囲。**Parquet の 73.8%（4,459,171行）が該当** |

バックテストは `active == 1` で絞る（`backtest_screener.py:160`）ため、
退役銘柄・旧ティッカーは母集団から正しく除外される。**ここは設計通り。**

#### 問題1【重大】replace インポートが SEC キーを破壊する

```
execute_import_diff(mode="replace")
  → source='spreadsheet_url' の行を DELETE
  → SymbolMaster(ticker=, exchange=, name=, ...) で再作成   ← cik を設定しない
```

**2026-08-03 に修正した「replace が手動追加行を消す」バグと同じ型。**
あのときの対処は「スプレッドシート由来でない行を保護する」だったが、
**由来する行は依然 DELETE→INSERT** なので、importer が知らない列は破壊される。
`symbols_master.id` も変わる。

**対策**: replace を「全削除→挿入」ではなく **「upsert ＋ シートから消えた行の削除」** に変える。
既存行を更新する形にすれば、`id` も未知の列も保たれる。

#### 問題2【重大】新規追加銘柄の SEC キーが永久に NULL

当初の計画では SEC キーの解決を**一度きりの初期タスク**として書いていた。
スプレッドシートから新規追加された銘柄は `cik` が付かないまま**追跡対象外**になり続ける。

**対策**: 週次同期の冒頭に **「`sec_key` が NULL の銘柄を解決する」ステップを常設**する。
`sec_checked_at` を見て、未解決または一定期間再確認していない銘柄を対象にする。

#### 注意点3件（対応不要だが記録）

| # | 事象 | 判断 |
| :--- | :--- | :--- |
| ① | 730日より古い価格の修正経路が無い | 本計画は価格を修正しないので破綻しない。**分割補正を実装する時点で Parquet 直接操作の経路が必要になる**（`restore_truncated_symbol_history.py` が実例） |
| ② | 非 active 銘柄の価格が Parquet に残り続ける | 現在15銘柄・26,649行（0.4%）。マージのみで行削除がないため単調増加するが、`active==1` で除外されるので正しさの問題はない。当面放置 |
| ③ | 週次で検知 → 反映は翌日のデイリー | 1日のラグは許容。改称直後の新ティッカーは stocktool.db に未存在なので、同一週次セッション内の鮮度監査には現れない（実害なし） |

## 4. ユーザー確認事項

| 項目 | 判断 |
| :--- | :--- |
| 多段改称の扱い | **中間は追わない**（2026-08-05 確定） |
| ETF の追跡キー | **`classId` で追う**（2026-08-05 確定） |
| 指数・仮想テーマ | **追跡対象外**（2026-08-05 確定） |
| UA の連絡先 | **メールアドレス。git 管理外のファイルに置く**（2026-08-05 確定） |
| `active` の自動化 | **自動化する**（退役は無条件、改称はガード3条件つき）（2026-08-05 確定） |
| レート制限 | **10 req/sec のラッパーを用意する**（2026-08-05 確定） |
| 通知先 | **`overall_status` には乗せない。週次レポートが主**（2026-08-05 確定） |
| 秘匿値の置き場所 | **`config.local.toml`（git 管理外）でよい**（2026-08-05 確定） |
| 実行タイミング | **週次メンテに組み込む**（2026-08-05 確定） |

**未確定の項目はなし。** 計画レビュー完了時点で全項目が確定済み。

## 5. 実装順序と進捗チェックリスト

- [x] `SecClient` のテストを先に書く（レート制限・UA 必須・404/429 の扱い）
- [x] `SecClient` を実装（`backend/data_collection/sec_client.py`）
- [x] `config.local.toml` の仕組みと `.gitignore` 追加、連絡先の解決順序
- [x] `universe.db` バックアップ → `cik` / `sec_class_id` / `sec_checked_at` の in-place マイグレーション
- [x] 既存3,255銘柄の SEC キー初回解決（対象外は NULL のまま）
- [x] 差分検知のテストを書く（改称／廃止／変化なし／キー無し）
- [x] `sync_sec_corporate_actions.py` を実装（検知 → 確定 → 適用/レポート）
- [x] 自動適用のガード3条件をテストで固定
- [x] 週次メンテへの組み込みとレポート節の追加
- [x] `doc/backend_specification.md` に SEC 連携の節を追加（§3.1 の列定義 + §8.4）
- [x] `.claude/skills/upstream-data-diagnosis/SKILL.md` §5.1 を「手動手順」から「自動化済み＋手動での確認方法」に更新
- [x] **`sheet_importer` の replace モードを upsert + 欠落削除へ変更**（§3.7 問題1）。
      既存テスト `test_sheet_importer_replace.py` に「replace 後も `id` と `cik` が保たれる」を追加
- [x] **SEC キーの継続解決を週次同期に常設**（§3.7 問題2）。`resolve_missing_keys()` が毎回拾い直す
- [x] `RSHO` / `CORZZ`（Yahoo 側の問題と判明した2件）の監視枠の扱いを決める

### 作業中メモ

- 実装前に `.claude/skills/sandbox-workflow/SKILL.md` を参照すること（universe.db はユーザー資産）。
- **2026-08-06 マイグレーション適用済み**（`universe.db.bak_20260806_021607` にバックアップ）。
  `ALTER TABLE ADD COLUMN` のみのため `symbols.id` は不変（3,255件・MAX 3256）。

  | カテゴリ | cik | classId | 未解決 | 解決率 |
  |---|---:|---:|---:|---:|
  | 個別 | 2,911 | 1 | 4 | 99.9% |
  | テーマ | 6 | 95 | 172 | 37.0% |
  | 市場 / セクタ / レバレッジ | 8 | 48 | 0 | 100% |
  | 指標 | 5 | 3 | 2 | 80.0% |

  未解決 178件の内訳は **仮想テーマ170 / 指数2 / その他6**。仮想テーマと指数は
  SEC に登録主体が無く追跡対象外（`is_sec_trackable()` で除外）。
  その他6件は `AWAY` `DOGEF` `GIGGU` `IPAY` `TBRG` `XWIN`（廃止 ETF・OTC 外国株）。

- **一括マスタだけでは足りないことが判明**（計画時の想定漏れ）。
  `company_tickers.json` は 10,398件しかなく、**全登録企業を網羅していない**。
  `AEP`（American Electric Power / CIK 4904）は submissions API に存在するのに
  一括マスタに無い。マスタだけに頼ると該当42銘柄が「キー無し＝追跡対象外」に静かに落ちる。
  → `SecClient.lookup_cik_by_ticker()`（`browse-edgar` の atom 出力）を
  **一括で解決できなかった分だけ**に使うフォールバックとして追加。42件中36件を解決。
  複数社ヒット時は誤った CIK を割り当てないよう `None` を返す。

- **この時点で既に未検知の改称が見つかっている**（本タスクの妥当性の裏付け）。
  `BK` → **`BNY`**（Bank of New York Mellon / CIK 1390777）、
  `ASGN` → 社名 **Everforth Inc**、`BLD` → **QXO Insulation, LLC**（QXO による買収）。
  いずれも現行 `universe.db` は旧ティッカーのまま。差分検知の実装後に処理する。

## 6. 検証プラン / 結果

- 単体: `SecClient` 17件 / 差分検知 43件 / 週次組み込み 8件 — いずれも**実測ケースで固定**
- **実データ検証（2026-08-06）**: 本番 `universe.db`（3,255銘柄）に対して実行。
  当初の設計では **6件中4件が誤判定**だったものが、§7 の修正後は**全件正解・要判断ゼロ**になった。

  | 銘柄 | 当初の判定 | 修正後 | 正解 |
  | :--- | :--- | :--- | :--- |
  | `CCRN` | retire | retire | ✅ Cross Country Healthcare / Aya による買収（Form 15-12G 2026-07-27） |
  | `KORE` | retire | retire | ✅ KORE Group 非公開化（Form 15-12G 2026-07-31） |
  | `UUP` | **retire** | master_gap | ✅ 現役 ETF（2008年の Form 25 を誤採用していた） |
  | `AEP` | **retire** | master_gap | ✅ 現役（NYSE→Nasdaq 移管の 25-NSE を誤採用） |
  | `GAMB` | **unknown → 併存** | rename → `GRSD` | ✅ Gambling.com → GRANDSTAND Ltd |
  | `VWDRY` | **unknown** | coexisting | ✅ Vestas の ADR と原株（同一 CIK の別証券） |

  適用結果: `CCRN` / `KORE` 退役、`GAMB`→`GRSD` 改称（`symbols_master.id=1411` 温存・
  `theme_members` も移行・`ticker_history` に記録）。`universe.db.bak_20260806_023329` に退避済み。

- 「今回の15件」での回帰は**実施しない**。適用済みで巻き戻しは universe.db（ユーザー資産）を
  触るリスクの方が大きい。代わりに上表の**新たな6件**を実データ検証として採用した
  （`BK`→`BNY` / `ASGN` / `BLD` は既に `active=0` のため検知対象外＝設計通り）。
- **運用シナリオの再検証**: `test_sheet_importer_replace.py` に
  「replace 後も `id` / `cik` / `sec_class_id` が保たれる」を追加（追加時点で red を確認 →
  `id` が 4 → 19 に振り直されていた）。§3.7 問題1 は解消。

## 7. 途中発生した課題

すべて**計画時には想定していなかった**もので、実データを流して初めて表面化した。
判定条件はテストに実データのまま埋め込んである（`test_sec_corporate_actions.py`）。

### 7.1 一括マスタが全登録企業を網羅していない

`company_tickers.json` は 10,398件しかない。`AEP`（American Electric Power / CIK 4904）は
submissions API に存在するのにマスタに無く、**42銘柄がキー無し＝追跡対象外**に落ちた。

→ `SecClient.lookup_cik_by_ticker()`（`browse-edgar` の atom 出力）を
一括で解決できなかった分だけに使うフォールバックとして追加。42件中36件を解決し、
個別カテゴリの解決率は 98.7% → **99.9%** になった。

### 7.2 Form 25 / 25-NSE は上場廃止とは限らない

**取引所の移管でも提出される。** 素朴に「提出があれば廃止」とすると健在な銘柄を退役させる。

```
UUP  Form 25    2008-11-21  … 2026年も 10-Q / 10-K を提出中の現役 ETF
AEP  Form 25-NSE 2023-08-14 … NYSE → Nasdaq の移管。直近 10-Q は 2026-07-30
```

→ ①提出から400日以内、②それより後に定期報告（10-K/10-Q/20-F/40-F）が無いこと、
の2条件を課した。どちらか片方では取りこぼす（移管直後なら①を通り、
四半期報告の間隔があるため②は最大3ヶ月遅れる）。

### 7.3 「マスタに無い＝廃止」も成立しない

7.1 の裏返し。マスタ欠落と本物の廃止を分けるため `is_still_filing()` を追加し、
定期報告が継続していれば `master_gap`（対応不要）として要判断リストから外す。
これが無いと `AEP` `UUP` が**毎週レポートに載り続けてレポート自体が読まれなくなる**。

### 7.4 1つの CIK に複数の証券がぶら下がる

```
BNY / BNY-PK    Bank of New York Mellon（普通株と優先株）   CIK 1390777
VWDRY / VWSYF   Vestas Wind Systems（ADR と原株）           CIK 1330306
```

→ マスタ索引を「キー → ティッカーの**集合**」にした（1対1の辞書だと後勝ちで
`BNY-PK` になり、普通株 `BNY` を「消えた」と誤判定する）。
両方が取引中のケースは `coexisting` として要判断から分離した。

### 7.5 改称の判定は「行数」ではなく「最終取引日の差」

`GAMB`→`GRSD` を当初「併存」と誤判定した。改称直後は旧ティッカーに残骸が数日残る。

```
GAMB  最終 2026-07-29（直近1ヶ月6行）  GRSD  最終 2026-08-05  → 7日差 ＝ 改称
VWDRY 最終 2026-08-05                 VWSYF 最終 2026-08-04  → 差なし ＝ 併存
```

「今日から何日前か」では測らない（連休・祝日・取得タイミングでぶれる）。
同じ市場の2銘柄を比べればその影響が消える。

### 7.6 submissions の `tickers` は一括マスタより遅れる

`GAMB` は社名が GRANDSTAND Ltd に変わり（`formerNames` は 2026-06-01 まで
Gambling.com Group Ltd）、`company_tickers.json` も `GRSD` に更新済みなのに、
submissions は `tickers=['GAMB']` を返し続けていた。

→ submissions が裏付けない改称も `evidence_strength="master_only"` として残し、
**ガード3条件に判断を委ねる**。`unknown` に落とすと一括マスタで捕まえた改称を毎回取りこぼす。

### 7.7 SEC の生存確認を退役判定に反映していなかった

`RSHO`（Tema ETF / classId C000239058）は SEC マスタに現ティッカーで載っているのに
Yahoo の価格供給だけが 2026-07-17 で止まっている。鮮度監査は自分の DB しか見ないので
これを毎週「上場廃止候補」に挙げ、`retire_stale_symbols.py --from-report` で
**健在な銘柄を退役させる**恐れがあった。

→ `split_by_sec_verdict()` で SEC が健在と言う銘柄を自動退役 CSV から除外し、
レポートの `7-c2. HELD FROM AUTO-RETIREMENT` に「供給側を疑え」と明記して残す。
SEC 同期が失敗・スキップされたときは**何も除外しない**（照合できないことを
「健在の証拠なし」と混同して候補を握り潰さないため）。

`CORZZ` は価格が最新（2026-08-04）で鮮度監査には掛からず、アノマリー分類器の
`undecided`（出来高がほぼ無く判定材料が無い）に留まる。専用の監視枠は不要と判断した。

## 8. スコープ外・残作業

### 運用に入ってから確認すること

- **初回の週次メンテ実行**でレポート「7. SEC corporate actions」節が出ること、
  `7-c2. HELD FROM AUTO-RETIREMENT` に `RSHO` が載ること。
- 新規追加銘柄が `resolve_missing_keys()` で拾われ `cik` が付くこと
  （スプレッドシート import → 次の週次、の順で確認する）。
- 未解決6件（`AWAY` `DOGEF` `GIGGU` `IPAY` `TBRG` `XWIN`）は SEC に実体が無く
  永久に NULL のまま。**追跡対象外**なので改称・廃止は手動で気づく必要がある。
  廃止 ETF・OTC 外国株なので実害は小さいと判断した。


- **分割・併合への対応**は本計画の対象外。`doc/completed/split_anomaly_noise_reduction_plan.md` を参照。
- 過去のティッカー履歴の一括復元はできない（EDGAR は現在のスナップショットのみ）。
  週次スナップショットの保存は監査証跡としては有用だが、処理には使わない。
- 米国外の銘柄・OTC のカバー率は未検証（現ユニバースはほぼ米国上場のため未着手）。

### 実行結果（2026-08-06 完了）

- `universe.db` に SEC キーを付与（個別 99.9% / ETF 100%）
- 実データ6件を全件正解で分類。`CCRN` `KORE` 退役・`GAMB`→`GRSD` 改称を自動適用
- 週次メンテに組み込み（レポート節「7. SEC corporate actions」）
- `sheet_importer` の replace が `id` / `cik` を破壊する構造を解消（§3.7 問題1）
- SEC が「上場中」と言う銘柄を自動退役 CSV から除外（`RSHO` 対策）

本計画の全チェックリスト項目を完了。以後の運用は
`doc/backend_specification.md` §8.4 と
`.claude/skills/upstream-data-diagnosis/SKILL.md` §5.1 を参照する。
