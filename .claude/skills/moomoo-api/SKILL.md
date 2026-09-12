---
name: moomoo-api
description: moomoo API（moomoo-api SDK + OpenD ゲートウェイ）で米国株の分割・配当記録やヒストリカル価格を取得する手順。upstream-data-diagnosis の第3段階（yfinance に分割記録が無い/曖昧な銘柄を独立ソースで照合する）や、将来のポートフォリオ moomoo 証券 API 連携で使う。セットアップ・接続時のハマりどころ・get_rehab の返り値の意味・クォータ/レート制限を実測でまとめてある。
---

# moomoo API (moomoo-api)

このプロジェクトの価格ソースは yfinance のみで、**上流が分割記録を持たない銘柄は原理的に
検出できない**（`upstream-data-diagnosis` §6 限界1）。moomoo API はそこを埋める独立ソース。
2026-09-11 に実アカウントで接続し、実測に基づいてまとめてある。

---

## 1. いつ使うか

- yfinance に分割記録が無い、または `scan_split_consistency.py` / `scan_price_anomalies.py` の
  判定が曖昧な銘柄を、独立ソースで照合したいとき（`doc/issue_list.md` P1 第3段階）
- 将来のポートフォリオ moomoo 証券 API 連携（`doc/issue_list.md` P3。保有銘柄の自動同期。
  **用途が別**なので、本skillの分割照合の使い方とは分けて考えること）

## 1.5. 本体コードからは `MoomooClient` Wrapper 経由で呼ぶ（2026-09-11実装）

> [!IMPORTANT]
> **本体コードから `moomoo` パッケージを直接 import しない。**
> `backend/data_collection/moomoo_client.py` の `MoomooClient` が唯一の入り口。
> `backend/tests/data_collection/test_moomoo_client.py::test_no_direct_moomoo_import_outside_client`
> が違反を機械的に検出する。

以下 §3〜§6 は raw の `moomoo` パッケージ自体の仕様（Wrapper がこれを踏まえて実装されている）。
**実装で直接これらを呼ばないこと。** `MoomooClient` が肩代わりする内容:

| 素の `moomoo` パッケージで自前対応が要ること | `MoomooClient` がやること |
| :--- | :--- |
| §3 の無限リトライ回避（ポート疎通の事前確認） | `port_is_open()` を内部で使い、繋がらなければ即座に `MoomooNotConnectedError` |
| レート制限（60req/30秒）を超えないよう自前で待つ | `SlidingWindowRateLimiter` が全呼び出しに自動適用（上限に達したら sleep） |
| `request_history_kline` のページングを自前でループする | `page_req_key` が尽きるまで内部で回し、結合した DataFrame を返す |
| クォータ残量を都度自分で `get_history_kl_quota()` を呼んで確認する | `usage_status()` でレート制限枠とクォータ残量をまとめて返す（未接続でも例外にならない） |

使い方:

```python
from data_collection.moomoo_client import MoomooClient, MoomooApiError, MoomooNotConnectedError

client = MoomooClient()                       # OpenD への接続は遅延（最初の呼び出し時）
print(client.usage_status())                   # 接続できていなくても例外にならない
rehab = client.get_rehab("SOXS")                # "US.SOXS" に自動変換
client.close()
```

テスト（`backend/tests/data_collection/test_moomoo_client.py`）は `context_factory` /
`port_check` を差し替えた `FakeQuoteContext`（`moomoo.OpenQuoteContext` のうち使う3メソッド
だけを再現する疑似オブジェクト）だけで検証しており、**実際の OpenD・ネットワークに一切繋がない**。
CIやOpenD未起動の環境でも Wrapper の単体テストは常に実行できる。

## 2. セットアップ

### 前提（2026-09 時点で実測確認済み）

- **口座開設・入金は不要。** moomoo アカウント登録（電話/メール）だけで API 利用可能
- 米国株（Nasdaq/NYSE 上場銘柄）の LV3 市場データ（Nasdaq Basic+TotalView, NYSE ArcaBook）は
  **現在プロモーションで無料**。将来有料化されうるので都度確認すること

### 手順

