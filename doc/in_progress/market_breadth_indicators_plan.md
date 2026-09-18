# S&P500市場ブレッド指標(S5FI/S5TH)取り込み 計画書

- **ステータス**: 🚧 進行中
- **実施者**: AI エージェント(Claude Sonnet 5)
- **開始日**: 2026-09-19 / **完了日**: —
- **作業ブランチ**: `worktree-feat-market-breadth-indicators`
- **対象 issue / 関連ドキュメント**: なし(新規)。関連(スコープ外・別系統): `doc/in_progress/min_periods_warmup_plan.md`(`market_signals.py:174` の `has_breadth` 日付ハードコードは同じ「MTS の breadth 成分」領域だが、SPY 自身の `breadth_sma50` の話で本計画とは別問題)

## 1. 背景と目的

ユーザーが `S5FI`(S&P500 Stocks Above 50-Day Average)/`S5TH`(同200-Day Average)という市場ブレッド指標を Market Trend Score 等の判断材料に使いたいとのことで着手。

検討の変遷(詳細は会話履歴):
1. 当初は「S&P500構成銘柄を自前で母集団管理し、既存の `breadth_sma50` と同じロジックで自前計算する」方向で検討(universe.db への構成銘柄タグ付け等)
2. ユーザーから「リストを取得して自前計算するのではなく、S5FI の値そのものをダウンロードしたい」と方針転換
3. 値そのものを配信する無料ソースを探索。Investing.com(自動化不可・Cloudflareで実際にブロック確認)、FMP(値自体を返すエンドポイントが無い)、`historyofmarket.com`(独自集計・2023-07以降のみ)を検証した上で、**`tvDatafeed`(TradingViewの非公式データ取得ライブラリ)が匿名アクセスで2006年まで遡れ、値が Investing.com の実測値と完全一致する**ことを確認
4. ユーザーが `data/fixed_data/S5FI.csv` / `S5TH.csv`(Investing.comから手動エクスポート、2009-01-02〜2026-09-17)を投入済み
5. データ取得元が yfinance / tvDatafeed と複数になるため、**取得元振り分けを1箇所に集約するラッパー**を導入する方針に確定

**完了時の成功条件**: `S5FI`/`S5TH` が `category='指標'` の銘柄として `symbols_master` に登録され、日次パイプライン(T2)経由で `stocktool.db`/Parquet の `daily_prices` に値が入り続ける状態になっていること。既存のダッシュボード「先行指標パネル」(`category='指標'` が対象、ハードコードなし)に自動的に表示されることを確認する。

**本計画のスコープ外**: MTS(Market Trend Score)の計算式自体への組み込みは別タスクとする(§8)。本計画は「データが安定して入ってくる状態を作る」までを対象とする。

## 2. スコープと設計判断

### 2.1 変更すること

1. **データ取得ルーターの導入**(新設 `backend/data_collection/data_source_router.py`)
   - `fetch_daily_data(ticker, start_date, end_date, progress)` の外部契約(シグネチャ・戻り値のDataFrame列: `date/open/high/low/close/volume`)は変更しない。`fetcher.py` はこのルーターを呼ぶだけの薄い入口にする
   - 内部を以下の順で分岐させる:
     1. `data/fixed_data/<ticker>.csv` が存在する場合、そこから `start_date` 〜 min(`end_date`, ファイル最終日) をまず充足する
     2. 残りの範囲(ファイル最終日より後、またはファイルが無い場合は全範囲)を、ティッカー別の振り分けテーブルで解決:
        - 既定: yfinance(現行ロジックをそのまま移動)
        - 特例: `{'S5FI': 'tvdatafeed', 'S5TH': 'tvdatafeed'}`(コード内辞書。**設計判断: DBカラムではなくコード内辞書で管理** — §2.2参照)
     3. tvDatafeed経路が失敗(例外・空データ)した場合、**`historyofmarket.com` の breadth JSON APIへフォールバック**(§2.2で確定)。フォールバックも失敗した場合は既存の失敗契約(空DataFrame/None)に従い、その日は更新しない(次回差分取得で追いつく)
2. **tvDatafeedアダプタの新設**: `data_collection/tvdatafeed_client.py`
   - `tvdatafeed-enhanced` をラップし、`fetch_daily_data` と同じ列構成の DataFrame を返す
   - 検証済み: `exchange='INDEX'` で `symbol='S5FI'` / `'S5TH'` を指定すると匿名アクセスで取得できる(2006-12-29〜取得確認済み)。他の exchange prefix(`TVC`/`SP`/`AMEX`/`CBOE`)は接続が切られる(`Connection to remote host was lost`)ため使わない
   - `n_bars` 引数のみで期間指定する(TradingViewの内部プロトコル制約。start_date/end_date から `n_bars` への変換ロジックが必要)
