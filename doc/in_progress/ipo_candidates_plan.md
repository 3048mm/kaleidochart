# IPO 銘柄の追加候補リストアップ 計画書

- **ステータス**: 🚧 進行中（A/B/C 完了 — 判定ロジック・モデル・マイグレーション）
- **実施者**: AI エージェント (Claude Opus 5) — オーケストレーター + implementer / test-writer へ委譲
- **開始日**: 2026-08-27 / **完了日**: —
- **作業ブランチ**: `worktree-ipo-candidates`（`.claude/worktrees/` 配下に作成）
- **関連ドキュメント**: `doc/universe_db_specification.md` / `doc/backend_specification.md` §8.4 / `doc/frontend_specification.md`

## 1. 背景と目的

`universe.db` の個別銘柄 2,883 件は 2026-04 頃に作成したスプレッドシートを初期インポートしたもので、
**それ以降に IPO した銘柄が一切入っていない**。現状の SEC 週次同期
（`sync_sec_corporate_actions.py`）は universe.db → SEC の方向にしか走査しておらず、
「改称」「上場廃止」は検知できるが「新規上場」は構造的に検知できない。

### 完了条件

1. 週次で IPO 銘柄が自動的に候補としてリストアップされる
2. フロントエンドの Universe 画面に候補レビュー画面があり、採用/却下を選択できる
3. ステータスバッジの詳細に未レビュー件数が出る
4. 候補には企業概要が付き、テーマのタグ付け判断に使える

### 着手前の実測ベースライン（2026-08-27 実測）

| 項目 | 実測値 |
| :--- | ---: |
| `symbols_master` 総数 / active | 3,258 / 3,222 |
| うち cik 解決済み | 2,951 |
| SEC `company_tickers.json` | 10,388 ティッカー / 8,002 CIK |
| SEC `company_tickers_mf.json`（ファンド） | 28,489 |
| **cik 未知の CIK（＝候補の生プール）** | **5,117** |
| ADR のみ（国内普通株なし）で丸ごと除外 | 873 |
| 複数クラス別上場で除外 | 231 |
| **Yahoo にプローブする件数** | **約 4,000** |
| **Yahoo 確定後に pending となる推定件数** | **50〜80 件**（層別サンプル440件からの外挿） |

## 2. スコープと設計判断

### 2.1 変更すること