1. moomoo アカウントを登録する（SMS認証あり。**Claude Code が代理実行できないユーザー側の作業**）
2. OpenD（ローカルゲートウェイのデスクトップアプリ）をインストールし、ログインする
3. Python パッケージを**本体venv**へ入れる: `.\venv\Scripts\python.exe -m pip install moomoo-api`
   （依存: pandas / protobuf / pycryptodome / simplejson。2026-09-11時点で `moomoo-api==10.10.7008`）
4. OpenD を起動した状態で `moomoo.OpenQuoteContext(host='127.0.0.1', port=11111)` に接続する

## 3. 接続時の注意 — OpenD 未起動だと延々とリトライする

`OpenQuoteContext(...)` は接続失敗時（OpenD未起動）に**明確なタイムアウトなく繰り返しリトライする**
（実測: bash の `timeout 20` で強制終了しても、まだ3回目のリトライ中だった）。
**先に短いタイムアウトでポート疎通を確認するガードを入れること**:

```python
import socket

def opend_is_running(host="127.0.0.1", port=11111, timeout=3) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()
```

日本語を含む docstring を Windows のターミナルで見るときは `PYTHONIOENCODING=utf-8` が要る
（既定の `cp932` だと `UnicodeEncodeError` で落ちる。CLAUDE.md の文字コード規律と同型）。

## 4. 銘柄コード形式

`"{市場}.{ティッカー}"`。米国株は `US.AAPL` のように `US.` プレフィックスを付ける
（`moomoo.Market.US == 'US'`）。

## 5. `get_rehab(code)` — 分割・配当記録

1銘柄1呼び出し。分割・併合・配当をまとめて返す（yfinance の `actions=True` 相当だが情報量が多い）。

主なフィールド:

| フィールド | 意味 |
| :--- | :--- |
| `ex_div_date` | 権利落ち日 |
| `split_ratio` | 分割・併合の比率。**実測で確認した意味は「その日の価格倍率（当日終値/前日終値）そのもの」**。forward split（1株→N株、価格は1/Nに下がる）なら `split_ratio = 1/N`、reverse split（N株→1株、価格はN倍に上がる）なら `split_ratio = N`。**yfinanceの`Stock Splits`列の値を本プロジェクトの `factor`（`scan_split_consistency.py`の定義）とすると、`split_ratio == 1.0/factor` で完全に対応する**（下記の検算で確認済み） |
| `per_cash_div` / `special_dividend` | 配当・特別配当 |
| `forward_adj_factorA/B` / `backward_adj_factorA/B` | 前置/後置調整係数。`調整後価格 = 調整前価格 * A + B` |

> [!IMPORTANT]
> `split_base`/`split_ert`（拆股分子/分母）は**forward splitのときしか埋まらない**。
> reverse split（併合）は `split_ratio` だけが立ち `split_base`/`split_ert` はNaNになる
> （`STKH` で実測）。**判定には `split_ratio` だけを見ればよい。**

### 検算（2026-09-11実施、`US.AAPL`）

`get_rehab('US.AAPL')` が返す分割イベント5件は、既知のAAPL分割と**日付・比率とも完全一致**した:

| 日付 | moomoo `split_ratio` | 既知の分割 |
| :--- | ---: | :--- |
| 1987-06-16 | 0.5 | 2:1 |
| 2000-06-21 | 0.5 | 2:1 |
| 2005-02-28 | 0.5 | 2:1 |
| 2014-06-09 | 0.142857（=1/7） | 7:1 |
| 2020-08-31 | 0.25 | 4:1 |

→ **独立ソースとして信頼できることを実測で確認済み。** CBOE連携（`upstream-data-diagnosis` §6.1）と
同じ「採用前に既知の値で検算する」原則をここでも踏襲すること。

## 6. `request_history_kline` — ヒストリカルK線

```python
ctx.request_history_kline('US.AAPL', start='2026-07-20', end='2026-08-03',
                          ktype='K_DAY', autype='qfq')
```

- `autype`: `None`（無調整）／ `'qfq'`（前置調整＝過去を現在の株数に合わせて遡及修正）／
  `'hfq'`（後置調整）