3. **historyofmarket.comフォールバックアダプタの新設**: `data_collection/historyofmarket_client.py`
   - `GET https://historyofmarket.com/api/sp500/breadth.json` を取得し、`pct50`→S5FIの`close`、`pct200`→S5THの`close`にマッピング(open/high/low は close と同値、volume=0で補完)
   - 単一JSONに全期間が入る形式なので、取得後に`start_date`〜`end_date`でフィルタする
   - 2023-07-13より前の日付は返せない(その場合は空DataFrameを返し、呼び出し元がそのまま「データ無し」として扱う)
   - ライセンス: CC BY 4.0。属性表示 `"History of Market · 美股编年史 (historyofmarket.com)"` をコード内コメントに明記する(再配布はしないが出典は残す)
4. **fixed_dataローダーの新設**: `data_collection/fixed_data_loader.py`
   - `data/fixed_data/<ticker>.csv` を読み、`fetch_daily_data` と同じ列構成で返す
   - **UTF-8 BOM付き**(`ef bb bf` 確認済み)。`encoding='utf-8-sig'` で読むこと
   - 列: `日付,終値,始値,高値,安値,出来高,変化率 %` → `date,close,open,high,low,volume,change_pct`(出来高列は空欄。`volume=0` で補完)
5. **`symbols_master` への登録**: `S5FI` / `S5TH` を `category='指標'` として追加
   - `exchange='INDEX'`(確定。tvDatafeed側の呼称に合わせる。既存 `symbols_master` に未使用の値であることを確認済み)
   - `exchange='VIRTUAL'` は使わない(`derive_theme_type()` が `theme_type='virtual'` と誤判定し、仮想テーマ指数合成パイプラインに乗ってしまうため。§2.2参照)
6. **`requirements.txt`** に以下を pin 追加(pip index で確認した現時点の最新版):
   - `tvdatafeed-enhanced==2.2.1`
   - `websocket-client==1.9.2`
7. **`data/fixed_data/` を git 管理下に置く** — 実装時に確認したところ `.gitignore` の `data/*.csv`(77行目)は `data/` 直下のみに一致し `data/fixed_data/*.csv` には一致しない(`git check-ignore` で無視されないことを確認済み)。**`.gitignore` の変更は不要**、そのまま `git add` すればよい

### 2.2 変更しないこと(確定した設計判断)

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| S&P500構成銘柄を universe.db で母集団管理 | 見送り | 値そのものを外部取得する方式に転換したため不要になった |
| 母集団の時点履歴(historical membership) | 見送り | 同上 |
| データ取得元振り分けを DB カラム(`symbols_master.data_source`等)で管理 | 見送り。コード内辞書で管理 | 対象は現時点で2件のみ。DBに持たせるのは③の検討で見送った「新規カラム」パターンの再来で過剰(YAGNI)。3〜4件を超えたら見直す |
| FMP API での定期取得 | 見送り | `sp500_constituent`/`tradable` 系はいずれも構成銘柄の**リスト**を返すエンドポイントで、S5FI/S5TH の**値そのもの**を返すエンドポイントが見つからなかった |
| Investing.com / Barchart の自動スクレイピング | 見送り(手動エクスポートのみ採用) | 実際に `curl` でアクセスしテスト。Investing.comはCloudflareの管理者チャレンジ、BarchartはAWS WAFのbotチャレンジで**いずれも実測でブロックされることを確認** |
| `historyofmarket.com` API の併用 | **採用(フォールバックとして)**。tvDatafeed失敗時のみ使用し、通常経路はtvDatafeed | 値がtvDatafeed/Investing.comと完全一致しない(自前集計のため主経路には使わない)が、tvDatafeedが不安定化した場合の保険として2023-07以降はカバーできる。2023-07より前はフォールバックも効かない(fixed_dataの範囲であり通常はそちらでカバーされる) |
| `exchange='VIRTUAL'` での登録 | 見送り | `symbol_classify.derive_theme_type()` が `theme_type='virtual'` と判定し、T2の仮想指数合成(`build_all_virtual_indexes_prices`)・T3のRS計算対象になってしまう。S5FI/S5THは構成銘柄からの合成ではなく外部取得値なので不適合 |

## 3. 変更内容