- `universe.db` に `ipo_candidates` テーブルを追加（in-place マイグレーション）
- `backend/data_collection/ipo_discovery.py`（新規・純粋関数）— 判定ロジック
- `backend/scripts/scan_ipo_candidates.py`（新規）— スキャン実行 CLI
- `weekly_maintenance.py` に週次ステップを追加
- `backend/api/universe_router.py` に候補 CRUD エンドポイントを追加
- `backend/api/routers.py` の `/system/health` に未レビュー件数を追加
- `frontend/src/pages/UniverseCandidatesPage.tsx`（新規）+ UniversePage にトップレベルタブ
- `config.toml` に `[ipo_scan]` セクション

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| 候補の状態を `symbols_master.active` で表現するか | **しない。別テーブル** | `active` は実測で 0/1 のみ（1:3,222 / 0:36）の実質 bool。T1 同期・`detect_candidates()`・退役スクリプトが全て真偽値として読んでおり、第3の値を足すと全経路の洗い直しになる |
| 検知を CIK 単独で行うか | **しない。(cik, ticker) ペア** | 1 CIK に複数ティッカーがぶら下がる（実測 1,443 CIK）。`SO/SOJC/SOJD/SOJE/SOJF/SOMN`、`BAC/BAC-PB/…/BACRP` など。CIK は発行体の単位であって銘柄の単位ではない |
| 「cik 既知 / ticker 未知」を IPO 候補に含めるか | **含めない** | それは改称または新クラス上場であり、既存の SEC 週次同期が `rename_candidate` として既に検知している。**二重検知になる** |
| `quoteType` / `longName` で株式クラスを判定するか | **できない（実測で否定）** | 普通株もワラントもユニットも優先株も全て `instrumentType=EQUITY`。`longName` も普通株と同一文字列を返す（`SCAGW`→"Scage Future"、`EURKU`→"Eureka Acquisition Corp"）。ETF 除外にのみ `quoteType` は有効 |
| 「上場日 >= 基準日」だけで絞るか | **絞れない。サフィックス除外が必須** | 実測で `SCAG`（普通株）上場日 2025-06-27 に対し `SCAGW`（ワラント）が 2026-07-17 と**ワラントの方が新しい**。SPAC はユニットが先に上場する（`EURKU` 2024-07-02 → `EURK` 2024-09-12）ので日付の前後関係も当てにならない |
| 取引所を前方一致で判定するか | **しない。完全一致** | `'NYSEArca'.startswith('NYSE')` が True になり、**ETF・信託の取引所である NYSEArca が混入する**（検証中に実際に `MSBT` Morgan Stanley Bitcoin Trust が通過した） |
| SPAC を社名 regex だけで判定するか | **しない。社名 OR 兄弟ティッカーの両方** | 社名 `Acquisition` 単独では実測 21件中 14件しか捕まらない。`Churchill Capital Corp XII` `Cartesian Growth Corp IV` `RMG ML Sports Holdings` が漏れる |
| SPAC 候補を DB から捨てるか | **捨てない。`auto_excluded` で行を残す** | SPAC は合併後に実業会社へ社名変更する（`Innventure` `Helport AI` `Foxx Development` が実例）。捨てると de-SPAC で生まれた実業会社を永久に取り逃がす |
| 却下した候補を物理削除するか | **しない。`status='rejected'` で残す** | ユーザー判断: 「一旦非表示でいい」。行を残せば画面トグル1つで復活でき、追加コストはゼロ |
| 初回ブートストラップの絞り込み | **CIK 水位は使わず約 4,000 件を全舐め** | 実測で高CIK(>=1.9M)帯が候補の約85%を占めるが、低CIK帯にも約34件ある。ブートストラップは一度きり・約21分なので取りこぼす理由がない。**ただし Yahoo が絞ってきた場合の縮退手段として CIK 水位を実装に残す** |
| 複数クラスが別々に上場している CIK を候補に出すか | **出さない。CIK ごと除外** | 実測 231 件の中身は社債(`FG/FGN/FGSN`)・優先株(`PDCC/PDPA`)・ワラント(`HUBC/HUBCW/HUBCZ`)・when-issued(`ALUR/ALURD`)で、**GOOGL/GOOG 型の本物のデュアルクラスは1件も無い**。現代のデュアルクラス IPO は Class A だけを上場し Class B/C はティッカーを持たないため単一ティッカーとして扱われる。さらに `CRBD/CRBG` のように接頭辞ルールが社債側を誤選択する例があり、曖昧なら落とす方が精度が高い。取りこぼしても既存 Universe 画面から手動追加できる |
| ティッカーが全て ADR/外国 OTC の CIK | **丸ごと除外** | 国内普通株が存在しない。実測 873 CIK。Yahoo プローブが 5,114 → 約 4,000 件に減る |
| SPAC 兄弟ティッカーを「ベース＋U/W/R」の完全一致で判定するか | **しない。末尾1文字が U/W/R か で判定** | SPAC は4文字の語幹でユニット・ワラントを上場し、普通株だけ3文字に短縮する（`JAB/JABRR/JABRU/JABRW`、`NCO/NCOOR/NCOOU/NCOOW`、`CAQ/CAQUU/CAQUW`）。完全一致では**SPAC 262 件を「複数クラス」と誤検知した**（計画レビュー中に実測して修正） |
| SPAC フラグをユニット・ワラント両方で立てるか | **ユニットがある場合だけ立てる** | **ユニットは合併成立時に分離・消滅する**。ワラントだけ残るのは de-SPAC 済み＝既に実業会社。両方で立てると `SCAG`(Scage Future) `INV`(Innventure) `HPAI`(Helport AI) `FOXX` `KWM` `GCL` `YDES` を SPAC 扱いで捨ててしまい、「de-SPAC で生まれた実業会社を取り逃がさない」という本計画の目的に反する。実装後の実測で spac フラグが 539 → 339 件に減った（200 件が de-SPAC 済みとして解放） |
| IPO 銘柄の指標欠損・スクリーナー露出 | **本計画のスコープ外** | ユーザー判断: 後続タスクで IPO 用スクリーナーを別途検討する |

## 3. 変更内容

### 3.1 判定パイプライン（確定版）

