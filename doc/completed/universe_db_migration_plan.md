# universe.db 移行 計画書

- **ステータス**: ✅ 完了（2026-07-29）— T1 のソースを Google スプレッドシートから `universe.db` へ移行。`symbols.id` を1件も変えずに本番適用完了、全整合性チェック合格
- **実施者**: AI エージェント (Claude Opus 5) — オーケストレーター + implementer 委譲
- **開始日**: 2026-07-28 / **完了日**: —
- **作業ブランチ**: 未定（`.claude/worktrees/universe-db-migration` を推奨）
- **対象 issue / 関連ドキュメント**: `doc/backend_specification.md` §3.1(T1) / §3.2(theme_constituents)、`doc/architecture.md` §11.1、`doc/issue_list.md`

---

## 1. 背景と目的

### 現状

T1（銘柄マスタ）は Google スプレッドシートから同期している。

```
gspread + credentials.json
  → spreadsheet_sync.fetch_symbols_from_sheet()   6シート(MarketList/LeadingList/SectorList/ThemeList/StockList/LeverageList)
  → orchestrator.sync_symbols_to_db()             symbols を「全件 active=0 → 掲載分を active=1」で upsert
  → theme_constituents を全DELETE → tags LIKE マッチで再構築 (weight = 1/N)
```

一方 `data/universe.db`（`symbols_master` / `theme_members` / `ticker_history`）は CRUD API `/api/universe/*` と `UniversePage.tsx` が実装済みだが、**パイプラインからは一切参照されていない**（完全な並行運用状態）。

### 目的

T1 のソースを universe.db に移し、銘柄定義を universe.db へ一元化する。完了時には以下が成立していること。

1. `update_pipeline.py` が universe.db を読んで T1 を同期し、**`symbols.id` が1件も変化しない**
2. Google Sheets / `credentials.json` への依存が T1 経路から消える
3. `config.toml` の `extra_symbols` 回避策が不要になる

### ベースライン（2026-07-27 実測）

| 項目 | stocktool.db | universe.db | Parquet |
| :--- | ---: | ---: | ---: |
| symbols (active=1) | 3,257 | 3,257 ※W0実施後 | 3,257 |
| theme 構成ペア | 4,731 | 4,717 | 4,731 |
| 個別 / テーマ / 市場 / レバレッジ / セクタ / 指標 | 2916/274/27/16/16/8 | 同左 | — |

`symbols.id` を整数FKで参照している行数:

```
stocktool.db  daily_prices     1,570,882
              indicators       1,570,882
              relative_ranks   1,562,850
              theme_constituents   4,731
Parquet       prices / indicators / ranks / theme_constituents  全期間(7年+)
user_data.db  watchlist(53) / portfolio_positions / position_history  ※ticker 併記あり
```

---

## 2. スコープと設計判断

### 2.1 変更すること

本計画は3フェーズに分割する。**今回のスコープは Phase 1 + Phase 2**。

| Phase | 内容 | 本計画での扱い |
| :--- | :--- | :--- |
| **Phase 1** | universe.db と stocktool.db の整合性確立（データ品質・カテゴリ整理） | **今回** |
| **Phase 2** | universe.db から T1 パイプラインを通す（同期関数実装・切替） | **今回** |
| **Phase 3** | DB 不在でもフロントエンドを起動し、universe 編集・スプレッドシート import を可能にする（コールドスタート） | **次フェーズ**（§8 に残す。現状実測のみ今回実施） |