| # | 何を | なぜ | 対象ファイル | 影響範囲 |
| :-- | :--- | :--- | :--- | :--- |
| 1 | `data_source_router.py` 新設、`fetcher.py`をそこへの薄い入口に変更 | 取得元が複数(yfinance/tvDatafeed/historyofmarket/fixed_data)になったため一元化 | `backend/data_collection/data_source_router.py`(新規)、`backend/data_collection/fetcher.py`(変更) | **既存呼び出し元は無改修**(`t2_prices.py`, `scan_ipo_candidates.py`, `backfill_symbol_history.py`, `delete_symbol_rows.py` 等)。シグネチャ・戻り値契約を維持するため |
| 2 | tvDatafeedアダプタ新設 | S5FI/S5THの継続取得(主経路) | `backend/data_collection/tvdatafeed_client.py`(新規) | 新規ファイルのみ |
| 3 | historyofmarket.comフォールバックアダプタ新設 | tvDatafeed失敗時の保険 | `backend/data_collection/historyofmarket_client.py`(新規) | 新規ファイルのみ |
| 4 | fixed_dataローダー新設 | 過去分(2009-2026)のバックフィル・将来同種ケースの汎用受け皿 | `backend/data_collection/fixed_data_loader.py`(新規) | 新規ファイルのみ |
| 5 | `symbols_master` に2行追加 | S5FI/S5THを`指標`カテゴリとして登録(`exchange='INDEX'`) | `data/universe.db`(手動追加、または小さな投入スクリプト) | T1同期で`stocktool.db`/Parquetへ伝播。既存銘柄には影響なし |
| 6 | `requirements.txt` 更新 | 新規依存の追加(pin指定) | `requirements.txt` | `tvdatafeed-enhanced==2.2.1` / `websocket-client==1.9.2` をvenvへ追加インストール |
| 7 | `data/fixed_data/` を `git add` | 静的シードをコミット対象にする | (計画時の想定と異なり `.gitignore` 変更は不要。実装時に確認済み) | ファイル追加のみ、既存ルールへの影響なし |
| 8 | `attach_market_cap()` の動作確認 | S5FI/S5THにyfinance非対応銘柄向けの安全装置が効くか | `backend/pipeline/utils.py`(コード変更なし、動作確認のみ) | 既存の try/except で `market_cap=None` に落ちることをコードリーディングで確認済み。テストで再確認 |

## 4. ユーザー確認事項

以下5点はレビューで確認済み。判断結果は§2.2/§3に反映済みで、ここには経緯のみ残す。

1. **`exchange` 値**: `'INDEX'` で確定 → §2.1/§3
2. **`historyofmarket.com` のフォールバック併用**: 採用(フォールバックとして) → §2.1/§2.2/§3
3. **ルーターの配置場所**: 新設の `data_source_router.py` に分離 → §2.1/§3
4. **`tvdatafeed-enhanced` のバージョン固定**: pinする方針で確定。実装時点の最新版 `2.2.1`(`websocket-client`は`1.9.2`)を採用 → §2.1/§3
5. **MTSへの組み込み**: 本計画のスコープに含めない。別計画書で対応 → §1/§8

**未解決の確認事項はなし。**

## 5. 実装順序と進捗チェックリスト

- [x] `data/fixed_data/` の既存2ファイルをコミット(`.gitignore`変更は不要と判明。実装時メモ参照)
- [x] `data_collection/fixed_data_loader.py` のテスト作成(TDD red、test-writerに委譲。commit `a905236`→cherry-pick `91ae8d5`で取り込み済み)
- [ ] `data_collection/fixed_data_loader.py` の実装(テストをgreenにする)
- [x] `requirements.txt` に `tvdatafeed-enhanced==2.2.1` / `websocket-client==1.9.2` を追加し、venvへインストール
- [x] `data_collection/tvdatafeed_client.py` のテスト作成(同上コミットに含む)
- [ ] `data_collection/tvdatafeed_client.py` の実装(テストをgreenにする。`TvDatafeed`はモジュール名前空間に直接importすること — テストが`monkeypatch.setattr(mod, "TvDatafeed", ...)`で差し替える前提)
- [x] `data_collection/historyofmarket_client.py` のテスト作成(同上コミットに含む)
- [ ] `data_collection/historyofmarket_client.py` の実装(テストをgreenにする)
- [x] `data_collection/data_source_router.py` のテスト作成(同上コミットに含む)
- [ ] `data_collection/data_source_router.py` の実装(テストをgreenにする。**下位関数は`from data_collection.xxx import yyy`の形でモジュール名前空間に直接importすること** — テストが`monkeypatch.setattr(router, "load_fixed_data", ...)`のように差し替える前提。`import ... as mod`形式だとテストの差し替えが効かない)
- [ ] `fetcher.py` の `fetch_daily_data()` をルーター呼び出しに差し替え(既存テストが通ることを確認 — 既存銘柄はyfinance経路のまま変化しないことの回帰確認)
- [ ] Sandbox環境で `symbols_master` に `S5FI`/`S5TH` を投入し、T1→T2が正しく通ることを確認([[sandbox-workflow]] 必須)
- [ ] Sandboxで `--rebuild-from T2 --category 指標` 相当の動作確認(fixed_data 2009-2026 + tvDatafeed tail 分が正しく `daily_prices` に入るか)
- [ ] ダッシュボードの先行指標パネルにS5FI/S5THが自動表示されることを確認(フロントエンド無改修の想定を検証)
- [ ] 本番へ昇格(種別B: `tools/deploy_after_merge.ps1`)
- [ ] `doc/backend_specification.md` に S5FI/S5TH の指標定義・データ取得元を追記
- [ ] 本計画書を `doc/completed/` へ移動

