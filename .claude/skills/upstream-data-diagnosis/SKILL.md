---
name: upstream-data-diagnosis
description: 上流データソース(yfinance / Yahoo Finance)起因のデータ異常を切り分ける手順と、既知の限界カタログ。銘柄の履歴が短い・データが古い・上場廃止候補に出た・価格に段差がある・退役させるか迷う・完全再構築を実行する、といった場面で使う。「取得側の不具合」と「供給側の問題」を取り違えると本番データを壊すため、判断の前に必ず参照する。
---

# 上流データ異常の診断 (upstream-data-diagnosis)

このプロジェクトの唯一の価格ソースは yfinance / Yahoo Finance であり、**上流は静かに壊れる**。
「手元のデータがおかしい」ときに、**取得側（こちらのバグ）と供給側（Yahoo）のどちらが原因か**を
切り分けるための手順書と、実測で確認した限界の一覧。

すべて 2026-07-28〜08-02 の実測に基づく。`doc/issue_list.md` にも同じ内容が載っているが、
**課題は解決すると issue_list から消えるため、恒久的な知識は本 skill を正とする。**

---

## 0. 最重要の原則

**yfinance のエラーメッセージだけで原因を判断してはいけない。**

`yf.download` は以下をすべて `possibly delisted; no price data found` という同じ文字列に畳む。

| 実際の事象 | 対応 |
| :--- | :--- |
| HTTP 404（ティッカーが存在しない） | 退役・改称調査 |
| HTTP 429（レート制限） | 時間を置いて再試行。**退役させたら事故** |
| 開始日 > 終了日 | 何もしなくてよい（取得済みが最新なだけ） |
| 供給側の一時的な障害 | 復旧を待つ |

**3つ以上の全く異なる事象が同じ見た目になる。** この誤読で実際に事故が起きている:

- 2026-07-29: 「45銘柄は T2 差分取得ロジックの穴が原因」と誤診断し、本番の55行を不要に削除
- 調査中も繰り返し `possibly delisted` に引っかかり、原因特定が遅れた

判断には必ず **Yahoo chart API を直接叩き、HTTP ステータスと `meta` を見る**（§7 のスニペット）。

---

## 1. 切り分けの決定木

```
手元のデータがおかしい
  │
  ├─ 複数銘柄が同時に失敗している？
  │    YES → レート制限(429)を疑う → §2 決定論テスト
  │    NO  → 個別銘柄の問題 → 以下へ
  │
  ├─ chart API が HTTP 404 を返す？
  │    YES → 本当に存在しない。改称か上場廃止 → §5
  │
  ├─ 名称は返るのに時系列が短い？
  │    YES → §3 銘柄レコード再作成の判定 ★最も見落としやすい
  │
  ├─ タイムスタンプはあるのに OHLCV が null？
  │    YES → 供給側の部分障害(限界C)。再取得でも直らない。復旧を待つ（実績: 8営業日）
  │
  └─ 価格に段差がある？
       YES → §4 売買代金テストで「未調整の分割」と「実際の急変」を判別
```

---

## 2. レート制限か、データの問題か（決定論テスト）

**レート制限は確率的・時間変動する。データ欠損は決定論的。** これが決め手になる。

正常銘柄（AAPL / SPY）と対象銘柄を**同一セッションで交互に**叩き、複数周する。

```python
for rnd in range(3):
    for tk in ["AAPL", TARGET]:
        print(rnd, tk, probe(tk)["rows"])
    time.sleep(2)
```

実測例（2026-08-02）:

```
周   AAPL                    BLD                LC                  SCVL
1    11500行 from 1980-12-12  3行 from 2026-06-30  11行 from 2026-07-17  1行 from 2026-07-17
2    11500行 from 1980-12-12  3行 from 2026-06-30  11行 from 2026-07-17  1行 from 2026-07-17
3    11500行 from 1980-12-12  3行 from 2026-06-30  11行 from 2026-07-17  1行 from 2026-07-17
```

| 観測 | 結論 |
| :--- | :--- |
| AAPL も同時に失敗する | レート制限。制御はセッション/IP 単位なので巻き込まれる |
| AAPL は毎回正常・対象だけ毎回同じ結果 | **供給側のデータ問題**。再試行しても無駄 |
| 周ごとに結果が揺れる | 供給側が不安定（限界G）。1回の結果で恒久的な判断をしない |

`query1` / `query2` の両ホストで確認するとなお確実。応答が 0.1 秒なら
タイムアウトによる切り詰めでもない。

---

## 3. 銘柄レコードの再作成（系列切断）

**2026-08-02 発見。A〜I のどれにも当てはまらない新種。**

**症状**: Yahoo はティッカーを認識し、正式名称も取引所も返すのに、**時系列だけが最近の日付から始まる**。
上場廃止でも改称でもない。現役の上場企業（S&P500 構成銘柄含む）で起きる。