Phase 3 を分離する理由: Phase 2 が完了して初めて「universe.db を編集 → パイプライン → stocktool.db 再構築」という一連の流れが成立する。Phase 2 なしの Phase 3 は「編集できるが何にも使えない」半端な機能になる。

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| universe.db の `id` を新FKにするか | **しない**。universe.db の id は持ち込まない | `daily_prices` 等157万行×3テーブルと Parquet 全期間が `symbols.id` の整数FK。振り直しは Parquet 全書き換えを意味し非現実的 |
| 同期のキー | **`(ticker, exchange)` 自然キーで upsert し、既存 `symbols.id` を温存** | 同上。`exchange` がズレると新 id が採番され価格履歴が孤児化する |
| 暗号資産の現物（BTC-USD / ETH-USD）採用 | **見送り** | 現物は7日/週。T2は土日行をそのまま取り込み、T3のEMA期間の意味がズレ、T4は土日にBTC単独パーティションになる。T2/T3/T4 全てに手が要りスコープが跳ねる。ETF(GBTC)で代替 |
| GBTC の category 変更に伴う T4 全期間再計算 | **しない** | ランクは `PARTITION BY s.category` だが、指標カテゴリのランクは leading パネル(`_build_leading_item`)がランク引数を受け取らず未使用。`/ranking` はフロントから未呼び出しのデッドエンドポイント。市場カテゴリ(27→25)の変化は自然な退役として許容 |
| 指標カテゴリを T4 対象から外す（W7） | **単独ではやらない。W4 の sandbox 検証のついでに実施** | 無駄ではあるが実量は約28,000行（`relative_ranks` 156万行の1.8%）。単独で種別Bのコストを払う価値はない |
| `/ranking` の母集団バイアス是正 | **やらない（別issue）** | デッドエンドポイントであり実害なし。掃除は universe 移行と無関係 |
| DX-Y.NYB の物理削除 | **しない。`active=0`（ソフトデリート）** | Parquet に4,104行(2010-04-01〜)の履歴。物理削除は関連3テーブルの掃除が必要になる |
| `fetcher.py` の `_normalize_ticker` から DX-Y.NYB 分岐を削除 | **しない（残す）** | デッドコードになるが、`.` を含むティッカー再追加時の誤正規化(`DX-Y-NYB`)を防ぐ保険。コストゼロ |
| `theme_constituents` の再構築を `tags LIKE` で行う | **廃止。`theme_members` から直接変換** | `LIKE '%CPER%'` は `_CMMM0F_` と `_CMMM30_` のような部分一致事故のリスク。正規化済みテーブルがあるのに文字列マッチする理由がない |
| **Parquet T2（価格原本）を1から作り直すか** | **作り直さない**（ユーザーから「必要なら実施可」の申し出あり。不要と判断） | 実測で active 31銘柄が既に SPY 最新日に追いついておらず、`CNCR`(216行, 最終2025-06-03) 等は **yfinance がもう配信していない**。Parquet は「取り直せない履歴」を保持するからこそマスターであり、再構築は不可逆なデータ損失になる。加えて 3,255銘柄×7年 の再取得は yfinance のレート制限（429 を実測）で非現実的。`market_cap` も過去分を再取得できる保証がない |
| **並行運転（Sheets 経路と universe 経路の日次 diff を N 日監視）** | **実施しない**（2026-07-28 ユーザー判断: スプレッドシートと universe の互換性は検証済みのため） | ただし §6.2 の **sandbox 単発 A/B 突合は残す**。検証したいリスクが「Sheets↔universe の取り込み忠実性」ではなく「`sync_symbols_from_universe()` の実装バグ（id温存・weight・tags逆生成）」であり、別物のため |

---

## 3. 変更内容

### Phase 1: 整合性確立

#### W0. 完了済み（2026-07-27 実施）

`config.toml` の `extra_symbols` 由来3銘柄（DX-Y.NYB / GBTC / JPY=X）を universe.db へ追加し、ティッカー差分を0件にした。属性は `orchestrator.py:28-36` の注入値と完全一致（`exchange='US'`, `industry='System'`, `theme_type='etf'`, `source='pipeline'`）。バックアップ: `data/_bk/universe_20260727_231618.db`。

#### W1. GBTC を 指標(LeadingList) へ昇格

- **何を**: `universe.db` `symbols_master` の GBTC を `category='市場'` → **`'指標'`**、`sector_etf` → **`'Crypto'`**、`name` / `industry` を整備（現在 name が ticker のまま）
- **なぜ**: 現在この枠は IBIT が「テーマ兼 指標」のハードコードで代用している。既存 指標 8件は全て `tags` に分類ラベル（Risk/Macro/Credit/Bond/Commodity/Currency）を持つ設計で、`Crypto` が空いている。GBTC は 2,818行(2015-05-11〜)と IBIT(635行, 2024-01-11〜)より履歴が長い
- **影響**: 市場 27→26、指標 8→9

> ⚠️ **既知の注意点**: GBTC は 2024年1月の ETF 転換前は信託で、NAV に対するプレミアム/ディスカウントを含む。年次リターンは 2021年 +7.0% / 2022年 -75.8% / 2023年 +317.6% と BTC 現物から乖離する。転換後（2024-01以降）の IBIT との日次リターン相関は 0.9996 で問題なし。**長期 RS ラインを見る際は 2024年以前に段差があることを認識すること**。

#### W2. IBIT / CPER の「兼任」ハードコード撤廃

- **何を**: 以下2箇所から `IBIT` と `CPER` を除去
  - `backend/api/dashboard_router.py:206` — `if s.category == "指標" or s.ticker in ("IBIT", "CPER")`
  - `backend/api/universe_router.py:188` — `extra_tickers = ["IBIT", "CPER"] ...`
- **なぜ**: W1・W2b で両者が正式に category で表現されるため、ハードコードが不要になる
- **影響**: IBIT はテーマ専任、CPER は指標専任になる

#### W2b. CPER をテーマから外し 指標 へ移す / COPX をテーマとして存続

- **何を**:
  1. `universe.db` の CPER を `category='テーマ'` → **`'指標'`**、`sector_etf` → **`'Commodity'`**
  2. `theme_members` から CPER の3ペア（FCX / IE / SCCO）を削除
  3. COPX は `category='テーマ'` のまま存続
- **なぜ**: 実測で **CPER のメンバー3件は全て COPX にも含まれ、CPER 固有メンバーはゼロ**（CPER⊂COPX の真部分集合）。意味論的にも CPER は銅**先物**連動ETFで株を持たず、FCX/IE/SCCO は COPX（銅**鉱山株**ETF）の構成銘柄。タグ付けが壊れている
- **情報の損失**: なし（COPX がスーパーセット: ERO / FCX / HBM / IE / SCCO / TMQ の6件）
- **CPER の行き先**: 銅先物は "Dr. Copper" として景気先行指標の定番で、鉱山株より現物価格の方が先行指標性が高い。指標に USO が既に `Commodity` だが `tags` は分類ラベルで一意である必要はない（`Risk` は ^VIX/^VIX3M/ARKK で重複済み）