- 取得可能期間: 日足は過去20年、60分足以下は過去8年のみ
- **`get_history_kl_quota()` で確認できる「ヒストリカルK線クォータ」を消費する**
  （直近7日以内の同一銘柄再取得は消費しない）。戻り値は `(ret_code, (used_quota,
  remain_quota, detail_list))` の2段タプル。**`remain_quota` は「残量」であって
  固定の「上限」ではない**（`used_quota + remain_quota` が総枠）。
  実測（2026-09-11、当日1回目の呼び出し）: `used=0, remain=300`
  → その後 `MoomooClient.request_history_kline` を1回呼ぶと `used=1, remain=299` に変化した。
  **`get_rehab` はこのクォータを消費しない**（`get_rehab` を複数回呼んでも `used` は変化しない）
- レート制限: 60リクエスト/30秒

## 7. 実施済みの照合結果（2026-09-11、`doc/issue_list.md` P1 第3段階向け）

### `SOXS`

これまで「上流(yfinance)に記録が無く、観測比0.054から1:20と推定」だった件について、
**moomooには2026-03-05付で `split_ratio=20.0` の併合記録が実在**した。推定値が確定値になった。
なお記録日（2026-03-05）と手元データで段差が観測された日（2026-05-26）が約2.5か月ズレているが、
これは `upstream-data-diagnosis` の限界3（日次差分取得では過去の調整が反映されない）で説明できる。

### `STKH`

「2026-07-27 ×1:3の分割記録に対し実測段差が2.6667＝11%外れ」だった際どいケース
（`upstream-data-diagnosis` §6.2「近傍だが比が一致しない」）。
moomooも**同じ2026-07-27に `split_ratio=3.0`（1:3のreverse split）を記録**しており、
分割比自体はyfinanceの記録と一致していた。moomoo独自の実売買価格
（`request_history_kline`、`autype='qfq'`）を見ると 07-27終値1.35→07-28終値3.60＝比2.667倍と、
**手元データと同じ「11%外れた」比率がそのまま再現された**。分割適用の不備ではなく、実際の値動き
（逆併合＋実勢の下げが同時に発生）である可能性が高いと判断できる。

## 8. 未確認・今後の課題

- **REST API**（`open.moomoo.com`、OAuth 2.1+PKCE またはAPIキー、ゲートウェイ不要）は本skillの
  対象外。`/api/quote/basic-data/rehab` の存在は確認したが、ドキュメントが薄く米国データの
  権限体系が未確認。自動化（無人実行）を検討する段になったら再調査する
- 長時間無人稼働時のトークン失効・再ログインの扱いは未検証（現状は手動実行前提なので影響なし）
- 「プロモーションで無料」の期限は不明。有料化されたらこの検証手段の費用対効果を再評価すること
- moomoo自体も「もう一つの独立ソース」に過ぎない。2026-09-04のMNST事故（上流一致を歯止めにした
  結果、壊れた上流に合わせて正しいデータを壊した）の教訓から、**moomooの値だけで自動修正しない**。
  判断は人間に残す（`scan_split_consistency.py` と同じ方針）

## 9. 存在を確認したが未使用の他のAPI面（参考。今後の調査候補）

`dir(moomoo)` で列挙した際に見つかった、`MoomooClient` がまだラップしていないAPI群。
使う場合は `MoomooClient` に追加してから使うこと（§1.5の制約）。活用アイデアは
`doc/issue_list.md` P3「moomoo API 知見の活用アイデア」参照。

| 分類 | API/型 | 用途の見立て |
| :--- | :--- | :--- |
| 決算 | `EarningsCalendar*`（`EarningsCalendarEstimateType`/`PubType`等） | 決算日・予想値。stocktoolの決算関連機能との重複可能性は未検証 |
| 配当ランキング | `DividendRank*` | 高配当銘柄のスクリーニング（stocktool側の用途は未検討） |
| セクター/テーマ | `get_owner_plate` | 銘柄が属する業種・概念分類。stocktoolの「テーマ」分類との対応は未検証 |
| 基本情報 | `get_stock_basicinfo` | 銘柄の基本情報一覧（上場市場・種別等） |
| 為替 | `Market.FX` | 為替レート。`fx_rates`のクロスチェックに使えるか未検証 |
| リアルタイム | `subscribe`/`get_rt_data`/`get_order_book`/`get_cur_kline` | リアルタイム気配・板情報。現状の日次バッチ設計とは前提が異なる（大掛かりな変更になる） |

いずれも実際に呼び出して仕様（引数・返り値の形・クォータ消費有無）を確認していない。
使う前に `US.AAPL` 等の既知の値で検算すること（§5の原則と同じ）。