**決め手は `firstTradeDate` と 52週レンジの自己矛盾**:

```
BLD (TopBuild, S&P500)
  firstTradeDate = 2026-06-30（1ヶ月前）   ← ありえない
  fiftyTwoWeekHigh = 559.47 / Low = 330.51 ← 1年分のデータが無いと算出できない
```

集計レイヤーには履歴があるのに時系列だけが孤立している = Yahoo 側で銘柄レコードが作り直された。
`range=2y` を指定してもタイムスタンプ配列自体が23個しか返らないので、パラメータの問題ではない。

```python
# backend/scripts/retire_stale_symbols.py の _is_truncated() が実装
def is_truncated(meta):
    ftd = meta.get("firstTradeDate")
    lo, hi = meta.get("fiftyTwoWeekLow"), meta.get("fiftyTwoWeekHigh")
    if not ftd or lo is None or hi is None or hi <= lo:
        return False   # 新規上場は 52週レンジが立たない(lo==hi)ので誤検出しない
    first = datetime.fromtimestamp(ftd, timezone.utc).date()
    return (date.today() - first).days < 365
```

実績: 対象17件を **17/17 検出、対照群 AAPL/SPY/MSFT は誤検出ゼロ**。
被害銘柄: `BLD` `LC` `SCVL` `GTLS` `SEM` `CPRX` `NUVL` `CNTA` `TBRG` `EDAP` `ORGN` `SSSS`
`UGRO` `ATLN` `CWAN` `RSHO` `CORZZ`。`firstTradeDate` は 06-24 / 06-30 / 07-02 / 07-09 /
07-15 / 07-17 に固まっており、特定日にまとめて処理された形跡がある。

**対応**:
1. **退役させない**（現役企業をバックテスト母集団から落とすことになる）
2. 旧 Parquet 世代から継ぎ直す: `backend/scripts/restore_truncated_symbol_history.py --dry-run`
3. 上流が復旧しても自動では戻らない。手元で復元する以外にない

**復元時の落とし穴**:
- **世代間で `symbols.id` は一致しない**（17件とも別 id だった）。**ticker で突合して振り直す。**
  旧 id のまま入れると別銘柄の系列を破壊する
- **旧世代の T3/T4 は流用不可**。指標列は 50→63、順位列は 17→26 に増えている。
  流用すると新しい列が欠損したままスクリーナー・バックテストに入る。**再計算する**

---

## 4. 未調整の分割か、実際の急変か

価格比だけでは判別できない。**売買代金（close × volume）の連続性**が決定的。
分割は株数が変わるだけなので代金は連続し、実際の急騰は代金も跳ねる。

| ticker | 価格比 | 売買代金比 | 判定 |
| :--- | ---: | ---: | :--- |
| `SOXS` | 0.05 | **1.16** | 未調整の分割 |
| `WBX` | 19.37 | **0.71** | 未調整の分割（1:20 併合） |
| `UGRO` | 0.60 | **0.057** | 分割ではない（実際の下落＋流動性枯渇） |
| `BMNR` | 7.95 | **32,440** | 実際の急騰（暗号資産トレジャリー） |
| `SBET` | 5.33 | **47.9** | 実際の急騰 |

ティッカー名や倍率の見た目では誤判定する（`WBX` の +1837% は実急騰に見えるが併合だった）。
出来高が 0 の証券（トランシェ等）では判定できないので、その旨を明記して保留する。

---

## 5. 退役させてよいかの判断

退役は**不可逆な運用判断**。`backend/scripts/retire_stale_symbols.py` は既定で供給側を照会する。
`--no-verify` で迂回できるが、**使わないこと**。

| 供給側の状態 | 意味 | 対応 |
| :--- | :--- | :--- |
| `gone` (404) | 本当に存在しない | 退役してよい。改称の可能性も調べる |
| `truncated` | §3 の系列切断 | **退役不可**。復元する |
| `alive` | データがある | 退役ではなく取得側の問題 |
| `empty` | 応答はあるがデータなし | 保留。時間を置いて再照会 |
| `error` | 判定不能 | 保留。再実行 |

週次監査（`weekly_maintenance.py`）の鮮度分類も併用する。**行数を見ずに「最終日が古い」だけで
判定すると現役企業を退役候補に出す**（旧実装で26銘柄が誤検出された）。

| 分類 | 条件 | 対応 |
| :--- | :--- | :--- |
| `delisted` | 21行以上 かつ 最終日が5営業日以上前 | 退役候補 |
| `no_history` | 20行以下（または1行も無い） | 退役候補。ただし §3 の切断でないか必ず確認 |
| `lagging` | 完全な履歴があり最終日が1〜2営業日前 | **放置**（次回実行が `current_max + 1` から取るので自己回復） |
| `ok` | 追いついている | — |