#### W3. DX-Y.NYB の退役

- **何を**: `universe.db` から削除、`stocktool.db` `symbols` は **`active=0`**
- **なぜ**: 全文検索の結果、**backend ロジック・API・フロントエンドのいずれからも参照されていない**。ヒットは `fetcher.py:17`（正規化の除外条件＝用途ではない）、`config.toml:10`、`scratch/` 2件のみ。ドルインデックスは 指標 枠の `UUP`（米国ドル・インデックス, `tags='Currency'`）が既にカバー済み
- **影響**: 市場 26→25

#### W4b. universe.db のデータ品質是正（課題C / E / G）

| 課題 | 内容 | 対応 |
| :--- | :--- | :--- |
| **C** | `theme_type` の判定ロジックが3系統に分裂。`spreadsheet_sync.py` / `universe_router._derive_theme_type` は category=テーマ→`theme`、`import_universe._detect_theme_type` は→`etf`。実データで IBIT/CPER が `etf` になっているのは3つ目が書いたため | 判定関数を**1本に統合**し、universe.db 全件を再導出 |
| **E** | `weight` が stocktool は `1/N` 正規化、universe は全件 `1.0` | 同期時に **`1/N` を再計算**（現行 `build_virtual_index_prices` は weight 未使用の equal-weight だが、既存 `theme_constituents` と値を揃える） |
| **G** | `theme_members` に親が `symbols_master` に存在しない孤児が3件（`_SMRTEE_` / `_TSCR10_` / `_WRBL67_`）。文字列参照のためDBが弾かない | 孤児を解消し、同期処理に**孤児検知ゲート**（孤児があれば同期を中断）を実装 |

### Phase 2: パイプライン切替

#### W5. `sync_symbols_from_universe()` の新設

- **何を**: `backend/data_collection/universe_sync.py`（新規）に、`txt_sync.sync_symbols_from_txt(db)` と同じインターフェース（`(sheet_data, symbol_ids)` を返す）で実装
- **必須要件**:
  1. `(ticker, exchange)` 自然キーで `symbols` を upsert し、**既存 `symbols.id` を温存**
  2. `theme_constituents` は `theme_members` から**直接変換**（`tags LIKE` を経由しない）、`weight = 1/N`
  3. `symbols.tags` は `theme_members` から `GROUP_CONCAT` で逆生成する。**並び順を昇順ソートで固定**（不安定だと Parquet symbols の差分が毎日発生する）
  4. 同期前に孤児検知ゲートを通す
- **なぜ**: これが移行の本体

#### W6. `extra_symbols` の削除

- **何を**: `config.toml` の `[data_collection].symbols` を削除、`orchestrator.sync_symbols_to_db()` の `extra_symbols` 引数と注入ロジック（`orchestrator.py:24-36`）を削除
- **なぜ**: 12件中9件は既にシート掲載済みでデッドエントリ、JPY=X は T2 で除外されるため、**実効は GBTC と DX-Y.NYB の2件のみ**。両方 W1/W3 で行き先が決まる
- **さらに**: extra_symbols は保護機構として機能していないどころか**事故要因**。注入時の `exchange` は `"US"` 固定なので、仮に SPY がシートから消えて再注入されると `(SPY, 'US')` という**別 id の重複行**が作られる（既存は `(SPY, 'NYSEARCA')`）
- **依存**: W1・W3 完了後
- **種別**: **B**（パイプライン挙動変更）→ sandbox 検証必須

#### W7. 指標カテゴリを T4 対象から外す（W6 のついで）

- **何を**: `backend/pipeline/phases/t4_ranks.py:94` の `WHERE i.date = :d AND s.category != 'レバレッジ'` を `AND s.category NOT IN ('レバレッジ', '指標')` に変更
- **なぜ**: 指標のランクは資産クラスがバラバラで解釈可能な意味がなく、かつ leading パネル（`_build_leading_item` はランク引数を受け取らない）でも `/ranking`（フロント未呼び出し）でも実質未使用
- **優先度**: 低。**単独実施はしない**。W6 の sandbox 検証を回すときに同梱する

#### W8. 上場廃止銘柄の退役フロー移設（課題F）

- **何を**: `weekly_maintenance.py` が出力する「上場廃止候補CSV」の宛先を、Sheets 手動除外から **universe.db の `active=0` 更新**へ接続
- **なぜ**: 現行 T1 は「全件 active=0 → シート掲載分を1」で、シートから消えれば自動失効した。universe は soft delete のみで一度立った `active=1` は落ちないため、**このループが切替と同時に失われる**

### Phase 1/2 共通

#### W9. fx_rates の是正（JPY=X）— 独立実施可

- **何を**:
  1. 現在の `fx_rates` **31行（全てダミー値）を削除**
  2. 実データを **1996-10-30 から 7,759行**バックフィル
  3. `symbols` の JPY=X を `active=0`、universe.db からは削除（W0 で追加した行を取り消す）
  4. `t2_prices.py:125-142` の `skip_fetch` ダミー投入を、本番 `fx_rates` へ書かないか識別可能な形にする
