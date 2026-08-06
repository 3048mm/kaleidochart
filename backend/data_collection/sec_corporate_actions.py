"""SEC マスタと universe.db を突合し、改称・上場廃止を検知する純粋ロジック。

## 中核のアイデア

各銘柄の submissions を引くと3,000リクエストになる。
**マスタファイルの差分なら3リクエストで全銘柄をスクリーニングできる。**

    [1] SEC マスタ3ファイルを取得（3リクエスト）
    [2] universe.db の (sec_key, ticker) と突合
          ├─ 同じキーが別 ticker になっている  → 改称候補
          ├─ キーがマスタから消えた            → 廃止候補
          └─ 一致                             → 正常
    [3] 候補だけ submissions API で確定（通常 0〜数十件）

「キーがマスタから消えた＝廃止」が成立する根拠: 実測で `BLD` は
`company_tickers.json` から削除済みだったが、`submissions` は `tickers=['BLD']` を
返し続けていた。**マスタは登録抹消で除去され、submissions には残る。**

## ここは I/O を持たない

ネットワークも DB も触らない。呼び出し側（`scripts/sync_sec_corporate_actions.py`）が
取得した辞書を渡す。判定ロジックを実測ケースでテスト固定するため。

詳細: `doc/in_progress/sec_ticker_tracking_plan.md` §3.3 / §3.4
"""

from __future__ import annotations

import datetime as dt

# 登録抹消（Form 15 系）と上場廃止（Form 25 系）。
#   15-12B / 15-12G / 15-15D … 登録の抹消
#   25 / 25-NSE               … 取引所からの上場廃止通知
#
# **提出があるだけでは廃止の根拠にならない。** Form 25 は取引所の移管でも出る
# （`AEP` の NYSE→Nasdaq 2023-08-14、`UUP` の 2008-11-21）。
# 下の2つのしきい値と併用すること。
DEREGISTRATION_FORMS = {"15-12B", "15-12G", "15-15D", "25", "25-NSE"}

# 抹消系の提出がこれより古ければ根拠として採用しない。
# `UUP` は2008年の Form 25 を持ったまま2026年も 10-Q を出している現役 ETF。
DEREGISTRATION_MAX_AGE_DAYS = 400

# 定期報告。登録が本当に抹消されればこれらは止まる。
# 抹消系の提出より後にこれがあれば、移管など別の理由だったと分かる。
PERIODIC_REPORT_FORMS = {"10-K", "10-Q", "20-F", "40-F"}

# 「まだ生きている」の判定に使う提出。定期報告に加えて外国発行体の 6-K・臨時報告も見る。
ACTIVE_FILING_FORMS = PERIODIC_REPORT_FORMS | {"6-K", "8-K"}

# 直近この日数以内に上記の提出があれば活動中とみなす。
# 四半期報告の間隔（約90日）＋ 提出遅延の余裕。
ACTIVE_FILING_MAX_AGE_DAYS = 200

# 改称を自動適用する条件②のしきい値。新ティッカーがこの行数を持たなければ適用しない。
# `tools/db_health_check.py` の LOW_HISTORY_ROWS と同じ値（「実質空」の基準を揃える）。
MIN_NEW_TICKER_ROWS = 20

# 条件③のしきい値。旧ティッカーの最終取引日が新ティッカーより**この日数以上古い**なら
# 「死んだ」とみなす。行数で見ると改称直後（旧が数日前まで動いていた）を
# 「まだ生きている」と誤判定する — `GAMB`→`GRSD` を実測で取りこぼした。
#
#   GAMB  最終 2026-07-29（直近1ヶ月6行）  GRSD  最終 2026-08-05  → 7日差＝改称
#   VWDRY 最終 2026-08-05（ADR）          VWSYF 最終 2026-08-04  → 併存（別証券）
OLD_TICKER_STALE_DAYS = 5