```
[1] SEC company_tickers.json 取得        ← 週次同期が既に取得済み。追加リクエスト 0
[2] 除外集合を引く
      symbols_master 全件（active に関係なく。退役36件を再浮上させない）
      ipo_candidates 全件（status 問わず）
      ticker_history.old_ticker（改称前ティッカーが SEC に残ると新規に見える）
      company_tickers_mf.json のシンボル（ファンド）
[3] cik 未知の CIK だけ残す               → 5,117 CIK
[4] CIK 内で普通株を1本選抜               → 約 4,000
      a. Y/F サフィックス（ADR・OTC外国普通株）を落とす
      b. 残りが空 → 国内普通株が存在しない ⇒ **CIK ごと除外**（実測 873 件）
      c. 他全ての接頭辞になっている最短のものをベースとする
      d. ベース以外が全て「末尾1文字が U/W/R」または -UN/-WT/-RI/-RT/-WS
         ⇒ SPAC 構造。ベースを普通株として採用し spac フラグ
      e. それ以外で2本以上残る ⇒ 複数クラス別上場 ⇒ **CIK ごと除外**（実測 231 件）
[5] SEC 側でのフラグ付け
      社名 /\b(acquisition|acquisitions|merger)\b/i          → spac
      社名 /\bETF\b/i                                        → fund
[6] Yahoo chart API で確定（1件1リクエスト、4 req/s スロットル）
      取引所 完全一致 {NasdaqGS, NasdaqGM, NasdaqCM, NYSE, NYSE American}
      instrumentType == 'EQUITY'
      firstTradeDate >= ipo_scan_since
[7] 通過分に yfinance `.info` で企業概要を付与 → ipo_candidates へ登録
      spac / fund フラグ付きは status='auto_excluded'
      それ以外は status='pending'
```

### 3.2 `ipo_candidates` テーブル（`universe.db`）

```
id                INTEGER PK
ticker            VARCHAR NOT NULL
exchange          VARCHAR                -- Yahoo の fullExchangeName から正規化
name              VARCHAR
cik               INTEGER
first_trade_date  VARCHAR                -- ISO date。Yahoo firstTradeDate
detected_at       DATETIME
market_cap        BIGINT                 -- 検知時点のスナップショット
avg_volume        BIGINT
last_price        FLOAT
sector            VARCHAR
industry          VARCHAR
summary           TEXT                   -- longBusinessSummary
website           VARCHAR
flags             VARCHAR                -- 'spac' / 'fund' のカンマ区切り
status            VARCHAR NOT NULL       -- pending / accepted / rejected / auto_excluded
status_note       VARCHAR
reviewed_at       DATETIME
UNIQUE(ticker, exchange)
INDEX(status)
```

**`universe.db` に置く理由**: 採用/却下は再生成不可能な人間の判断であり、
`agent_execution_rules.md` §10.1 の「ユーザー資産」に該当する。`universe.db` は既に
バックアップ + in-place マイグレーションの規律下にあり、全期間再構築の対象外。

### 3.3 API

| メソッド | パス | 内容 |
| :--- | :--- | :--- |
| GET | `/api/universe/candidates` | 一覧。`status` / `flags` / 上場日レンジ / 時価総額下限でフィルタ、ページング |
| POST | `/api/universe/candidates/{id}/accept` | `symbols_master` へ INSERT（`source='ipo_candidate'`）+ status 更新。テーマ紐付けを同時指定可 |
| POST | `/api/universe/candidates/{id}/reject` | status 更新のみ |
| POST | `/api/universe/candidates/bulk` | 複数 id への accept / reject |
| POST | `/api/universe/candidates/{id}/refresh-profile` | `.info` を再取得（初回失敗分の手動リトライ） |
| GET | `/api/universe/candidates/stats` | status 別件数 |

`accept` は書き込みなので `_get_write_db` を使う。`symbols_master` への INSERT は
`theme_type` を `derive_theme_type()` に通す（Universe 画面の既存 CRUD と同じ経路）。

### 3.4 週次スキャンの組み込み

`weekly_maintenance.py` の `run_sec_corporate_action_sync()` の直後に
`run_ipo_candidate_scan()` を追加する。**既存の SEC 同期と同じく try/except で囲み、
失敗しても週次メンテ全体を落とさない**（物理メンテと整合性監査の結果を失わないため）。
SEC マスタは同一プロセス内で `SecClient` がキャッシュするので追加リクエストは発生しない。

日次ではなく週次にする理由: SEC マスタ自体が週次更新であること、
日次更新の critical path に Yahoo 通信を増やしたくないこと。

### 3.5 フロントエンド

- **件数表示**: `/api/system/health` のレスポンスに `universe: { ipo_candidates_pending: int }` を追加。
  `App.tsx` のステータス詳細ポップオーバーに1行、加えて**ナビの "Universe" の横にバッジ**を出す
  （ポップオーバーはクリックしないと見えず、運用されないため）