- **なぜ**: 31行中31行が `rate_val = 155.0 + (curr.day % 5) * 0.2` に一致し、土日を含む。2026-07-27 に `--skip-fetch` 付きで一度走り、空だった `fx_rates` にダミーが入った経緯。**実勢 163.70 に対し 155.4 で約5%乖離**。`portfolio_service.py:661-666` の `total_equity_jpy` / `current_exchange_rate` に効く
- **現時点の実害**: `transactions` / `portfolio_positions` とも0行のため金額影響なし。ただし TotalPortfolio 画面の為替レート表示はダミー値
- **なぜ全期間か**: Portfolio 機能は暫定実装で将来の機能追加余地がある。`fx_rates` は4列のみで7,759行でも軽量。取り直しは可能だが「あの時取っておけば」になりやすい
- **JPY=X を symbols から外してよい根拠**: `t2_prices.py:59` が T2 から明示除外、`sync_fx_rates()` は `"JPY=X"` をハードコードし `symbols` を参照しない、`db_health_check.py:53-66` は `"=X" in t` でスキップ済み

---

## 4. ユーザー確認事項

| # | 項目 | 状態 |
| :--- | :--- | :--- |
| 1 | 暗号資産は現物(BTC-USD/ETH-USD)ではなく ETF(GBTC)を使う | ✅ 決定済（2026-07-28） |
| 2 | DX-Y.NYB を削除する | ✅ 決定済（2026-07-28） |
| 3 | `extra_symbols` を削除する | ✅ 決定済（2026-07-28） |
| 4 | CPER をテーマから外し COPX に一本化、CPER は指標へ | ✅ 決定済（2026-07-28） |
| 5 | 指標に相対ランクは不要（W7 は W6 のついで扱い） | ✅ 決定済（2026-07-28） |
| 6 | 本計画のスコープを Phase 1 + Phase 2 とし、Phase 3 は次フェーズとする | ✅ 決定済（2026-07-28） |
| 7 | 並行運転（Sheets 経路と universe 経路の日次 diff 監視）は**実施しない** | ✅ 決定済（2026-07-28）— スプレッドシートと universe の互換性は検証済みのため。§6.2 の sandbox 単発 A/B 突合は残す |
| 8 | W9(fx_rates) を**本計画に含める** | ✅ 決定済（2026-07-28） |
| 9 | Parquet を1から作り直すか → **作り直さない** | ✅ 決定済（2026-07-28）— 判断理由は §2.2 参照 |

**未解決の確認事項はなし。** 着手可。

---

## 5. 実装順序と進捗チェックリスト

### Phase 0: 準備 ✅

- [x] ~~作業ワークツリー作成~~ → **本体チェックアウトで実施**（本番 `universe.db` / `stocktool.db` への書き込みを伴い、`agent_execution_rules.md` §10.3 によりワークツリーからは実行できないため）。コミットはせず `git add` まで
- [x] `universe.db` のバックアップ取得 → `data/_bk/universe_20260728_013602.db`
- [x] 現状スナップショット記録 → `tmp/universe_migration_snapshot/`（`before_stocktool_symbols.csv` 3257行ほか5ファイル）

### Phase 1: 整合性確立（データ + 軽微なコード変更）✅

- [x] **W4b-C**: `theme_type` 判定を `backend/data_collection/symbol_classify.py` に一元化。3系統（`spreadsheet_sync` / `universe_router._derive_theme_type` / `import_universe._detect_theme_type`）を薄いラッパー化。単体テスト28件
- [x] **W4b-C**: universe.db 全件の `theme_type` を再導出 → IBIT `etf`→`theme`、`_MRAD_` `theme`→`virtual` の2件を是正
- [x] **W4b-G**: 孤児テーマ親3件（`_SMRTEE_` / `_TSCR10_` / `_WRBL67_`）の 4 ペアを削除
- [x] **W1**: GBTC を `category='指標'` / `sector_etf='Crypto'` / `name='ビットコイン (GBTC)'` へ
- [x] **W2b**: CPER を `category='指標'` / `sector_etf='Commodity'` へ。`theme_members` から3ペア削除（COPX がスーパーセットであることをスクリプト内で検証済み）
- [x] **W2b'**: IBIT はテーマ専任のまま `sector_etf` を `Crypto`→`BLOK`（親セクタETF）に復元 — §7.2 の発見による追加項目
- [x] **W3**: DX-Y.NYB を universe.db から削除、`stocktool.db` `symbols` を `active=0`
- [x] **W2**: `dashboard_router.py` / `universe_router.py` から `IBIT` / `CPER` のハードコードを撤廃
- [x] Phase 1 検証（§6.1）→ **20/20 合格**、全 pytest **446 passed**

実装スクリプト: `backend/scripts/migrate_universe_phase1.py`（冪等・`--dry-run` 対応）

### Phase 2: パイプライン切替（種別 B）✅ コード完了・本番適用待ち

