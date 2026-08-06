"""SEC EDGAR と universe.db を突合し、改称・上場廃止を検知して反映する。

## なぜ

2026年6〜7月に**15件**（改称6・上場廃止9）のコーポレートアクションを2ヶ月見逃した。
Yahoo は「データが少ない」としか言わないため**改称と上場廃止を区別できず**、
「上流のデータ不具合」と誤診して復旧に丸一日を費やした。

**CIK と classId は変わらない。** マスタ3ファイルの差分なら3リクエストで
全銘柄をスクリーニングできる（個別に submissions を引くと3,000リクエストになる）。

## 流れ

    [0] SEC キーが未解決の銘柄を解決して保存   ← 新規追加銘柄が追跡対象外に落ちるのを防ぐ
    [1] SEC マスタ3ファイルを取得（3リクエスト）
    [2] universe.db の (sec_key, ticker) と突合 → 改称候補 / 廃止候補
    [3] 候補だけ submissions API で確定（通常 0〜数十件）
    [4] 退役は自動適用。改称はガード3条件を満たせば自動適用、欠ければレポートのみ

## 自動適用の方針

**退役は自動適用する。** 根拠が硬く（Form 15-12G / 25-NSE の提出という事実）、
かつ**可逆**（`active=1` に戻せば価格履歴もそのまま残る）。

**改称はガード3条件をすべて満たしたときだけ自動適用する。**
誤って付け替えると以後ずっと別銘柄を追い続けるため（`sec_corporate_actions.py` 参照）。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\sync_sec_corporate_actions.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\sync_sec_corporate_actions.py --apply
    # 検知だけして適用しない（レポートは出る）
    .\\venv\\Scripts\\python.exe backend\\scripts\\sync_sec_corporate_actions.py --apply --no-auto-apply

詳細: `doc/in_progress/sec_ticker_tracking_plan.md`
"""

import argparse
import csv
import os
import shutil
import sys
from datetime import datetime

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli  # noqa: E402
from data_collection.sec_client import SecClient, SecNotFound  # noqa: E402
from data_collection.sec_corporate_actions import (  # noqa: E402
    build_master_index,
    classify_candidate,
    detect_candidates,
    evaluate_rename_guard,
    evaluate_retire_guard,
    resolve_rename_outcome,
    sec_key,
)
from db.database_universe import get_universe_write_db, init_universe_db  # noqa: E402
from db.models_universe import SymbolMaster, ThemeMember  # noqa: E402
from scripts.migrate_universe_sec_keys import is_sec_trackable  # noqa: E402
from scripts.rename_symbol import rename_one  # noqa: E402
from scripts.retire_stale_symbols import probe_supply  # noqa: E402

REPORT_DIR = os.path.join(_project_root, "data", "maintenance_reports")
PENDING_CSV = os.path.join(REPORT_DIR, "sec_pending_actions.csv")

# ガード条件②で新ティッカーの履歴を見る期間。1ヶ月では改称直後と空振りを区別できない。
NEW_TICKER_PROBE_RANGE = "2y"


def resolve_missing_keys(db, client, apply: bool) -> int:
    """SEC キーが未解決の銘柄を解決して保存する。

    **新規追加銘柄は放置すると永久に `cik` が NULL のまま＝追跡対象外**になる
    （計画書 §3.7 問題2）。同期のたびに拾い直す。

    一括マスタは全登録企業を網羅していない（`AEP` が典型）ため、
    残りは1件ずつ `browse-edgar` で引く。
    """
    ct, mf = client.company_tickers(), client.company_tickers_mf()
    targets = [s for s in db.query(SymbolMaster).filter(SymbolMaster.active == 1).all()
               if sec_key(s.cik, s.sec_class_id) is None and is_sec_trackable(s.ticker)]
    if not targets:
        return 0

    print(f"\n[0] SEC キー未解決 {len(targets)} 件を解決中...")
    now = datetime.utcnow()
    n = 0
    for s in targets:
        t = s.ticker.upper()
        if t in mf:
            cik, _series, class_id = mf[t]
        elif t in ct:
            cik, class_id = ct[t], None
        else:
            cik, class_id = client.lookup_cik_by_ticker(s.ticker), None
        if cik is None:
            continue
        if apply:
            s.cik, s.sec_class_id, s.sec_checked_at = cik, class_id, now
        n += 1
        print(f"    {s.ticker}: cik={cik} class_id={class_id}")
    print(f"    → {n} 件を解決" + ("" if apply else "（dry-run のため未保存）"))
    return n