def sec_key(cik, sec_class_id) -> tuple[str, str] | None:
    """突合キーを選ぶ。`class_id` があればそれ、無ければ `cik`、両方無ければ対象外。

    **ETF は classId で追う。** CIK はトラスト単位で粗すぎるため
    （`RSHO` の CIK 1944285 は Tema ETF Trust の13ファンドを含む）。
    """
    if sec_class_id:
        return ("class", str(sec_class_id))
    if cik:
        return ("cik", str(int(cik)))
    return None


def preferred_ticker(tickers) -> str | None:
    """複数ティッカーから普通株を選ぶ。

    優先株・ワラントは `BNY-PK` `ABC-WT` のようにサフィックスが付く。
    普通株（`-` を含まない最短のもの）を優先する。
    """
    if not tickers:
        return None
    plain = sorted(t for t in tickers if "-" not in t)
    return plain[0] if plain else sorted(tickers)[0]


def build_master_index(ct: dict, mf: dict) -> dict[tuple[str, str], set[str]]:
    """SEC マスタを「キー → ティッカー集合」に変換する。

    **1つの CIK に複数ティッカーがぶら下がる。** 実測: Bank of New York Mellon
    （CIK 1390777）は `BNY`（普通株）と `BNY-PK`（優先株）の2行を持つ。
    1対1の辞書にすると後勝ちで `BNY-PK` になり、普通株を「消えた」と誤判定する。

    Args:
        ct: `company_tickers.json` 由来の ticker → cik
        mf: `company_tickers_mf.json` 由来の symbol → (cik, seriesId, classId)
    """
    idx: dict[tuple[str, str], set[str]] = {}
    for ticker, cik in ct.items():
        idx.setdefault(("cik", str(int(cik))), set()).add(ticker.upper())
    for symbol, (cik, _series, class_id) in mf.items():
        if class_id:
            idx.setdefault(("class", str(class_id)), set()).add(symbol.upper())
        if cik:
            idx.setdefault(("cik", str(int(cik))), set()).add(symbol.upper())
    return idx


def detect_candidates(symbols, master_index: dict) -> list[dict]:
    """universe.db の銘柄をマスタと突合し、候補に仕分ける。

    Args:
        symbols: `ticker` / `cik` / `sec_class_id` / `active` を持つ辞書の列。
        master_index: `build_master_index()` の結果。

    Returns:
        `status` が ``ok`` / ``rename_candidate`` / ``delist_candidate`` /
        ``untracked`` のいずれかである辞書のリスト。
        **active=0 の銘柄は返さない**（退役済みを毎週レポートに出し続けないため）。
    """
    out = []
    for s in symbols:
        if not s.get("active"):
            continue
        ticker = (s.get("ticker") or "").strip()
        key = sec_key(s.get("cik"), s.get("sec_class_id"))
        row = {"ticker": ticker, "key": key, "new_ticker": None}

        if key is None:
            row["status"] = "untracked"
        elif key not in master_index:
            # マスタから消えた＝登録抹消。submissions には残るのでここでしか分からない
            row["status"] = "delist_candidate"
        elif ticker.upper() in master_index[key]:
            row["status"] = "ok"
        else:
            row["status"] = "rename_candidate"
            row["new_ticker"] = preferred_ticker(master_index[key])
        out.append(row)
    return out


def _recent_filings(submissions: dict | None) -> list[tuple[str, str]]:
    if not submissions:
        return []
    recent = ((submissions.get("filings") or {}).get("recent") or {})
    return [(f, d) for f, d in zip(recent.get("form") or [],
                                   recent.get("filingDate") or []) if f and d]


def _age_days(date_str: str, today: dt.date) -> int:
    return (today - dt.date.fromisoformat(date_str)).days