- [x] **W5-test**: TDD red（18件失敗を確認）→ green。`backend/tests/data_collection/test_universe_sync.py`
- [x] **W5**: `backend/data_collection/universe_sync.py` 実装
- [x] **W5-hardening**: **exchange 変更の耐性**（§7.6 の事故を受けて追加）。`(ticker, exchange)` で引けず同一 ticker の既存行が一意なら、新規採番せず `exchange` を更新して id を温存する
- [x] **W4b-E**: `theme_constituents` 変換で `weight = 1/N` を再計算
- [x] **W5-3**: `symbols.tags` の逆生成（**カテゴリ別分岐** — §7.2。個別は昇順ソート固定）
- [x] **W5-4**: 孤児検知ゲート実装（書き込み前に中断。DB は変更されない）
- [x] 本番データ複製での検証 → **`symbols.id` 変化 0 件・新規採番 0 件・MAX(id) 不変**（§6.2）
- [x] **W7**: `t4_ranks.py` の除外条件に `'指標'` と `s.active = 1` を追加
- [x] **W6**: `config.toml` の `extra_symbols` 削除、`orchestrator.sync_symbols_to_db()` の注入ロジック削除（レガシー関数自体は警告付きで残置）
- [x] **W6**: `orchestrator.py` の T1 呼び出しを `sync_symbols_from_universe()` に差し替え
- [x] **W8**: `weekly_maintenance.py` の出力先を `delisting_recommendations.csv` に改称し、反映スクリプト `backend/scripts/retire_stale_symbols.py` を新設
- [x] Phase 2 検証（§6.2）→ **23/23 合格**、全 pytest **466 passed**
- [x] **本番適用**（2026-07-28 22:28 〜 23:56、正常完了）。日次パイプライン1回実行で T1 切替・Parquet 更新まで完結
- [x] ~~昇格（`tools/deploy_after_merge.ps1`）~~ → **実行不要**（下記）

> [!CAUTION]
> **`deploy_after_merge.ps1` は本移行には使えない。** `deploy_after_merge.py:108` が
> `update_pipeline.py --skip-sync` で回すため **T1 をスキップする**。indicator 追加・
> スキーマ変更向けの道具であり、T1 変更には効かないどころか、本番 Parquet をワークスペースへ
> コピーして T3 以降を再計算し swap するため通常実行の結果を上書きするリスクがある。
> **T1 の変更は通常の日次実行そのものが昇格を兼ねる**（パイプライン末尾の
> `rotate_and_archive_to_parquet` が Parquet を更新するため）。

> [!IMPORTANT]
> 本番適用時に発生する変更（sandbox で実測済み）:
> - `theme_constituents` 4,729 → **4,710**（CPER の3ペア + 孤児4ペア + 重複12ペアの解消）
> - `symbols.tags` が **74銘柄**で変化（重複・自己参照のクレンジングと並び順の正規化）
> - `DX-Y.NYB` / `JPY=X` が `active=0`（universe.db に存在しないため）

### 追加実施（計画外）

- [x] **GBTC 重複行の掃除**: §7.6 の id=3256 を SQLite（symbols / daily_prices / indicators / relative_ranks 各502行）と Parquet（prices / indicators 各2,818行、ranks 2,090行、symbols 1行）から削除。`backend/scripts/purge_orphan_symbol.py`（MVCC 世代管理・PyArrow ストリーミングで OOM 回避）。`db_health_check --parquet` 合格

### W9: fx_rates 是正 ✅

- [x] **W9-1**: `fx_rates` のダミー31行削除（31/31 がダミー式に一致・土日10件を含むことを確認して削除）
- [x] **W9-2**: 実データ **7,711行**（1996-10-30〜2026-07-27）をバックフィル。§7.5 参照
- [x] **W9-3**: JPY=X を `symbols` で `active=0`、universe.db から削除
- [x] **W9-4**: `skip_fetch` 時に本番 DB へダミーを書かないガード（`_is_production_db()`）+ 回帰テスト2件

実装スクリプト: `backend/scripts/backfill_fx_rates.py`（冪等・`--dry-run` / `--skip-retire` 対応）

検証: `get_historical_fx_rate()` が当時のレートを返すことを実測
（2024-01-15→145.15 / 2015-06-01→124.19 / 2000-03-10→106.22）。是正前は全日付が
ダミーの 155.x に張り付いていた（実勢 163.6 に対し約5%乖離）。

### ドキュメント更新

- [ ] `doc/backend_specification.md` §3.1 / §3.2 に universe.db を T1 のソースとして記載
- [ ] `doc/architecture.md` §11.1 の表に universe.db の行を追加（データ3分類での位置づけ = ユーザー資産相当）
- [ ] `doc/agent_execution_rules.md` §10.1 のデータ3分類表に universe.db を追加
- [ ] `CLAUDE.md` の「Hybrid hot/cold data model」に universe.db を追記
- [ ] `doc/issue_list.md` に残作業（§8）を起票
- [ ] 本計画書を `doc/completed/` へ移動

### 作業中メモ

（着手時に記入）

---

## 6. 検証プラン / 結果

### 6.1 Phase 1 検証

```powershell
# universe.db と stocktool.db の突合（W0 で使用した比較スクリプトを流用）
$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe tmp\cmp_universe.py
```