### 作業中メモ

(未着手)

## 6. 検証プラン / 結果

### 6.1 反証 — この結論が誤りだとしたら、何が観測されるはずか

- **この結論が誤りだとしたら観測されるはず**:
  - tvDatafeedの匿名アクセスが継続利用(日次)で早期にブロックされる(今回の検証は数回のスポット実行のみで、連続運用での安定性は未確認)
  - tvDatafeedが返す値がInvesting.comの値と将来ズレる(今回一致を確認したのは2026-09-17時点の直近数日分のみで、全期間・全日付での突き合わせはしていない)
  - `fetch_daily_data` のルーター化により、既存の3,300以上の銘柄(yfinance経路)の取得タイミング・リトライ挙動が意図せず変わる
- **独立経路での確認**:
  - 実施済み: tvDatafeedの`INDEX:S5FI`/`INDEX:S5TH`の値(2026-09-11〜09-17)と、ユーザーが手動投入した`data/fixed_data/S5FI.csv`/`S5TH.csv`(Investing.com由来)の同日値を突き合わせ、**完全一致を確認**(例: 2026-09-17 S5FI close=30.81、両ソースで一致)。これは系列内の自己整合ではなく、**別ベンダー(TradingView vs Investing.com)間の突き合わせ**なので独立経路の確認として成立する
  - 未実施: tvDatafeedの日次連続取得の安定性(1週間程度の実運用観察が必要。実装後、本番投入前にSandboxで数日様子を見る運用とする)
  - 未実施: fixed_data全期間(2009-2026、4,461日)とtvDatafeedの同期間全体の突き合わせ(今回は直近数日のみ)。実装時に取り込みスクリプトの検証ステップとして全期間diffを取ることを推奨

### 6.2 転記の完全性

- **転記元**: 本会話(ユーザーとの設計検討、2回のレビューラウンド)。issue_list.md 等への事前記載なし
- **元の件数**: 会話内で確定した設計判断は12件
  - 採用(§2.1相当、6件): tvDatafeed採用(主経路)、fixed_data優先読み込み、ルーター新設(`data_source_router.py`に分離)、historyofmarket.comフォールバック採用、`exchange='INDEX'`確定、`tvdatafeed-enhanced`/`websocket-client`のバージョンpin確定
  - 見送り(§2.2相当、5件): S&P500構成銘柄のuniverse.db母集団管理、母集団の時点履歴、データ取得元振り分けのDBカラム化(→コード内辞書)、FMP APIでの値取得、Investing.com/Barchartの自動スクレイピング
- **本計画書の件数**: §2.1に6件、§2.2に6件(採用6+見送り5=11件が会話由来。`exchange='VIRTUAL'`見送りの1件は会話には無く、コードリーディング(`derive_theme_type()`の挙動確認)で新たに判明したため計画書側で追加)
- **差分の説明**: 会話由来12件 + コードリーディングで新規判明1件(`exchange='VIRTUAL'`見送り) = 計画書は13件相当を記載。過不足なし

## 7. 途中発生した課題

- **事象**: 計画時、`data/fixed_data/` をコミットするには `.gitignore` に例外追加が必要と想定していた
- **原因**: `.gitignore:77` の `data/*.csv` は gitignore の仕様上 `/` を越えて一致しない(直下のCSVのみが対象)ため、`data/fixed_data/*.csv` はそもそも無視されていなかった
- **解決**: `git check-ignore -v data/fixed_data/S5FI.csv` で無視されないことを確認。`.gitignore` の変更をスキップし、そのまま `git add` した(§2.1/§3 の記載を修正済み)

## 8. スコープ外・残作業

- **MTS(Market Trend Score)への組み込み**: 本計画はデータ取り込みのみ。計算式への反映は別計画書で行う
- **`market_signals.py:174` の `has_breadth` 日付ハードコード**: 別系統の既知課題(`doc/in_progress/min_periods_warmup_plan.md`)。本計画とは無関係だが同じ「MTSのbreadth成分」領域なので、着手時に相互に影響しないか軽く確認する
- **他の指数(S&P400, Russell等)への拡張**: 今回はYAGNIで見送り。tvDatafeedの`exchange='INDEX'`パターンが他指数でも通用するかは未検証