---

## 6. 既知の限界カタログ

### 分割・併合（再取得では回避できない構造的限界）

**限界1: Yahoo が分割記録を持っていない銘柄がある**
`auto_adjust=True`（yfinance 1.3.0 の既定）は Yahoo 自身の分割・配当記録に依存する。
記録が欠けている銘柄では調整されず、生の価格ジャンプが残る。**何度取り直しても直らない。**
```
SOXS 2026-05-22 → 05-26   自社 1146.15 → 62.18 ／ Yahoo 1159.50 → 62.90
UAVS 2024-09-27 → 09-30   自社   13.50 → 4.50  ／ Yahoo   13.50 → 4.50（完全一致）
```
2026-07-30 に Parquet 全消去＋全期間再取得しても 299 件が残ったことで確認済み。

**限界2: 調整は「現在のティッカー」にしか反映されない（遡及的）**
分割調整は過去の全価格を遡って書き換えるが、載るのは**現行ティッカーの系列だけ**。
改称・廃止された旧ティッカーの系列は、死んだ時点の未調整の姿で凍結される。
```
EDOC（旧）  2020-07-30 〜 2025-09-01 は HEAL の 1/3 の水準（1:3 併合が未調整）
HEAL（新）  全期間が調整済みの連続系列
```
→ **改称を universe.db に反映すれば解消する**（`backend/scripts/rename_symbol.py`）。

**限界3: 日次の差分取得では過去の調整が反映されない**
T2 は `current_max + 1日` からの差分取得なので、分割が起きても既存行は書き換わらない。
調整済みの新しい行が未調整の古い行に継ぎ足され、分割日に段差が残る。
→ 限界1・2と違い、**全期間の取り直しで解消する**。

### API・ライブラリの挙動（A〜J）

**A. HTTP 404 と 429 を同じメッセージに畳む — 最も危険**
§0 参照。切り分けは chart API を直接叩いて HTTP ステータスを見る。

**B. レート制限(429)を踏みやすく、部分成功が最も危険**
数十リクエストの連続で 429 になる。3,000銘柄規模の一括取得では部分失敗が起きうる。
**1行でも入ると T2 は差分取得に切り替わり、過去を二度と取りに行かない（自己修復しない）。**
→ 大規模再取得の後は必ず鮮度監査を行い、`no_history` に落ちた銘柄を個別に取り直す。

**C. タイムスタンプ行はあるのに OHLCV がすべて null（部分的な供給停止）**
`^VIX3M` が 2026-07-20〜07-29 の8営業日、`AVNS` `KORE` `TMHC` が 07-20〜07-30 で発生。
`^VIX` `SPY` `AAPL` `ALNY` は正常 = **一部銘柄だけの系統的な障害**。再構築でも直らない。
行数は十分あるため「履歴なし」判定に引っかからず、**上場廃止候補に紛れて埋もれる**。
07-30 に自然復旧した。**代替値で埋めようとしないこと** — `^VIX3M` で proxy 式を検討したが、
復旧後の実測 20.51 に対し ffill の誤差 0.055pt / proxy の誤差 4.055pt（73倍悪い）だった。

**D. 死んだティッカーが「残骸1行」を返す**
改称後の `FDP` `IAC` `VSCO` はいずれも `2026-07-17` の1行だけを返した。
完全な 404 ではないため**「まだ生きている」と誤判定させる**。改称の自動検出ができない理由。

**E. `range=max` は `interval=1d` を指定しても日足を返さない**
```
JPY=X  range=max        358行（12行/年 = 月足）
JPY=X  period1=0 明示  7,759行（日足）
AAPL   range=max        168行            ← ダウンサンプリングされる
AAPL   period1=0 明示  11,500行（日足）
```
**日足が必要なら `period1` / `period2` を明示する。**

**F. 日付のタイムゾーン変換を誤ると存在しない土日行が生成される**
chart API のタイムスタンプを UTC で変換すると、為替(`JPY=X`)で金曜バーが土日にずれ込む。
`meta.exchangeTimezoneName`（`JPY=X` なら `Europe/London`）で変換する。
```
UTC 変換            Mon1552 Tue1551 Wed1552 Thu1552 Fri649 Sun903  ← 土日が発生
Europe/London 変換  Mon1552 Tue1551 Wed1552 Thu1552 Fri1552       ← 正常
```

**G. 同じ問い合わせでも結果が揺れる**
`^VIX3M` を `range=1mo` で照会した際、ある時点では「07-29 に有効値あり」、後の照会では
「07-29 は null / 07-30 に値あり」。**1回の照会結果で恒久的な判断（退役等）をしない。**