| 検証項目 | 期待値 |
| :--- | :--- |
| active ティッカー差分 | universeのみ=0件、stocktoolのみ=DX-Y.NYB / JPY=X のみ（W3/W9 実施後） |
| `theme_type` の3系統不整合 | 0件（IBIT/CPER の `etf` が解消） |
| 孤児テーマ親 | 0件 |
| CPER の theme_members ペア | 0件（COPX は6件で不変） |
| GBTC / CPER の category | いずれも `指標` |

### 6.2 Phase 2 検証（最重要）

```powershell
# sandbox で T1 のみ実行
$env:STOCKTOOL_ENV="sandbox"; $env:PYTHONPATH="backend"
.\venv\Scripts\python.exe backend\scripts\update_pipeline.py --skip-fetch
```

| 検証項目 | 期待値 | 失敗時の意味 |
| :--- | :--- | :--- |
| **`symbols.id` の変化** | **0件**（Phase 0 のスナップショットと完全一致） | 自然キー不一致 → 価格履歴の孤児化。**即中断** |
| `symbols` 行数 | 3,255（3,257 − DX-Y.NYB − JPY=X） | — |
| `theme_constituents` 行数 | 4,717 − CPER 3ペア = 4,714 前後 | 変換ロジックの取りこぼし |
| `theme_constituents.weight` | 各テーマで合計 1.0（`1/N`） | 正規化漏れ |
| `symbols.tags` の並び | 2回連続実行で完全一致（並び順安定） | ソート未固定 → Parquet 差分が毎日発生 |
| `tools/db_health_check.py --all` | NG 0件 | — |
| `pytest backend/tests/ -v` | 全件パス | — |

**A/B 突合（単発 — 並行運転の代替）**

同一 sandbox データに対し Sheets 経路と universe 経路を1回ずつ実行し、`symbols` / `theme_constituents` をダンプして突合する。差分が以下の「意図した差分」のみであれば合格。

| 意図した差分 | 内容 |
| :--- | :--- |
| GBTC | `category` 市場 → 指標、`tags` NULL → `Crypto` |
| CPER | `category` テーマ → 指標、`tags` `GLTR` → `Commodity`、theme_constituents 3ペア消失 |
| DX-Y.NYB | `active` 1 → 0 |
| JPY=X | `active` 1 → 0 |
| IBIT | `theme_type` の統一結果（`etf` → `theme`） |

上記以外の差分（特に `id` の変化、theme ペアの増減、`tags` の並び順違い）が1件でも出たら **切替を中止**し §7 に記録する。

### 6.3 結果（2026-07-29 本番適用後の実測）

| 検証項目 | 結果 |
| :--- | :--- |
| **`symbols.id` の変化** | **0 件**（新規採番 0・MAX(id) 不変） |
| **T2 行数 == T3 行数** | **不一致 0 件**（全 3,255 active 銘柄） |
| **孤児 FK** | `daily_prices` / `indicators` / `relative_ranks` / `theme_constituents`（`symbol_id`・`theme_id` とも）すべて **0 件** |
| SQLite `theme_constituents` | 4,710 |
| **Parquet `theme_constituents`** | **4,710**（SQLite と完全一致＝削除が伝播） |
| Parquet `symbols` | 3,257（SQLite と一致） |
| W7 の効果 | `relative_ranks` は 個別 2846 / テーマ 268 / 市場 24 / セクタ 16 のみ。**指標・レバレッジ・`active=0` の混入 0 件** |
| `db_health_check --parquet` | 合格（ID 列すべて数値型） |
| pytest 全体 | **468 passed** |

移行後の主要銘柄:

```
GBTC     id=3258  NASDAQ    指標   Crypto      active=1   （重複行なし）
CPER     id=30    NYSEARCA  指標   Commodity   active=1
IBIT     id=34    NASDAQ    テーマ  BLOK        active=1
DX-Y.NYB id=3255  US        市場   -           active=0   （退役）
JPY=X    id=3257  US        市場   -           active=0   （退役、fx_rates へ移行済み）
```

**`db_health_check --all` の NG 75 件について**: すべて「SPY 最新日より古い」＝鮮度の指摘で、
データ完全性の破損ではない（T2/T3 行数一致・孤児 FK ゼロ）。内訳は T2 が 0 行の 18 銘柄
（従来 19 銘柄から JPY=X が退役して 18）と、当日分を取得できなかった 57 銘柄。
本移行による回帰ではなく、上場廃止銘柄の棚卸し課題として `doc/issue_list.md` に起票済み。

---

## 7. 途中発生した課題

### 7.1 `_MRAD_` — 仮想テーマが `exchange='NYSE'` で登録され価格系列が空だった（Phase 1 で解消）

- **事象**: `_MRAD_`（通信::広告、構成銘柄12件）の `daily_prices` が 0 行
- **原因**: `exchange='NYSE'` で登録されていたため `theme_type='theme'` と導出され、
  T2 は yfinance から `_MRAD_` の取得を試みて失敗（そんなティッカーは存在しない）、
  一方で仮想指数合成は `theme_type == 'virtual'` で対象を選ぶ（`orchestrator.py:673`）ため対象外。
  **取得も合成もされない**状態で放置されていた
- **解決**: `derive_theme_type()` にティッカーパターン（`_..._`）を第2の判定材料として追加。
  `exchange` は変更していない（`(ticker, exchange)` が自然キーのため、変更すると Phase 2 で
  新 id が採番されるリスクがある）。合成対象は `theme_type` で選ばれるためこれで十分