- **候補画面**: `UniverseCandidatesPage.tsx` を新規作成。
  `UniversePage.tsx` は既に 1,593 行あるので**追記しない**。両画面の上に
  「銘柄一覧 / IPO候補 (n)」のトップレベルタブを置く
  - 列: ティッカー / 社名 / 取引所 / 上場日 / 経過日数 / 時価総額 / 平均出来高 / 株価 / セクタ / フラグ
  - 行展開: 企業概要の全文 + 公式サイト / SEC EDGAR / Yahoo Finance へのリンク
  - 操作: チェックボックス複数選択 → 「追加」「却下」。追加時に category とテーマ紐付けを指定
  - フィルタ: status（既定は pending）/ SPAC疑いを表示 / 却下済みを表示 / 上場日レンジ / 時価総額下限

### 3.7 `classify_symbol_freshness()` の判定順の修正

現在の実装（`weekly_maintenance.py:239`）は `no_history` を**行数だけで**先に判定するため、
**上場直後で行数が少なくデータは最新、という銘柄が退役候補に落ちる**。

```python
# 現在（バグ）
if row_count <= low_history_rows or last_date is None:
    return "no_history"                      # ← last_date を見ていない
if last_date < spy_latest_date - timedelta(days=stale_days):
    return "delisted"

# 修正後: 「供給が今生きているか」を先に見る
if last_date is None:
    return "no_history"
if last_date >= spy_latest_date - timedelta(days=stale_days):
    return "ok" if last_date >= spy_latest_date else "lagging"
if row_count <= low_history_rows:
    return "no_history"
return "delisted"
```

**`created_at` による除外ではなくこの方式を採る理由**: `created_at` は「最近追加した」という
帳簿の都合でしかなく、最近追加した本当に死んだティッカーまで守ってしまう。
`last_date` は「供給が今生きている」という事実そのもので、退役判定が本来見るべき対象。

**閾値は既存の `STALE_CALENDAR_DAYS = 7` を再利用する**（3 を新設しない）。
別の閾値を作ると「行数15・最新日5日前」が新ルールで `no_history`、既存ルールで `lagging` と
食い違い、「供給が生きている」の定義が2つになる。

**既存銘柄への影響は実測ゼロ**（2026-08-27 時点、SPY 最新日 2026-08-26 に対し
`active` かつ行数<=20 の銘柄は 0 件）。

### 3.6 `config.toml`

```toml
[ipo_scan]
since = "2026-04-01"           # 初期インポート元スプレッドシート作成時期＝棚卸し済みの水位
min_market_cap = 0             # 0 = 無効。運用しながら調整
min_avg_volume = 0
yahoo_rate_per_sec = 4         # 実測440件を 0.25s 間隔で完走（404:36件はティッカー不在）
cik_floor = 0                  # 縮退用。Yahoo が絞ってきたら 1900000 を入れる
```

## 4. ユーザー確認事項

| # | 項目 | 状態 |
| :--- | :--- | :--- |
| 1 | IPO 銘柄の指標欠損・スクリーナー露出 | ✅ **スコープ外**（後続タスクで IPO 用スクリーナーを検討） |
| 2 | 初回ブートストラップの方式 | ✅ **A案（全件スロー舐め）**。約 4,000 件 / 約17分 |
| 3 | 却下候補の扱い | ✅ **非表示**。DB 上は `status='rejected'` で残し、画面トグルで復活可能 |
| 4 | `ipo_scan.since = 2026-04-01` の意味 | ✅ 初期インポート元スプレッドシートの作成時期 |
| 5 | SPAC の扱い | ✅ **自動除外**。ただし `auto_excluded` で行は残す（de-SPAC 後の拾い直しのため） |
| 6 | 普通株のみに絞る | ✅ **絞る**。§3.1 [4]〜[6] の3段構え |
| 7 | 複数議決権クラスの扱い | ✅ **CIK ごと除外**。実測で本物のデュアルクラスは0件、中身は社債・優先株・ワラントだった |
| 8 | `retire_stale_symbols` の `LOW_HISTORY_ROWS=20` との干渉 | ✅ **`classify_symbol_freshness()` の判定順を入れ替える**（§3.7）。既存銘柄への影響は実測0件 |

## 5. 実装順序と進捗チェックリスト

