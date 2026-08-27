"""SEC マスタと universe.db の差分から IPO 候補を検知し、`ipo_candidates` に登録する。

## 流れ

    [1] SEC company_tickers.json / company_tickers_mf.json を取得（2リクエスト）
    [2] universe.db から除外集合を引く
    [3] cik 未知の CIK について普通株を1本選抜  → 約 4,000 件
    [4] Yahoo chart API で上場日・取引所・株式種別を確定（スロットル付き）
    [5] 通過分に企業概要（`.info`）を付与して upsert

判定ロジックは `data_collection/ipo_discovery.py`（**通信しない純粋関数**）に置き、
本スクリプトは通信と DB 書き込みだけを持つ。

## なぜ Yahoo を1件ずつ叩くのか

`v7/finance/quote` なら一括で取れるが crumb / cookie 認証が要り未検証。
chart API は認証不要で、実測 440 件を 0.25s 間隔（4 req/s）で完走できた
（404 は 36 件でティッカー不在＝正常）。初回約 4,000 件でも 17 分程度。

## レビュー済みの判断は上書きしない

`status` が `pending` 以外の行（accepted / rejected / auto_excluded）は
**人間の判断**なので、再スキャンで `pending` に戻してはいけない。
`upsert_candidates()` がこれを守る。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\scan_ipo_candidates.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\scan_ipo_candidates.py --apply
    # 初回ブートストラップを分割実行したいとき
    .\\venv\\Scripts\\python.exe backend\\scripts\\scan_ipo_candidates.py --apply --limit 500
    # Yahoo に絞られたときの縮退（CIK は連番なので新しい登録主体ほど大きい）
    .\\venv\\Scripts\\python.exe backend\\scripts\\scan_ipo_candidates.py --apply --cik-floor 1900000

詳細: `doc/in_progress/ipo_candidates_plan.md` §3.1 / §3.4
"""

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli  # noqa: E402

from data_collection.ipo_discovery import (  # noqa: E402
    confirm_with_quote,
    detect_ipo_candidates,
    parse_chart_meta,
)
from data_collection.sec_client import SecClient  # noqa: E402
from db.database_universe import (  # noqa: E402
    get_universe_write_db,
    init_universe_db,
)
from db.models_universe import IpoCandidate  # noqa: E402

_UA = {"User-Agent": "Mozilla/5.0"}
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=5d&interval=1d"

DEFAULT_SINCE = "2026-04-01"
DEFAULT_RATE_PER_SEC = 4


class NotFound(Exception):
    """HTTP 404。ティッカーが Yahoo に存在しない。

    **レート制限(429)と明確に区別する。** yfinance が 404 と 429 を同じ
    メッセージに畳んでいたせいで誤診した経験があるため（`sec_client.py` 参照）。
    404 は候補から静かに落として良いが、429 は絞られている合図で対応が違う。
    """


class RateLimiter:
    """最小間隔を空ける。Yahoo は SEC のような公開上限が無いので自主的に絞る。"""

    def __init__(self, per_sec: int = DEFAULT_RATE_PER_SEC,
                 now=time.monotonic, sleep=time.sleep):
        self._interval = 1.0 / max(per_sec, 1)
        self._now = now
        self._sleep = sleep
        self._last = None

    def acquire(self) -> None:
        if self._last is not None:
            wait = self._interval - (self._now() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._now()


def _default_opener(url: str) -> str:
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=_UA), timeout=30) as r:
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotFound() from e
        raise


def probe_quote(ticker: str, opener=_default_opener, stats: dict | None = None):
    """Yahoo chart API を1件引いて正規化した辞書を返す。

    **例外でスキャン全体を落とさない。** 4,000 件のうち1件の通信エラーで
    全部やり直しになるのは割に合わない。ただし黙って握り潰さず `stats` に数える。
    """
    try:
        body = opener(CHART_URL.format(t=urllib.parse.quote(ticker)))
    except NotFound:
        if stats is not None:
            stats["not_found"] = stats.get("not_found", 0) + 1
        return None
    except Exception:  # noqa: BLE001 — 1件の失敗で全体を止めない
        if stats is not None:
            stats["error"] = stats.get("error", 0) + 1
        return None

    try:
        return parse_chart_meta(json.loads(body))
    except Exception:  # noqa: BLE001
        if stats is not None:
            stats["error"] = stats.get("error", 0) + 1
        return None


def decide_status(flags: set) -> tuple[str, str | None]:
    """フラグから `status` と理由を決める。

    **除外しても行は残す。** SPAC は合併後に実業会社へ社名変更するため、
    捨てると de-SPAC で生まれた実業会社を永久に取り逃がす（計画書 §2.2）。
    """
    if "spac" in flags:
        return "auto_excluded", "SPAC（ユニット構造または社名から判定）"
    if "fund" in flags:
        return "auto_excluded", "ETF・信託（社名から判定）"
    return "pending", None