- **副作用**: 次回パイプライン実行で `_MRAD_` の合成価格が生成される（正しい挙動）

### 7.2 `tags` の意味論がカテゴリごとに異なる（Phase 2 の設計に反映）

計画段階では「`tags` は所属テーマの CSV」と想定していたが、実測すると**カテゴリごとに別物**だった。

| category | `tags` の意味 | 充足 |
| :--- | :--- | ---: |
| 個別 | 所属テーマの CSV | 2915/2916 |
| テーマ | **親セクタETF**（GLTR, BLOK 等） | 274/274 |
| 指標 | **分類ラベル**（Risk, Currency 等） | 8/8 |
| レバレッジ | **原資産ETF**（TQQQ→QQQ） | 16/16 |
| 市場 / セクタ | 空 | 0 |

→ **W5 の `tags` 逆生成はカテゴリで分岐が必要**:

```
category == '個別'  → theme_members から昇順ソートで CSV 生成
それ以外            → universe.sector_etf をそのまま使う
```

非個別340件のうち `stocktool.tags` と `universe.sector_etf` が食い違うのは CPER / IBIT の2件のみで、
いずれも Phase 1 で是正済み（CPER は 指標 へ移動、IBIT は親セクタETF `BLOK` を復元）。

### 7.3 `tags` 側に重複・自己参照のゴミがある（Phase 2 で自動クレンジング）

個別400件を抜き取り比較したところ 397件で `tags` と `theme_members` が一致。
不一致3件はすべて `tags` 側の不正データだった。

```
ANET  tags=[DTCR, SIXG, _HRDW4F_, _HRDW4F_, _NTWRCB_]   ← _HRDW4F_ が重複
ARLO  tags=[XRT, _SMRT42_, _SMRT42_]                    ← _SMRT42_ が重複
BODI  tags=[BODI, _LNGVB7_]                             ← 自分自身を参照
```

`theme_members` 側が正しいため、W5 の逆生成でこれらは自動的に解消される。

### 7.4 yfinance がレート制限で使えず chart API 直叩きに変更（W9）

`yf.download` が HTTP 429 を "possibly delisted; no price data found" として握り潰すため、
JPY=X の一括バックフィルでは Yahoo chart API を直接呼ぶ実装にした（`backfill_fx_rates.py`）。
日付変換は `meta.exchangeTimezoneName`（`Europe/London`）で行う。**UTC で変換すると
金曜バーが土日にずれ込み、為替に存在しないはずの土日行が生成される**（実測: UTC だと
Fri=649/Sun=903 に割れ、London だと Mon〜Fri 各1552で土日ゼロ）。

### 7.6 【重要】日次パイプラインの自動実行で GBTC の id 重複が実際に発生した（2026-07-28）

**§2.2 で警告していた「`exchange` がズレると新 id が採番され価格履歴が孤児化する」事故が本番で発生した。**

**経緯**（`logs/pipeline.log`）:

```
2026-07-28 06:45:03  Starting Step 3 Pipeline Orchestrator   ← スケジュール実行
2026-07-28 06:45:04  Starting T1: Symbol Sync...
2026-07-28 06:45:06  Loaded 24 active symbols from sheet: MarketList
2026-07-28 06:45:07  Loaded 10 active symbols from sheet: LeadingList   ← 8 → 10
2026-07-28 06:45:08  Loaded 273 active symbols from sheet: ThemeList    ← 274 → 273
```

Phase 1 の内容に合わせてスプレッドシート側が更新されており（LeadingList 8→10、ThemeList 274→273）、
日次パイプラインがそれを stocktool.db へ同期した。その際 **スプレッドシートの GBTC は
`exchange='NASDAQ'`** で登録されていたが、`extra_symbols` 注入で作られた既存行は
`exchange='US'` だったため、`(ticker, exchange)` 自然キーが一致せず新規行が採番された。

```
id=3256  GBTC  exchange='US'      category='市場'  active=0   ← 旧行（孤児化）
id=3258  GBTC  exchange='NASDAQ'  category='指標'  active=1   ← 新規採番
```

**データ損失はなかった。** パイプラインが新 id で全期間を再取得し、両方に同じデータが入っている。

| | id=3256 (active=0) | id=3258 (active=1) |
| :--- | ---: | ---: |
| SQLite `daily_prices` | 502 | 502 |
| SQLite `indicators` | 502 | 502 |
| SQLite `relative_ranks` | **502** | 6 |
| Parquet `prices` | 2,818 | 2,818 |

**残っている問題**: 死んだ id=3256 が `relative_ranks` に 502 行残っている。
`t4_ranks.py` は `active` でフィルタしないため（`WHERE i.date=:d AND s.category != 'レバレッジ'`）、
**無効化された重複銘柄が 市場 カテゴリのパーセンタイル母集団に混入し続ける**。