def fetch_submissions(client, cand: dict, cik_by_ticker: dict) -> dict | None:
    """候補の submissions を引く。引けなければ None（＝確定させない）。"""
    cik = cik_by_ticker.get(cand["ticker"])
    if not cik:
        return None
    try:
        return client.submissions(cik)
    except SecNotFound:
        return None
    except Exception as e:  # noqa: BLE001 — 1件の失敗で同期全体を落とさない
        print(f"    [WARN] {cand['ticker']} の submissions 取得に失敗: {e}")
        return None


def apply_guard(cand: dict) -> dict:
    """改称候補に対してガード②③を Yahoo で検証する（①は検知時点で成立済み）。

    ③は**最終取引日を新旧で比較する**。1ヶ月の行数で見ると、改称直後に旧ティッカーへ
    残る数日分の残骸を「まだ生きている」と誤判定する（`GAMB`→`GRSD` で実測）。
    """
    new_status, new_rows, new_last = probe_supply(cand["new_ticker"], NEW_TICKER_PROBE_RANGE)
    old_status, old_rows, old_last = probe_supply(cand["ticker"], NEW_TICKER_PROBE_RANGE)
    ok, reasons = evaluate_rename_guard(
        key_matched=True, new_ticker_rows=new_rows,
        old_last_date=old_last, new_last_date=new_last)
    out = dict(cand)
    out.update({"guard_ok": ok, "guard_reasons": reasons,
                "outcome": resolve_rename_outcome(ok, new_rows, old_last, new_last),
                "new_ticker_rows": new_rows, "new_ticker_status": new_status,
                "new_last_date": new_last,
                "old_ticker_rows": old_rows, "old_ticker_status": old_status,
                "old_last_date": old_last})
    return out


def write_pending_csv(rows: list[dict], report_dir: str | None = None) -> str:
    """要判断の候補を CSV に出す。`rename_symbol.py --batch` にそのまま渡せる形式。

    出力先を差し替えられるのは、週次メンテが**監査対象 DB と同じ階層**に
    レポートを書くため（テスト実行が本番のレポートを壊した事故への対処）。
    """
    out_dir = report_dir or REPORT_DIR
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, os.path.basename(PENDING_CSV))
    cols = ["ticker", "new_ticker", "action", "evidence", "reason"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "reason": " / ".join(r.get("guard_reasons") or [])})
    print(f"\n→ {path}")
    return path