def find_deregistration(submissions: dict | None, today: dt.date | None = None) -> dict | None:
    """**現に有効な**登録抹消・上場廃止の提出を探す。

    提出があるだけでは根拠にしない。Form 25 は取引所の移管でも提出されるため、
    2つの条件を課す:

        ① 提出から `DEREGISTRATION_MAX_AGE_DAYS` 以内であること
           → `UUP`（2008年の Form 25 を持つ現役 ETF）を弾く
        ② それより後に定期報告（10-K/10-Q/20-F/40-F）が出ていないこと
           → `AEP`（2023年に NYSE→Nasdaq 移管、以後も 10-Q を提出）を弾く

    どちらも実測で誤検知を起こしたケースに対応する。

    Returns:
        `{"form": ..., "date": ...}`。該当なしは ``None``。複数あれば最も新しいもの。
    """
    today = today or dt.date.today()
    filings = _recent_filings(submissions)
    hits = [{"form": f, "date": d} for f, d in filings if f in DEREGISTRATION_FORMS]
    if not hits:
        return None
    latest = max(hits, key=lambda h: h["date"])

    if _age_days(latest["date"], today) > DEREGISTRATION_MAX_AGE_DAYS:
        return None
    if any(f in PERIODIC_REPORT_FORMS and d > latest["date"] for f, d in filings):
        return None
    return latest


def is_still_filing(submissions: dict | None, today: dt.date | None = None) -> bool:
    """直近に定期・臨時報告があるか（＝実体がまだ活動しているか）。

    `company_tickers.json` は 10,398件しかなく**全登録企業を網羅していない**。
    「マスタに載っていない＝廃止」としてしまうと、`AEP` `UUP` のような現役銘柄を
    毎週退役候補に挙げ続けることになる。これを分離するための判定。
    """
    today = today or dt.date.today()
    return any(f in ACTIVE_FILING_FORMS and _age_days(d, today) <= ACTIVE_FILING_MAX_AGE_DAYS
               for f, d in _recent_filings(submissions))


def classify_candidate(candidate: dict, submissions: dict | None,
                       today: dt.date | None = None) -> dict:
    """候補を submissions で確定させる。

    判定の優先順位は **退役 > 改称 > マスタ欠落 > 不明**。

    買収では改称と登録抹消が同時に立つ（`BLD`: `tickers=['QXO']` かつ Form 15-12G）。
    その証券自体は消えるので**退役が正しい**。改称として付け替えると
    存在しないティッカーを追い続けることになる。

    Returns:
        `action` が以下のいずれかである辞書。

        ``retire``      … 登録抹消・上場廃止が確定（自動適用してよい）
        ``rename``      … 改称。`evidence_strength` が ``confirmed`` か ``master_only``
        ``master_gap``  … 一括マスタの取りこぼし。実体は活動中（対応不要）
        ``unknown``     … 根拠なし。**推測せず**人間に回す
                          （`RSHO` / `CORZZ` のように Yahoo 側の問題のことがある）
    """
    out = dict(candidate)
    dereg = find_deregistration(submissions, today)
    if dereg:
        out["action"] = "retire"
        out["evidence"] = f"Form {dereg['form']} ({dereg['date']})"
        return out

    current = [t.upper() for t in ((submissions or {}).get("tickers") or []) if t]

    if out.get("status") == "rename_candidate" and out.get("new_ticker"):
        if current and out["ticker"].upper() not in current:
            # submissions も改称を裏付けている
            out["new_ticker"] = preferred_ticker(set(current))
            out["evidence_strength"] = "confirmed"
            out["evidence"] = f"SEC tickers={current} name={(submissions or {}).get('name')}"
        else:
            # **submissions の `tickers` は遅れる。** `GAMB`→`GRSD` は社名が
            # GRANDSTAND Ltd に変わり一括マスタも更新済みなのに、submissions は
            # `tickers=['GAMB']` を返し続けていた。弱い根拠として残し、
            # 付け替えの可否はガード3条件（`evaluate_rename_guard`）に委ねる。
            out["evidence_strength"] = "master_only"
            out["evidence"] = (f"一括マスタのみ（submissions は tickers={current} で未更新）"
                               f" name={(submissions or {}).get('name')}")
        out["action"] = "rename"
        return out

    if is_still_filing(submissions, today):
        out["action"] = "master_gap"
        out["evidence"] = "一括マスタに未収載だが定期報告は継続中（実体は活動中）"
        return out

    out["action"] = "unknown"
    out["evidence"] = "SEC 上に改称・登録抹消の根拠なし（Yahoo 側の問題の可能性）"
    return out