- [x] **A. 判定ロジック（純粋関数）のテスト作成** — `backend/tests/data_collection/test_ipo_discovery.py`
      普通株選抜 / SPAC 判定（社名・兄弟ティッカー両方）/ 取引所完全一致 / 除外集合。
      実測で判明した具体例（`SCAG/SCAGW`、`EURK/EURKU`、`CXII/CXIIU/CXIIW`、`KPET/KPET-UN`、
      `NYSEArca` 前方一致、`BAC/BACRP`、`LLYVA/LLYVB/LLYVK`）を**そのままテストケースにする**
- [x] **B. `ipo_discovery.py` 実装** — A を green にする（95 テスト green）
- [x] **C. `ipo_candidates` テーブルのマイグレーション** — `migrate_universe_ipo_candidates.py` + `models_universe.IpoCandidate`（冪等・バックアップ自動取得・8テスト）
- [ ] **D. `scan_ipo_candidates.py` 実装** — `--dry-run` / `--apply` / `--bootstrap` / `--limit`。Yahoo スロットル込み
- [ ] **E. 初回ブートストラップ実行**（`--dry-run` → 件数確認 → `--apply`）。`run_in_background` で実行
- [ ] **F. API エンドポイント + テスト** — `backend/tests/api/test_universe_candidates.py`
- [ ] **G. `/system/health` に件数追加 + スキーマ更新**
- [ ] **H. `weekly_maintenance.py` に週次ステップ追加 + テスト**
- [ ] **H2. `classify_symbol_freshness()` の判定順を修正（§3.7）+ 回帰テスト**
      「行数10・データ最新」が `ok` になること、「行数10・データ30日前」が `no_history` のままであることを両方テストする
- [ ] **I. `UniverseCandidatesPage.tsx` + トップレベルタブ + API クライアント**
- [ ] **J. `App.tsx` のバッジとポップオーバー**
- [ ] **K. フロントエンドテスト（vitest）**
- [ ] **L. ドキュメント更新** — `universe_db_specification.md` に §9 追加、`backend_specification.md` / `frontend_specification.md` に反映
- [ ] **M. 全テスト実行 + 本計画書を `doc/completed/` へ移動**

### 作業中メモ

**現在地**: A / B / C 完了。次は D（`scan_ipo_candidates.py`）。

`init_universe_db()` の `create_all()` がモデル定義からテーブルを作るため、
マイグレーションスクリプトの役割は**バックアップ取得と作成結果の検証**。
なお `create_all` は**既存テーブルに列を足さない**ので、後から列を増やすときは
`ALTER TABLE` が必要（スクリプトが不足を検出して例外を投げるようにしてある）。

`backend/data_collection/ipo_discovery.py` は**通信を一切しない純粋関数のみ**にしてある。
SEC / Yahoo への通信は D の `scan_ipo_candidates.py` 側に置くこと（テスタビリティのため）。

**ワークツリー特有のハマりどころ（2件）**:

1. `data/` は git 管理外なので**ワークツリーに Parquet マスタが無い**。
   `backend/tests/backtest/test_scenario_comparison.py::test_run_comparison_generates_outputs`
   が `FileNotFoundError: Parquet master cache files not found` で必ず落ちる。
   **本体では通る**ので実装起因ではない。ワークツリーでの全テストは
   この1件の失敗を織り込んで読むこと。
2. `config.local.toml` は git 管理外なので
**ワークツリーには存在しない**。`SecClient()` がそのままでは
`ValueError: SEC への連絡先が未設定です` で落ちる。実データ検証時は
`SecClient(project_root=r'd:/My Documents/Programing/stocktool')` と本体を指すか、
環境変数 `STOCKTOOL_SEC_CONTACT` を設定する。

## 6. 検証プラン / 結果

### 単体テスト

```powershell
$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/data_collection/test_ipo_discovery.py backend/tests/api/test_universe_candidates.py -v
```

### 判定精度の受け入れ基準（2026-08-27 の層別サンプル440件を回帰データとして使う）

サンプルから得られた「上場日 >= 2026-04-01 かつ主要取引所」の 33 件に対し:

| 期待 | 銘柄 |
| :--- | :--- |
| **SPAC として除外**（23件・再現率 100%） | ALPX IACQ FXAC OSPR JATT MZYX ACAA TVIV CTAA GLED IACO PECE BWIV MYX ARCL FTHA（社名）/ YICC CGCF CXII SHOT WENC KPET **MTNE**（ユニット兄弟） |
| **ETF・信託として除外** | MSBT（Morgan Stanley Bitcoin Trust）/ THYP（21Shares Hyperliquid ETF） |
| **pending として通過**（誤除外ゼロ） | EROC / REA / STDN / **FDXF（FedEx Freight のスピンオフ）** / FRBT / TPTS / PBLS |