**H. `auto_adjust` の既定値に依存している（潜在リスク）**
`data_collection/fetcher.py` の `yf.download()` は `auto_adjust` を明示していない。
yfinance 1.3.0 の既定は `True` だが、**バージョンで変わりうる**。
変わると DB 全体の価格基準が黙って切り替わる。**明示指定すべき。**

**I. `fetch_daily_data` の `df.ffill()` が欠損を捏造する（潜在リスク）**
取得直後に無条件で `df.ffill()` する。実測:
```
ffill 前   Open=NaN High=NaN Low=NaN Close=NaN Volume=NaN
ffill 後   Open=100 High=101 Low=99  Close=100.5 Volume=1000  ← 出来高まで前日値で埋まる
```
現状 `^VIX3M` の欠損日が DB に入っていないのは **yfinance 側が全 NaN 行を落としているため**で、
こちらの防御が効いているわけではない。**一部の列だけ NaN で返った場合は捏造される。**

**J. 銘柄レコードの再作成による系列切断** → §3

### 為替（`JPY=X` / `fx_rates`）固有

**土日のバーは存在しない。** 為替は日曜 17:00 ET 〜 金曜 17:00 ET の連続取引で、日足バーは月〜金のみ。
Yahoo は「当日・部分バー」を返すため、**土曜に日次パイプラインを実行すると土曜行が入る**
（2026-08-01 に 157.40 が混入し、金曜終値 160.18 と 1.7% 乖離）。

株価の T2 は `row['date'] <= spy_latest_date` で当日バーが自動的に落ちるが、
**`fx_rates` にはこの上限が無かった**のが原因。判定は `indicators/fx_calendar.py` に一元化済み。

**米国の祝日は除外しない。** 為替は米国休場日でも動くため、株式の取引日カレンダーと一致させてはいけない。

---

## 7. やってはいけないこと

**完全再構築で上書きしない。**
`run/tool/refresh_All.bat` の Parquet クリーンは、上流から取り直す前に手元の原本を捨てる操作。
上流が系列を切っていた銘柄は**永久に失われる**。17銘柄の8年分がこれで消えた（2026-08-02）。
現在は削除ではなく退避する（`backend/scripts/archive_parquet_master.py`）が、
**再構築は冪等な安全操作ではなく、上流品質に対して不可逆**であることを忘れないこと。

**取得できた行数が既存を下回るとき、黙って上書きしない。**
「取得できなかった」が「データが無い」として保存されると、正しいデータが失われる。

**1回の照会結果で恒久的な判断をしない**（限界G）。

**代替値・proxy で欠損を埋めない**（限界C）。ffill の方が誤差が小さいことが実測で確認されている。

---

## 8. 診断用スニペット

```python
import json, urllib.request, datetime as dt
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

def probe(tk):
    """全期間の日足と meta を取る。range=max は月足に落ちるので period1 を明示する。"""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}"
           f"?period1=0&period2=9999999999&interval=1d")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) as r:
            p = json.load(r)
    except urllib.error.HTTPError as e:
        return {"status": f"HTTP{e.code}"}      # 404 = 存在しない / 429 = レート制限
    res = (p.get("chart") or {}).get("result")
    if not res:
        return {"status": "no-result"}
    r0, meta = res[0], res[0].get("meta", {})
    ts = r0.get("timestamp") or []
    cl = (r0.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    valid = [(t, c) for t, c in zip(ts, cl) if c is not None]
    f = lambda x: str(dt.datetime.fromtimestamp(x, dt.timezone.utc).date())
    return {
        "status": "ok",
        "rows": len(valid),                      # 有効行数
        "nulls": len(ts) - len(valid),           # null 行（限界C の部分障害）
        "first": f(valid[0][0]) if valid else None,
        "name": meta.get("shortName"),
        "tz": meta.get("exchangeTimezoneName"),  # 日付変換に必ず使う（限界F）
        "firstTradeDate": f(meta["firstTradeDate"]) if meta.get("firstTradeDate") else None,
        "w52": (meta.get("fiftyTwoWeekLow"), meta.get("fiftyTwoWeekHigh")),  # §3 の判定用
    }
```

調査用の使い捨てスクリプトは `tmp/` に置く（`backend/scripts/` には置かない）。

---

## 関連

- `backend/scripts/retire_stale_symbols.py` — 退役判定（供給側検証つき）
- `backend/scripts/restore_truncated_symbol_history.py` — 旧世代からの履歴復元
- `backend/scripts/archive_parquet_master.py` — 再構築前の退避
- `backend/scripts/rename_symbol.py` — 改称の反映
- `backend/scripts/weekly_maintenance.py` — 鮮度監査・アノマリー検出
- `backend/indicators/fx_calendar.py` — 為替の営業日判定
- `.claude/skills/pipeline-debugging/SKILL.md` — T1〜T5 の不整合診断（手元側の問題）
- `.claude/skills/parquet-data-quality/SKILL.md` — Parquet の読み書き・世代管理