def run(apply: bool, auto_apply: bool = True, report_dir: str | None = None) -> dict:
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    udb_path = config["system"].get(
        "universe_db_path",
        os.path.join(os.path.dirname(config["system"]["db_path"]), "universe.db"))

    print("=" * 74)
    print(f"SEC コーポレートアクション同期  (apply={apply}, auto_apply={auto_apply})")
    print("=" * 74)

    # universe.db はユーザー資産（手動編集と改称履歴を持つ）。書き込む前に必ず退避する
    if apply:
        bk = f"{udb_path}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(udb_path, bk)
        print(f"バックアップ: {os.path.basename(bk)}")

    client = SecClient()
    init_universe_db(udb_path)
    summary = {"renamed": [], "retired": [], "pending": [], "unknown": [],
               "master_gap": [], "coexisting": [], "alive_tickers": []}

    with get_universe_write_db() as db:
        resolve_missing_keys(db, client, apply)

        ct, mf = client.company_tickers(), client.company_tickers_mf()
        print(f"\n[1] SEC マスタ: company_tickers {len(ct):,} / mf {len(mf):,}")
        index = build_master_index(ct, mf)

        symbols = db.query(SymbolMaster).all()
        cik_by_ticker = {s.ticker: s.cik for s in symbols}
        rows = [{"ticker": s.ticker, "cik": s.cik,
                 "sec_class_id": s.sec_class_id, "active": s.active} for s in symbols]

        found = detect_candidates(rows, index)
        counts = {}
        for r in found:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        print(f"[2] 突合結果: " + " / ".join(f"{k}={v}" for k, v in sorted(counts.items())))

        # SEC マスタに現ティッカーで載っている＝上場している、の確証。
        # 週次の鮮度監査が出す「上場廃止候補」と突き合わせるために持ち帰る
        # （`RSHO` のように SEC 上は健在なのに Yahoo の供給だけが止まる例がある）。
        summary["alive_tickers"] = [r["ticker"] for r in found if r["status"] == "ok"]

        candidates = [r for r in found
                      if r["status"] in ("rename_candidate", "delist_candidate")]
        confirmed = []
        if candidates:
            print(f"\n[3] 候補 {len(candidates)} 件を submissions で確定中...")
            for c in candidates:
                got = classify_candidate(c, fetch_submissions(client, c, cik_by_ticker))
                confirmed.append(got)
                print(f"    {got['ticker']:<8} {got['status']:<18} → {got['action']:<8}"
                      f" {got.get('new_ticker') or '':<8} {got.get('evidence', '')[:60]}")
        else:
            print("\n[3] 候補なし（変化なし）")

        # --- 適用 ---------------------------------------------------------
        print(f"\n[4] 適用")
        for c in confirmed:
            if c["action"] == "unknown":
                summary["unknown"].append(c)
                continue
            if c["action"] == "master_gap":
                # 一括マスタの取りこぼし。実体は活動中なので対応不要
                summary["master_gap"].append(c)
                continue

            if c["action"] == "retire":
                # テーマの親を落とすと構成が壊れるので自動では触らない
                # （`retire_stale_symbols.py` と同じ保護。あちらとロジックを揃えている）
                as_parent = db.query(ThemeMember).filter(
                    ThemeMember.theme_ticker == c["ticker"]).count()
                ok, reasons = evaluate_retire_guard(as_parent)
                if not ok:
                    c["guard_reasons"] = reasons
                    summary["pending"].append(c)
                    print(f"    [要判断] {c['ticker']}  {' / '.join(reasons)}  {c['evidence']}")
                    continue

                # 根拠が硬く可逆なので自動適用する
                summary["retired"].append(c)
                if apply and auto_apply:
                    sym = db.query(SymbolMaster).filter(
                        SymbolMaster.ticker == c["ticker"]).first()
                    if sym is not None:
                        sym.active = 0
                    print(f"    [退役] {c['ticker']}  {c['evidence']}")
                else:
                    print(f"    [退役(未適用)] {c['ticker']}  {c['evidence']}")
                continue

            # rename — ガード3条件を検証
            g = apply_guard(c)
            desc = (f"(新 {g['new_ticker_rows']}行 最終{g['new_last_date']}"
                    f" / 旧 {g['old_ticker_rows']}行 最終{g['old_last_date']})")
            if g["outcome"] == "coexisting":
                # 同一 CIK にぶら下がる別証券（ADR と原株など）。改称ではない
                summary["coexisting"].append(g)
                print(f"    [併存] {g['ticker']} / {g['new_ticker']} は同一 CIK の別証券 {desc}")
            elif g["outcome"] == "rename" and apply and auto_apply:
                rename_one(db, g["ticker"], g["new_ticker"],
                           f"SEC: {g['evidence']}", None, dry_run=False)
                summary["renamed"].append(g)
                print(f"    [改称] {g['ticker']} → {g['new_ticker']}  {desc}")
            elif g["outcome"] == "rename":
                summary["renamed"].append(g)
                print(f"    [改称(未適用)] {g['ticker']} → {g['new_ticker']}  {desc}")
            else:
                summary["pending"].append(g)
                print(f"    [要判断] {g['ticker']} → {g['new_ticker']}"
                      f"  {' / '.join(g['guard_reasons'])}  {desc}")

    pending = summary["pending"] + summary["unknown"]
    if pending:
        write_pending_csv(pending, report_dir)
    else:
        # 前回の候補が解消したのに古い CSV が残っていると、誤って適用する恐れがある
        stale = os.path.join(report_dir or REPORT_DIR, os.path.basename(PENDING_CSV))
        if os.path.exists(stale):
            os.remove(stale)
            print(f"\n要判断が解消したため古い {os.path.basename(stale)} を削除しました")

    print(f"\n=== 集計 ===")
    print(f"  退役 {len(summary['retired'])} / 改称 {len(summary['renamed'])}"
          f" / 要判断 {len(summary['pending'])} / 根拠なし {len(summary['unknown'])}")
    print(f"  （対応不要: マスタ欠落 {len(summary['master_gap'])}"
          f" / 同一CIKの別証券 {len(summary['coexisting'])}）")
    if not apply:
        print("\n[DRY-RUN] universe.db は変更していません。")
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="SEC EDGAR と universe.db を突合し改称・上場廃止を反映する")
    p.add_argument("--dry-run", action="store_true", help="検知のみ（変更しない）")
    p.add_argument("--apply", action="store_true", help="universe.db に反映する")
    p.add_argument("--no-auto-apply", action="store_true",
                   help="検知と保存はするが、退役・改称の自動適用はしない")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    run(apply=a.apply, auto_apply=not a.no_auto_apply)