def upsert_candidates(db, rows) -> int:
    """候補を upsert する。**レビュー済みの判断は上書きしない。**

    Returns:
        新規に挿入した件数。
    """
    inserted = 0
    for r in rows:
        flags = r.get("flags") or set()
        existing = db.query(IpoCandidate).filter_by(
            ticker=r["ticker"], exchange=r.get("exchange")).one_or_none()

        if existing is None:
            db.add(IpoCandidate(
                ticker=r["ticker"], exchange=r.get("exchange"),
                name=r.get("name"), cik=r.get("cik"),
                first_trade_date=r.get("first_trade_date"),
                market_cap=r.get("market_cap"), avg_volume=r.get("avg_volume"),
                last_price=r.get("last_price"),
                sector=r.get("sector"), industry=r.get("industry"),
                summary=r.get("summary"), website=r.get("website"),
                flags=",".join(sorted(flags)) or None,
                status=r.get("status", "pending"),
                status_note=r.get("status_note"),
            ))
            inserted += 1
            continue

        if existing.status != "pending":
            # accepted / rejected / auto_excluded は人間の判断。触らない
            continue

        # 未レビューなら最新のスナップショットを見せる
        existing.name = r.get("name") or existing.name
        existing.first_trade_date = r.get("first_trade_date") or existing.first_trade_date
        existing.market_cap = r.get("market_cap", existing.market_cap)
        existing.avg_volume = r.get("avg_volume", existing.avg_volume)
        existing.last_price = r.get("last_price", existing.last_price)
        for f in ("sector", "industry", "summary", "website"):
            if r.get(f):
                setattr(existing, f, r[f])
    return inserted


def load_exclusion_sets(db) -> tuple[set, set, set]:
    """universe.db から除外集合を引く。

    `symbols_master` は **`active` に関係なく全件**除外する
    （自分で退役させた 36 件を候補として再浮上させないため）。
    """
    from sqlalchemy import text

    known_tickers = {r[0].upper() for r in
                     db.execute(text("SELECT ticker FROM symbols_master")) if r[0]}
    known_ciks = {r[0] for r in db.execute(
        text("SELECT cik FROM symbols_master WHERE cik IS NOT NULL"))}
    old_tickers = {r[0].upper() for r in
                   db.execute(text("SELECT old_ticker FROM ticker_history")) if r[0]}
    return known_tickers, known_ciks, old_tickers


def fetch_profile(ticker: str) -> dict:
    """企業概要を取得する。テーマのタグ付け判断の材料。

    `.info` は重く不安定なので**フィルタを通過した候補にだけ**呼ぶ。
    失敗しても候補行は残す（各項目は NULL 可）。
    """
    try:
        from data_collection.fetcher import fetch_fundamentals
        info = (fetch_fundamentals(ticker) or {}).get("info") or {}
    except Exception:  # noqa: BLE001 — 概要が無くても候補としては成立する
        return {}
    return {
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "summary": info.get("longBusinessSummary"),
        "website": info.get("website"),
        "market_cap": info.get("marketCap"),
        "avg_volume": info.get("averageVolume"),
    }


def load_config() -> dict:
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    ipo = config.get("ipo_scan") or {}
    udb = config["system"].get(
        "universe_db_path",
        os.path.join(os.path.dirname(config["system"]["db_path"]), "universe.db"))
    return {
        "universe_db_path": udb,
        "since": ipo.get("since", DEFAULT_SINCE),
        "rate_per_sec": ipo.get("yahoo_rate_per_sec", DEFAULT_RATE_PER_SEC),
        "cik_floor": ipo.get("cik_floor", 0),
    }