def is_old_ticker_dead(old_last_date: str | None, new_last_date: str | None) -> bool:
    """旧ティッカーの取引が止まっているか。

    **絶対日付ではなく新ティッカーとの差で見る。** 「今日から何日前か」で判定すると
    連休・祝日・取得タイミングでぶれるが、同じ市場の2銘柄を比べればその影響が消える。
    """
    if not old_last_date:
        return True                      # 応答なし・データなし＝死んでいる
    if not new_last_date:
        return False                     # 比較相手が無い。安全側に倒す
    lag = (dt.date.fromisoformat(new_last_date) - dt.date.fromisoformat(old_last_date)).days
    return lag >= OLD_TICKER_STALE_DAYS


def evaluate_rename_guard(key_matched: bool, new_ticker_rows: int,
                          old_last_date: str | None,
                          new_last_date: str | None) -> tuple[bool, list[str]]:
    """改称を自動適用してよいかを判定する（3条件すべて必須）。

    誤って付け替えると**以後ずっと別銘柄を追い続ける**ため、ガードは厳しくする。

        ① SEC キー（cik / class_id）が一致すること
        ② 新ティッカーが実際に履歴を持つこと      ← 空振りへの付け替え事故を防ぐ
        ③ 旧ティッカーの取引が止まっていること

    ②を入れる理由: `FLZH` 1,687行・`SHOE` 8,404行を確認してから改称した実績がある。
    ③を入れる理由: SEC は優先株・ADR を同じ CIK に載せるため、キー一致だけでは
    付け替えの根拠にならない。旧ティッカーがまだ動いているなら別物の可能性が高い。

    Returns:
        (適用可否, 満たさなかった条件の説明リスト)。
        **満たさない条件はすべて返す** — 人間が全体像を掴めるように。
    """
    reasons = []
    if not key_matched:
        reasons.append("① SEC キーが一致しない")
    if new_ticker_rows < MIN_NEW_TICKER_ROWS:
        reasons.append(
            f"② 新ティッカーの履歴が不足（{new_ticker_rows}行 < {MIN_NEW_TICKER_ROWS}行）")
    if not is_old_ticker_dead(old_last_date, new_last_date):
        reasons.append(
            f"③ 旧ティッカーがまだ取引されている（旧 {old_last_date} / 新 {new_last_date}）")
    return (not reasons), reasons


def evaluate_retire_guard(theme_child_count: int) -> tuple[bool, list[str]]:
    """退役を自動適用してよいかを判定する。

    退役自体は根拠が硬く可逆（`active=1` に戻せば価格履歴ごと復元できる）なので
    原則そのまま適用してよい。**ただしテーマの親は別。** 親を落とすと
    構成銘柄の紐付けが宙に浮き、テーマの合成値が壊れる。影響が本人に閉じないため
    人間の判断に回す（`retire_stale_symbols.py` の保護と揃えている）。

    Returns:
        (適用可否, 満たさなかった条件の説明リスト)
    """
    if theme_child_count > 0:
        return False, [f"テーマの親（構成銘柄 {theme_child_count} 件）のため要手動判断"]
    return True, []


def resolve_rename_outcome(guard_ok: bool, new_ticker_rows: int,
                           old_last_date: str | None, new_last_date: str | None) -> str:
    """ガードの結果を「適用 / 併存 / 要判断」に振り分ける。

    **旧も新も取引されているなら改称ではない。** `VWDRY`（ADR）と `VWSYF`（原株）は
    Vestas Wind Systems（CIK 1330306）の別々の証券で、SEC は同じ CIK に載せる。
    これを要判断リストに毎週載せても人間には何もできないので、情報として分離する。

    Returns:
        ``rename``（自動適用可） / ``coexisting``（同一 CIK の別証券・対応不要） /
        ``pending``（人間の判断が要る）
    """
    if guard_ok:
        return "rename"
    if (new_ticker_rows >= MIN_NEW_TICKER_ROWS
            and not is_old_ticker_dead(old_last_date, new_last_date)):
        return "coexisting"
    return "pending"