**対応（W5 に反映済み）**: `sync_symbols_from_universe()` に exchange 変更の耐性を実装した。
`(ticker, exchange)` で引けず、かつ同一 ticker の既存行が一意に定まる場合は、
**新規採番せず既存行の `exchange` を更新して id を温存する**（警告ログを出す）。
universe 側が同一 ticker を複数 exchange で持つ場合は寄せ先を決められないため救済しない。
回帰テスト: `test_exchange_change_updates_in_place_instead_of_new_id` /
`test_ambiguous_ticker_with_multiple_exchanges_does_not_hijack`。

**未対応（ユーザー判断待ち）**: 本番に残った id=3256 の掃除。§8 参照。

### 7.7 Phase 1 の stocktool 側変更は W6 完了まで永続しない

W3（DX-Y.NYB の `active=0`）と W9-3（JPY=X の `active=0`）は、日次パイプラインの
T1 同期（`extra_symbols` 注入）で `active=1` に戻された。**T1 のソースがスプレッドシートのままである以上、
これは想定内の挙動**であり、W6 で `extra_symbols` を削除して universe 経路に切り替えれば
universe.db に両者が存在しないため自動的に `active=0` になる。再適用は不要。

> [!WARNING]
> 日次パイプラインは毎朝 06:45 頃にスケジュール実行される。`agent_execution_rules.md` §10.5 の通り、
> **W6 の切替作業中は daily update と API サーバを停止すること。** 作業中に T1 同期が走ると
> 検証のベースラインが動く（本件で実際に発生した）。

### 7.5 fx_rates の実投入は 7,711 行（計画の 7,759 から変更）

計画書の 7,759 は生タイムスタンプ数。`close` が NaN の行と重複日を除いた実投入は 7,711 行。

---

## 8. スコープ外・残作業

### Phase 3（次フェーズ）: コールドスタート対応

**「DB が存在しなくてもフロントエンドを起動し、universe の編集とスプレッドシート import ができる」**状態にする。

現状調査で判明している前提:

- `db/database.py` の `init_db()` は `makedirs` + `Base.metadata.create_all()` を行うため、**DB ファイルが無くても空 DB が生成され、起動自体は通る見込み**
- `sheet_importer.fetch_sheet_csv()` は **gviz CSV export URL 経由で `credentials.json` 不要**（スプレッドシートの共有設定が「リンクを知っている全員」であることが前提）
- `App.tsx` は起動時に `/api/system/health` / `/api/symbols` / `/api/system/info` を叩く。空 DB で空配列が返れば画面は立つ見込み
- `/universe` は独立ルート（`App.tsx:521`）

**残っている確認事項**（Phase 3 着手時に実測する）:

- [ ] `config.toml` が存在しない場合 `server.py:31` の `load_config()` で起動失敗する → デフォルト値でのフォールバックが要るか
- [ ] 空 DB 状態で `/api/dashboard` 等が 500 を返さないか（フロントの初期表示が壊れないか）
- [ ] 空 DB 状態で `/universe` ページが到達・操作可能か
- [ ] import 実行後、universe.db → stocktool.db の初期構築を促す導線が必要か

### 別 issue として起票するもの

| 項目 | 内容 |
| :--- | :--- |
| **現物暗号資産の土日対応** | BTC-USD / ETH-USD は7日/週。取り込むなら T2 で SPY の取引日にリインデックスする等の対応が必要（§2.2 で見送り決定済み） |
| **`/ranking` のデッドコード掃除** | エンドポイント本体、`schemas.RankingItem` / `RankingResponse`、`types.ts:118` の `RankingItem`、`index.css` の `.ranking-*` が全て未使用 |
| **`/ranking` の母集団バイアス** | 復活させる場合、カテゴリ跨ぎでパーセンタイルを並べると母集団の小さいカテゴリ（指標7件・セクタ16件・市場26件）が構造的に上位を占める。カテゴリ別に返す設計が必要 |
| **universe import の replace モード保護（課題H）** | `sheet_importer.py:296-304` の replace モードは `symbols_master` を無条件全 DELETE する。`source IN ('pipeline','manual','system')` を保護対象にする。`ticker_history` に `_DRONE_`(旧`ARKX`) が残り実体が無いのが過去に発生した痕跡 |
| **`^VIX3M` のデータ停滞** | 最新 2026-07-17（SPY は 2026-07-24）。`relative_ranks` の指標カテゴリが8件中7件しかない原因 |
| **`stocktool.db` の肥大化** | 実測 `stocktool.db` 6.3GB + `-wal` 3.6GB。`architecture.md` §11.1 の想定は 1.7GB 前後で大幅超過。WAL が 3.6GB のまま残っておりチェックポイントが効いていない。`restore_sqlite_cache_from_parquet` によるホットキャッシュ再構築で解消できる（Parquet から直近730日を復元するだけなのでネットワーク不要・安全）。本移行とは独立 |
| **上場廃止銘柄の棚卸し** | active 31銘柄が SPY 最新日に追いついていない。うち `CNCR`(最終2025-06-03) / `LUX`(最終2025-08-08) は1年以上停止しており yfinance が配信を停止した可能性が高い。W8 の退役フロー整備後に一括棚卸しする |

### 本計画で扱わないと決めたもの

§2.2 の表を参照（universe id の持ち込み、T4 全期間再計算、DX-Y.NYB 物理削除、`_normalize_ticker` の分岐削除、`/ranking` 是正）。
