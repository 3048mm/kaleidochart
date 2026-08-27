"""SEC マスタと universe.db の差分から IPO 候補を拾う（純粋関数のみ）。

## なぜ

既存の SEC 週次同期（`sec_corporate_actions.py`）は universe.db → SEC の方向にしか
走査しない。だから「改称」「上場廃止」は検知できるが、**「新規上場」は構造的に検知できない**。
`universe.db` の個別銘柄は 2026-04 頃のスプレッドシート由来で、それ以降の IPO が入っていない。

本モジュールは逆方向（SEC → universe.db）に走査する。役割分担は次のとおり:

    cik 既知 / ticker 既知 … 既存銘柄。何もしない
    cik 既知 / ticker 未知 … 改称または新クラス上場 → `sec_corporate_actions` の担当
    cik 未知               … 新規発行体 → **本モジュールの担当**

この切り分けにより二重検知が起きない。

## SEC は株式クラスを教えてくれない

`company_tickers.json` は `{cik, ticker, title}` だけで、普通株・優先株・ワラント・
ユニットの区別が無い。**Yahoo も教えてくれない**（2026-08-27 実測）:

    SCAG   instrumentType=EQUITY  longName="Scage Future"            ← 普通株
    SCAGW  instrumentType=EQUITY  longName="Scage Future"            ← ワラントなのに同じ
    EURKU  instrumentType=EQUITY  longName="Eureka Acquisition Corp" ← ユニットなのに同じ

したがって**ティッカーの構造と兄弟関係から推定するしかない**。

## 上場日だけでは絞れない

    SCAG  （普通株）  上場日 2025-06-27
    SCAGW（ワラント）上場日 2026-07-17   ← 普通株より新しい
    EURKU（ユニット）上場日 2024-07-02   ← 普通株 EURK 2024-09-12 より古い

SPAC はユニットが先に上場して後から分離するため、日付の前後関係も当てにならない。
`since` フィルタの前に必ずティッカー構造で落とす。

詳細: `doc/in_progress/ipo_candidates_plan.md` §2.2 / §3.1
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 取引所は**完全一致**で判定する。
# `'NYSEArca'.startswith('NYSE')` は True になるため、前方一致で実装すると
# ETF・信託の取引所である NYSEArca が通ってしまう（検証中に
# `MSBT` Morgan Stanley Bitcoin Trust が実際に通過した）。
ALLOWED_EXCHANGES = frozenset({
    "NasdaqGS", "NasdaqGM", "NasdaqCM", "NYSE", "NYSE American",
})

# ADR・非スポンサード外国普通株。OTC でしか取引されず候補になりえない。
# 実測: `PITAF`/`PSTEY`（Poste Italiane）、`CYATY`/`CTATF`（CATL）など
_ADR_RE = re.compile(r"^[A-Z]{4,5}[YF]$")

# ユニット・ワラント・ライツ。
# **末尾1文字で見る。**SPAC は4文字の語幹でこれらを上場し、普通株だけ短縮するため
# 「ベース＋サフィックス」の完全一致では拾えない
#   JAB / JABRR / JABRU / JABRW      NCO / NCOOR / NCOOU / NCOOW
#   CAQ / CAQUU / CAQUW              GIX / GIXXR / GIXXU
_UWR_RE = re.compile(r"(?:-(?:UN|WT|RI|RT|WS)|[UWR])$")

# **ユニットだけを見分ける。**
# ユニット（普通株＋ワラントの抱き合わせ）は SPAC の IPO 時にしか存在せず、
# **合併が成立すると分離・消滅する**。したがって:
#
#     ユニットがある      … 現役 SPAC（まだ事業実体が無い）
#     ワラントだけ残る    … de-SPAC 済み。既に実業会社になっている
#
# 実測（2026-08-27）:
#     現役     EURK/EURKR/EURKU   CXII/CXIIU/CXIIW   MTNE/MTNE-UN/MTNE-WT
#     de-SPAC  SCAG/SCAGW   INV/INVLW   HPAI/HPAIW   FOXX/FOXXW   KWM/KWMWW
#
# ワラント兄弟だけで SPAC 扱いにすると、**de-SPAC で生まれた実業会社を
# 取り逃がす**（計画書 §2.2 が避けたかったことそのもの）。
_UNIT_RE = re.compile(r"(?:-UN|U)$")

# 社名からの SPAC 判定。実測で全 8,002 CIK 中 297 件、うち古い CIK での該当は
# `RDGA` `STQN` `T-REX` の3件だけ（いずれも実際にシェル企業）なので誤爆はほぼ無い
_SPAC_NAME_RE = re.compile(r"\b(?:acquisition|acquisitions|merger)\b", re.I)

# ETF は `company_tickers_mf.json` 側にあるはずだが、暗号資産・コモディティの信託は
# 10-K を出す事業会社扱いで `company_tickers.json` に載る（実測 `THYP` 21Shares ETF）。
# **`Trust` は入れない** — REIT の "Terra Property Trust" を巻き込むため
_FUND_NAME_RE = re.compile(r"\bETF\b", re.I)


@dataclass
class CikGroupResult:
    """1つの CIK 配下のティッカー群を仕分けた結果。"""

    ticker: str | None = None
    flags: set[str] = field(default_factory=set)
    excluded: bool = False
    exclude_reason: str | None = None


@dataclass
class IpoCandidate:
    """検知した候補1件（Yahoo 確定前）。"""

    ticker: str
    cik: int
    name: str
    flags: set[str] = field(default_factory=set)


def is_allowed_exchange(exchange: str | None) -> bool:
    """主要取引所か。**完全一致**で判定する（前方一致にしない理由はモジュール冒頭）。"""
    return (exchange or "") in ALLOWED_EXCHANGES


def is_non_common_line(base: str, ticker: str) -> bool:
    """`ticker` がユニット・ワラント・ライツの行か。

    `base` の派生であることを前提に、**末尾のサフィックスだけ**で判定する。
    ベースとの完全一致を要求すると SPAC の語幹伸長（`JAB` → `JABRU`）を取りこぼす。
    """
    if ticker == base:
        return False
    return bool(_UWR_RE.search(ticker))


def has_unit_sibling(base: str, siblings) -> bool:
    """兄弟にユニットがあるか＝現役 SPAC か（理由は `_UNIT_RE` のコメント）。

    `KWMWW` `GCLWW` のような二重 W のワラントをユニットと誤認しないこと。
    """
    return any(
        t != base and _UNIT_RE.search(t) for t in siblings)


def name_flags(name: str | None) -> set[str]:
    """社名から `spac` / `fund` フラグを立てる。

    **除外ではなくフラグ**。SPAC は合併後に実業会社へ社名変更するため
    （`Innventure` `Helport AI` `Foxx Development` が実例）、行を残して
    de-SPAC 後に拾い直せるようにする。
    """
    out: set[str] = set()
    n = name or ""
    if _SPAC_NAME_RE.search(n):
        out.add("spac")
    if _FUND_NAME_RE.search(n):
        out.add("fund")
    return out


def classify_cik_group(tickers, name: str | None) -> CikGroupResult:
    """1つの CIK 配下のティッカー群から普通株を1本選ぶ。

    手順（計画書 §3.1 [4]）:
        a. ADR・外国 OTC を落とす
        b. 残りが空 → 国内普通株が無い ⇒ CIK ごと除外（実測 873 件）
        c. 最短（同長なら辞書順）をベースとする
        d. ベース以外が全てユニット・ワラント・ライツ ⇒ ベースを普通株として採用。
           さらに**ユニットがあれば**現役 SPAC として `spac` フラグを立てる
        e. それ以外で2本以上残る ⇒ 複数クラス別上場 ⇒ CIK ごと除外（実測 231 件）

    e で除外する理由: 実測 231 件の中身は社債（`FG/FGN/FGSN`）・優先株
    （`PDCC/PDPA`）・ワラント（`HUBC/HUBCW/HUBCZ`）で、**GOOGL/GOOG 型の
    本物のデュアルクラスは1件も無い**。現代のデュアルクラス IPO は Class A だけを
    上場し Class B/C はティッカーを持たないため、単一ティッカーとして扱われる。
    さらに `CRBD`（社債）が `CRBG`（普通株）より辞書順で先に来るため、
    接頭辞ルールに任せると誤選択する。曖昧なら落とす方が精度が高い。
    """
    uniq = sorted(set(t.upper() for t in tickers))
    if not uniq:
        return CikGroupResult(excluded=True, exclude_reason="empty")

    core = [t for t in uniq if not _ADR_RE.match(t)]
    if not core:
        return CikGroupResult(excluded=True, exclude_reason="adr_only")

    base = sorted(core, key=lambda x: (len(x), x))[0]
    others = [t for t in core if t != base]

    flags = name_flags(name)
    if others:
        if not all(is_non_common_line(base, t) for t in others):
            return CikGroupResult(excluded=True, exclude_reason="multi_class")
        # ユニットを伴う＝**現役** SPAC。ワラントだけなら de-SPAC 済みなので
        # フラグを立てない（`_UNIT_RE` のコメント参照）
        if has_unit_sibling(base, others):
            flags.add("spac")

    return CikGroupResult(ticker=base, flags=flags)


def confirm_with_quote(quote: dict, since: str) -> tuple[bool, str | None]:
    """Yahoo の応答で候補を確定させる。

    Args:
        quote: `instrumentType` / `fullExchangeName` / `first_trade_date`(ISO) を持つ辞書
        since: 棚卸し済みの水位（この日以降に上場したものだけを候補とする）

    Returns:
        (通過したか, 落ちた理由)
    """
    if (quote.get("instrumentType") or "") != "EQUITY":
        return False, "instrument_type"
    if not is_allowed_exchange(quote.get("fullExchangeName")):
        return False, "exchange"
    ftd = quote.get("first_trade_date")
    if not ftd:
        return False, "no_first_trade_date"
    if str(ftd) < since:
        return False, "before_since"
    return True, None


def detect_ipo_candidates(
    company_tickers: dict,
    titles: dict,
    known_tickers: set,
    known_ciks: set,
    old_tickers: set,
    fund_symbols: set,
) -> list[IpoCandidate]:
    """SEC マスタから IPO 候補を組み立てる（**通信なし**）。

    Args:
        company_tickers: `company_tickers.json` 由来の ticker → cik
        titles: cik → 社名
        known_tickers: `symbols_master` の全ティッカー。
            **`active` に関係なく除外する** — 自分で退役させた 36 件を再浮上させない
        known_ciks: `symbols_master` の解決済み cik。
            既知なら改称・新クラス上場であり `sec_corporate_actions` の担当
        old_tickers: `ticker_history.old_ticker`。
            改称前ティッカーが SEC マスタに残っていると新規に見える
        fund_symbols: `company_tickers_mf.json` のシンボル（ファンド）
    """
    by_cik: dict[int, list[str]] = {}
    for ticker, cik in company_tickers.items():
        by_cik.setdefault(int(cik), []).append(ticker.upper())

    excluded_tickers = (
        {t.upper() for t in known_tickers}
        | {t.upper() for t in old_tickers}
        | {t.upper() for t in fund_symbols}
    )

    out: list[IpoCandidate] = []
    for cik, tickers in by_cik.items():
        if cik in known_ciks:
            continue
        name = titles.get(cik)
        r = classify_cik_group(tickers, name)
        if r.excluded or not r.ticker:
            continue
        if r.ticker in excluded_tickers:
            continue
        out.append(IpoCandidate(
            ticker=r.ticker, cik=cik, name=name or "", flags=set(r.flags)))
    return sorted(out, key=lambda c: c.ticker)