`FDXF` が通ることが特に重要。スピンオフは最も価値の高い候補であり、
SPAC 判定を強めすぎて落とすと本末転倒になる。

> [!NOTE]
> **`MTNE` は当初この表の「通過」側に置いていたが誤りだった。**
> `MTNE-UN`（ユニット）を持つ現役 SPAC であり、除外が正しい。
> 当初のリストは修正前のバグ入り判定の出力から作ったため、誤りを引き継いでいた。

**実測結果（2026-08-27, 実装後）**: 30 件すべて期待どおり。NG 0 件。
Yahoo プローブ対象 4,011 件（見積り約 4,000 と一致）、spac 339 / fund 50。

### 全体テスト

```powershell
$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/ -v
cd frontend; npm test; npm run build
```

### 手動検証

1. `scan_ipo_candidates.py --dry-run` の出力件数が 50〜80 件のレンジに入るか
   （大きく外れたら §3.1 のどの段で落ちすぎ/漏れすぎかを段ごとの件数で切り分ける）
2. Universe 画面のタブに件数が出るか / ヘッダのバッジに反映されるか
3. 1件 accept → `symbols_master` に入るか、`theme_type` が正しく導出されるか
4. 翌日の T1 同期 → T2 で価格が入るか（**`symbols.id` が振り直されていないことを必ず確認**）

## 7. 途中発生した課題

（着手後に追記）

### 実装中に判明した事象

- **ユニットとワラントを区別しないと de-SPAC 済み企業を捨てる**（A/B 実装時）。
  当初は「兄弟に U/W/R があれば SPAC」としていたが、実測で
  `SCAG`(Scage Future) `INV`(Innventure) `HPAI` `FOXX` `KWM` `GCL` `YDES` が
  巻き添えになった。いずれもワラントだけ残る de-SPAC 済みの実業会社。
  **ユニットは合併時に消滅する**という性質で切り分けて解決（§2.2）。
- **`MTNE` の誤フラグは実は正解だった**。§6 の期待値リストの方が誤りで、
  修正前のバグ入り判定の出力から作ったため誤りを引き継いでいた。
  **受け入れ基準そのものを疑う**という教訓。

### 着手前に判明している注意点

- **`retire_stale_symbols` との干渉**: `classify_symbol_freshness()` は `row_count <= 20` を
  `no_history`（＝退役候補）と判定する（`weekly_maintenance.py:208`）。
  上場から1ヶ月未満の銘柄を accept すると**翌週の週次メンテで退役候補に挙がる**可能性がある。
  `ipo_scan.since` が数ヶ月前なら通常は 80 行以上あるため実害は出にくいが、
  **チェック項目 H で必ず確認する**。必要なら `symbols_master.created_at` が
  N 日以内の銘柄を退役判定から除外するガードを入れる。
- **Yahoo のレート制限**: 実測 440 件を 0.25s 間隔で完走（404: 36件はティッカー不在で正常）。
  ブートストラップの約 4,000 件で絞られる可能性は残るため、
  `--limit` と `cik_floor` による分割実行の逃げ道を実装に含める。
- **`sleep` によるポーリング禁止**: ブートストラップは `run_in_background` で実行し完了通知を待つ
  （`CLAUDE.md` エラーリトライ規律）。

## 8. スコープ外・残作業

- **IPO 銘柄向けスクリーナー**: IPO 直後は 200日 SMA も RS も算出できず、
  Minervini トレンドテンプレートが成立しない。accept してもスクリーナーには当分出てこない。
  ユーザー判断により**本計画のスコープ外**とし、後続タスクで別途検討する
- **SEC full-index (Form 8-A12B) による検知**: 上場登録の一次情報だが、
  今回は SEC マスタ差分 + Yahoo `firstTradeDate` で十分な精度が出たため見送る。
  取りこぼしが顕在化したら再検討する
- **Yahoo `v7/finance/quote` の一括取得**: crumb / cookie 認証が必要で未検証。
  1件ずつの chart API で実測 4 req/s が通ったため、最適化としては後回し
- **改称・上場廃止の検知**: 既存の `sync_sec_corporate_actions.py` の担当。本計画では触らない