def run(dry_run: bool, limit: int | None = None, cik_floor: int | None = None,
        skip_profile: bool = False) -> dict:
    conf = load_config()
    since = conf["since"]
    floor = conf["cik_floor"] if cik_floor is None else cik_floor

    print("=" * 74)
    print(f"IPO 候補スキャン  (dry-run={dry_run})")
    print("=" * 74)
    print(f"対象 DB : {conf['universe_db_path']}")
    print(f"基準日  : {since} 以降に上場したものを候補とする")
    if floor:
        print(f"CIK 下限: {floor:,}（縮退モード）")

    client = SecClient(project_root=_project_root)
    print(f"\n[1] SEC マスタ取得中... (連絡先: {client.contact})")
    d = client._get_json(
        "https://www.sec.gov/files/company_tickers.json")
    mf = client.company_tickers_mf()
    ct, titles = {}, {}
    for v in d.values():
        t = (v.get("ticker") or "").upper()
        if not t:
            continue
        cik = int(v["cik_str"])
        ct[t] = cik
        titles[cik] = v.get("title", "")
    print(f"    company_tickers {len(ct):,} / company_tickers_mf {len(mf):,}")

    init_universe_db(conf["universe_db_path"])
    with get_universe_write_db() as db:
        known_tickers, known_ciks, old_tickers = load_exclusion_sets(db)
    print(f"\n[2] 除外集合: ticker {len(known_tickers):,} / "
          f"cik {len(known_ciks):,} / 旧ticker {len(old_tickers)}")

    cands = detect_ipo_candidates(
        ct, titles, known_tickers, known_ciks, old_tickers, set(mf))
    if floor:
        cands = [c for c in cands if c.cik >= floor]
    if limit:
        cands = cands[:limit]
    print(f"\n[3] Yahoo に問い合わせる候補: {len(cands):,} 件")

    rl = RateLimiter(per_sec=conf["rate_per_sec"])
    stats = {"probed": 0, "passed": 0}
    reasons: dict[str, int] = {}
    rows = []
    for i, c in enumerate(cands, 1):
        rl.acquire()
        q = probe_quote(c.ticker, stats=stats)
        stats["probed"] += 1
        if q is None:
            reasons["no_quote"] = reasons.get("no_quote", 0) + 1
        else:
            ok, why = confirm_with_quote(q, since=since)
            if not ok:
                reasons[why] = reasons.get(why, 0) + 1
            else:
                stats["passed"] += 1
                status, note = decide_status(c.flags)
                rows.append({
                    "ticker": c.ticker, "cik": c.cik,
                    "exchange": q["fullExchangeName"],
                    "name": q.get("name") or c.name,
                    "first_trade_date": q["first_trade_date"],
                    "last_price": q.get("last_price"),
                    "avg_volume": q.get("volume"),
                    "flags": c.flags, "status": status, "status_note": note,
                })
        if i % 250 == 0 or i == len(cands):
            print(f"    {i:,}/{len(cands):,}  通過 {stats['passed']}")

    print(f"\n[4] 確定: 通過 {stats['passed']} / 探索 {stats['probed']:,}")
    print(f"    落ちた理由: {dict(sorted(reasons.items(), key=lambda x: -x[1]))}")

    n_pending = sum(1 for r in rows if r["status"] == "pending")
    print(f"    pending {n_pending} / auto_excluded {len(rows) - n_pending}")

    if dry_run:
        print("\n[dry-run] DB には書き込みません。上位20件:")
        for r in sorted(rows, key=lambda x: x["first_trade_date"] or "",
                        reverse=True)[:20]:
            fl = ",".join(sorted(r["flags"])) or "-"
            print(f"    {r['ticker']:<7}{r['first_trade_date']} "
                  f"{r['exchange']:<14}{fl:<10}{(r['name'] or '')[:34]}")
        return {"candidates": len(cands), "passed": stats["passed"],
                "pending": n_pending, "dry_run": True}

    if not skip_profile:
        targets = [r for r in rows if r["status"] == "pending"]
        print(f"\n[5] 企業概要を取得中... ({len(targets)} 件)")
        for i, r in enumerate(targets, 1):
            r.update(fetch_profile(r["ticker"]))
            if i % 10 == 0 or i == len(targets):
                print(f"    {i}/{len(targets)}")

    # ユーザー資産なのでバックアップを取る
    udb = conf["universe_db_path"]
    if os.path.exists(udb):
        bk = f"{udb}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(udb, bk)
        print(f"\nバックアップ: {os.path.basename(bk)}")

    with get_universe_write_db() as db:
        inserted = upsert_candidates(db, rows)
        db.commit()
        total_pending = db.query(IpoCandidate).filter_by(status="pending").count()
    print(f"\n[OK] 新規 {inserted} 件 / 未レビュー合計 {total_pending} 件")
    return {"candidates": len(cands), "passed": stats["passed"],
            "inserted": inserted, "pending_total": total_pending}


def main():
    ap = argparse.ArgumentParser(description="IPO 候補を検知して universe.db に登録する")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="DB に書かずに結果だけ表示")
    g.add_argument("--apply", action="store_true", help="実際に登録する")
    ap.add_argument("--limit", type=int, help="Yahoo に問い合わせる件数の上限（分割実行用）")
    ap.add_argument("--cik-floor", type=int,
                    help="この CIK 未満を対象外にする（縮退用。CIK は連番）")
    ap.add_argument("--skip-profile", action="store_true",
                    help="企業概要の取得を省く（高速化）")
    args = ap.parse_args()
    run(dry_run=args.dry_run, limit=args.limit, cik_floor=args.cik_floor,
        skip_profile=args.skip_profile)


if __name__ == "__main__":
    main()
