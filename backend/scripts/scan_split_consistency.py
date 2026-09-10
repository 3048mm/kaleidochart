"""上流の分割調整が自己矛盾していないかを検査する（**報告のみ・修正しない**）。

## なぜ必要か（2026-09-04 の事故）

価格履歴の充足で接合部に段差が出た5銘柄を「上流が正・手元が古い」と判断して
上流へ合わせたところ、**MNST は上流側が壊れており、正しかった手元データを壊した**。
誤った価格の上に T3/T4 全期間再計算とデイリー更新まで走らせ、ユーザーが
TradingView で実測して指摘するまで気づけなかった。

既存の検査は3つとも素通りした:

| 検査 | 素通りした理由 |
|---|---|
| `resync_price_scale.py` の `verify_after()` | 「上流と一致するか」しか見ない。**上流の誤りは原理的に検出不能** |
| `scan_price_anomalies.py` | 系列内の段差を見る。全期間が一律にズレていると**段差が出ない** |
| `adjust_symbol_split.py` の接合部検算 | 申告 `--factor` との照合。申告値自体は検証しない |

MNST は yfinance が分割記録を**持っていた**（2026-08-11 ×2）のに、調整済み系列へ
適用していなかった。つまり**同じ上流の中で metadata と時系列が矛盾していた**。
これは外部ソース無しで検出できる。

## 2つの検査

**検査A（`detect_unapplied_split`）**: 上流が記録している分割日に、上流自身の
調整済み終値が段差を残していないか。遡及適用済みなら段差は出ない。
段差が `1/factor` と一致すれば「知っているのに適用できていない」。

**検査B（`compare_scale`）**: 手元と上流の比が 1.0 から乖離していないか。
比が分割比に一致すれば `split_suspect`、一致しないが乖離が大きければ `divergent`。
当プロジェクトは `auto_adjust=True` で配当調整も入るため、**配当銘柄の緩やかな
乖離は正常**。閾値を配当の有無で変える。

## やらないこと

> [!IMPORTANT]
> **自動修正しない。どちらが正しいかも決めない。**
> 2026-09-04 の事故は「自動的に上流へ合わせた」ことが原因だった。
> 両方の値と判断材料を並べ、**判断は人間に残す**。

## 検出できない範囲（`upstream-data-diagnosis` §6 限界1）

**上流が分割記録すら持っていない銘柄は原理的に検出できない。** 検査Aは
「記録された分割日」を起点にするため。`SOXS` / `UAVS` など299件が該当する。
そこを塞ぐには外部の一次情報（moomoo 等）が要る（第3段階・別計画）。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\scan_split_consistency.py --years 2
    .\\venv\\Scripts\\python.exe backend\\scripts\\scan_split_consistency.py --tickers MNST,APH
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
for _p in (_project_root, _backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tomli  # noqa: E402
import yfinance as yf  # noqa: E402

from data_collection.split_records import (  # noqa: E402
    resolve_default_path,
    save_split_records,
)
from data_collection.tls_trust import ensure_ca_bundle  # noqa: E402
from indicators.price_anomaly import (  # noqa: E402,F401  （再エクスポート）
    MIN_DISCRIMINABLE_GAP,
    SPLIT_JUMP_TOLERANCE,
)
from pipeline.parquet_cache_manager import (  # noqa: E402
    get_latest_master_files,
    get_parquet_master_dir,
    get_pointer_file_path,
)
from scripts.scan_price_anomalies import use_utf8_stdout  # noqa: E402

# --- 検査A ---------------------------------------------------------------
# 段差の許容幅と、識別可能性の下限は `indicators/price_anomaly.py` に集約した
# （価格アノマリーの分類器が同じ判定をするため。**同じ閾値を2箇所に書かない**）。
#
#   SPLIT_JUMP_TOLERANCE  … 段差が `1/factor` からどれだけ外れてよいか
#   MIN_DISCRIMINABLE_GAP … 株式配当（factor ≈ 1.0x）は段差では識別できない。
#                           2026-09-09 の全ユニバース実測で、検出18件のうち
#                           16件がこの誤検出だった
#
# 再エクスポートしているのは、既存のテスト・呼び出しが
# `scripts.scan_split_consistency` 側の名前を参照しているため。

# --- 検査B ---------------------------------------------------------------
# よくある分割比。手元/上流の比がこれらに一致したら分割の取りこぼしを疑う。
COMMON_SPLIT_RATIOS = (
    0.1, 0.125, 0.2, 0.25, 1 / 3, 0.4, 0.5, 2 / 3, 0.75,
    1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 8.0, 10.0,
)
SPLIT_RATIO_TOLERANCE = 0.02

# 「乖離あり」とみなす閾値。配当調整は年数に比例して効くので、配当銘柄は緩くする。
# IEP は比 1.07 で正常（分配金で説明可能）だったため、無配当側もそこまでは許容しない。
DIVERGENCE_THRESHOLD_NO_DIV = 0.05
DIVERGENCE_THRESHOLD_WITH_DIV = 0.30

# 判定に必要な最小の重複行数。これ未満は unknown（0 や 1.0 に丸めない）。
MIN_OVERLAP_ROWS = 3

DEFAULT_YEARS = 2
BATCH_SIZE = 100
SLEEP_SEC = 1.0

# 仮想テーマは構成銘柄から合成するもので yfinance から取得しない
EXCLUDED_CATEGORIES = ("テーマ",)


def is_split_like_ratio(ratio: float,
                        tolerance: float = SPLIT_RATIO_TOLERANCE) -> bool:
    """比が「よくある分割比」に一致するか。

    配当調整ぶんの乖離（数%〜十数%）を分割と誤認しないことが目的。
    """
    if ratio is None or ratio <= 0:
        return False
    return any(abs(ratio - r) <= r * tolerance for r in COMMON_SPLIT_RATIOS)


def is_within_our_data(split_date: str, our_first: str | None,
                       our_last: str | None) -> bool:
    """分割日が手元の保有期間に入っているか。

    保有期間の外にある分割は上流の古い履歴の問題であって、**こちらのデータには
    影響しない**。報告し続けると対応不能な警告が常態化し、レポートが読まれなくなる。

    2026-09-10 の全期間スキャンで `CHT`（2008-10-15 ×1.21）と
    `FWONA`（2016-04-18 ×1.424）が該当した。手元は 2017-01-03 以降しか持たない。

    Note:
        **保有期間が分からないときはスキップしない。** 見落としより誤検出を選ぶ。
    """
    if not split_date or not our_first or not our_last:
        return True
    return our_first <= split_date <= our_last


def detect_unapplied_split(closes: pd.DataFrame, split_date: str,
                           factor: float,
                           tolerance: float = SPLIT_JUMP_TOLERANCE) -> dict:
    """検査A: 上流が記録した分割を、上流自身が適用できているかを判定する。

    Args:
        closes: 上流の `date` / `close`（調整済み終値）。
        split_date: 上流が記録している分割日（`YYYY-MM-DD`）。
        factor: 分割比（2:1 なら 2.0、1:10 併合なら 0.1）。

    Returns:
        `{"verdict": "applied"|"unapplied"|"unknown", "jump": float|None, ...}`

    Note:
        **判定不能を「正常」に丸めない。** 分割日が直近すぎてデータが無いことが
        実際にある（`APH` の 2026-09-03 がそうだった）。ここで例外を投げると
        スキャン全体が止まるので、`unknown` を返して先へ進む。
    """
    out = {"verdict": "unknown", "jump": None, "split_date": split_date,
           "factor": factor}
    if closes is None or closes.empty or not factor or factor <= 0:
        return out

    df = closes.dropna(subset=["close"]).sort_values("date")
    before = df[df["date"] < split_date]["close"]
    after = df[df["date"] >= split_date]["close"]
    if before.empty or after.empty:
        return out

    prev, nxt = float(before.iloc[-1]), float(after.iloc[0])
    if prev <= 0 or nxt <= 0:
        return out

    jump = nxt / prev
    out["jump"] = jump
    expected = 1.0 / factor          # 未適用なら分割比のぶん落ちる（併合なら上がる）

    # **2つの仮説が識別できないなら判定しない。** 株式配当（factor ≈ 1.0x）では
    # 「未適用（jump≈1/factor）」と「適用済み（jump≈1.0）」がほぼ同じ値になる。
    if abs(expected - 1.0) < MIN_DISCRIMINABLE_GAP:
        out["reason"] = f"factor={factor} は段差で識別できない（株式配当の可能性）"
        return out

    if abs(jump - expected) <= expected * tolerance:
        out["verdict"] = "unapplied"
    elif abs(jump - 1.0) <= 0.15:
        out["verdict"] = "applied"
    return out


def compare_scale(mine: pd.DataFrame, upstream: pd.DataFrame,
                  has_dividend: bool) -> dict:
    """検査B: 手元と上流のスケールが食い違っていないかを判定する。

    Args:
        mine / upstream: `date` / `close`。
        has_dividend: 対象期間に配当・分配金があるか。閾値を変えるのに使う。

    Returns:
        `{"verdict": "ok"|"split_suspect"|"divergent"|"unknown", "ratio": ...}`

    Note:
        **どちらが正しいかは判定しない。** 2026-09-04 にそれを誤って MNST を
        壊した。比と判断材料を返すだけにする。

        **「分割比ではない」を理由に `ok` へ丸めない。** `VISN` は比 1.7541 で
        分割比に一致しなかったが、実際に手元が古かった（2026年の特別分配
        $10.00 / $5.00 が効いていた）。分割比への不一致は正常の証明ではない。
    """
    out = {"verdict": "unknown", "ratio": None, "rows": 0,
           "has_dividend": has_dividend}
    if mine is None or upstream is None or mine.empty or upstream.empty:
        return out

    m = mine[["date", "close"]].merge(
        upstream[["date", "close"]].rename(columns={"close": "up"}), on="date")
    m = m[(m["close"] > 0) & (m["up"] > 0)]
    out["rows"] = len(m)
    if len(m) < MIN_OVERLAP_ROWS:
        return out

    ratio = float((m["close"] / m["up"]).median())
    out["ratio"] = ratio

    if is_split_like_ratio(ratio):
        out["verdict"] = "split_suspect"
        return out

    threshold = (DIVERGENCE_THRESHOLD_WITH_DIV if has_dividend
                 else DIVERGENCE_THRESHOLD_NO_DIV)
    out["verdict"] = "ok" if abs(ratio - 1.0) <= threshold else "divergent"
    return out


# --- 取得と実行 -----------------------------------------------------------

def resolve_db_path() -> str:
    """`config.toml` の `system.db_path` を返す。

    > [!IMPORTANT]
    > **相対パス（`"data/stocktool.db"`）を直書きしない。**
    > ワークツリーから実行するとカレントディレクトリ基準で解決され、
    > ワークツリー自身の空の `data/` を見にいって
    > 「`latest_master.json` を解決できません」で止まる（2026-09-10 実測）。
    > `config.toml` は本番の絶対パスを持つので、`scan_price_anomalies.py` と
    > 同じ導出にそろえる。
    """
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        return tomli.load(f)["system"]["db_path"]


def load_targets(tickers: list[str] | None) -> pd.DataFrame:
    """検査対象の銘柄を Parquet マスタから取る（仮想テーマは除外）。"""
    pointer = get_pointer_file_path(get_parquet_master_dir(resolve_db_path()))
    cur = get_latest_master_files(pointer)
    if not cur:
        raise RuntimeError("latest_master.json を解決できません。")
    sym = pd.read_parquet(cur["symbols"])
    keep = sym["active"] == 1 if "active" in sym.columns else pd.Series(
        True, index=sym.index)
    if "category" in sym.columns:
        keep &= ~sym["category"].isin(EXCLUDED_CATEGORIES)
    out = sym[keep][["id", "ticker"]].copy()
    if tickers:
        out = out[out["ticker"].isin(tickers)]
    return out.reset_index(drop=True), cur


def fetch_batch(tickers: list[str], start: str) -> dict:
    """価格と分割イベントを1回の呼び出しで取る（銘柄ごとに叩かない）。"""
    raw = yf.download(tickers=tickers, start=start, progress=False, threads=False,
                      actions=True, group_by="ticker", auto_adjust=True)
    out = {}
    if raw is None or raw.empty:
        return out
    for t in tickers:
        try:
            df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = df.reset_index()
        if "Date" not in df.columns:
            continue
        df["date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
        rec = {"closes": df[["date", "Close"]].rename(
            columns={"Close": "close"}).dropna()}
        rec["splits"] = ([(r["date"], float(r["Stock Splits"]))
                          for _, r in df.iterrows()
                          if r.get("Stock Splits", 0) not in (0, None)
                          and not pd.isna(r.get("Stock Splits"))]
                         if "Stock Splits" in df.columns else [])
        rec["has_dividend"] = bool(
            "Dividends" in df.columns and (df["Dividends"].fillna(0) > 0).any())
        out[t] = rec
    return out


def run(tickers=None, years=DEFAULT_YEARS, report_dir=None,
        records_path=None) -> dict:
    ensure_ca_bundle()
    start = (datetime.now() - timedelta(days=int(365.25 * years))).strftime("%Y-%m-%d")

    targets, cur = load_targets(tickers)
    print("=" * 74)
    print(f"分割整合スキャン  {len(targets):,}銘柄 / {start} 以降 / years={years}")
    print("**報告のみ。データは一切変更しない。**")
    print("=" * 74)

    px = pd.read_parquet(cur["prices"], columns=["symbol_id", "date", "close"])
    px["date"] = px["date"].astype(str)
    px = px[px["date"] >= start]

    findings, checked, failed = [], 0, []
    skipped_out_of_range = []   # 保有期間外の分割。黙って落とさず件数を出す
    # 取得した分割記録は**検出の有無に関わらず全件残す**。
    # ここで捨てていたせいで、価格アノマリーの分類器が「その日に分割があったか」を
    # 知らないまま段差を market_wide / real_move に落としていた
    # （`doc/issue_list.md` P1・`data_collection/split_records.py` の docstring）。
    all_splits: dict[str, list] = {}
    names = targets["ticker"].tolist()
    for i in range(0, len(names), BATCH_SIZE):
        chunk = names[i:i + BATCH_SIZE]
        try:
            got = fetch_batch(chunk, start)
        except Exception as e:  # noqa: BLE001
            failed.extend(chunk)
            print(f"  [WARN] バッチ取得に失敗 ({chunk[0]}..): {str(e)[:80]}")
            continue

        for _, row in targets[targets["ticker"].isin(chunk)].iterrows():
            t, sid = row["ticker"], int(row["id"])
            rec = got.get(t)
            if not rec or rec["closes"].empty:
                failed.append(t)
                continue
            checked += 1
            if rec["splits"]:
                all_splits[t] = list(rec["splits"])

            mine = px[px["symbol_id"] == sid][["date", "close"]]
            our_first = mine["date"].min() if not mine.empty else None
            our_last = mine["date"].max() if not mine.empty else None

            # 検査A: 上流が記録した分割を、上流自身が適用できているか
            for sd, f in rec["splits"]:
                # 保有期間の外なら上流の古い履歴の問題。こちらは手を出せない
                if not is_within_our_data(sd, our_first, our_last):
                    skipped_out_of_range.append((t, sd, f))
                    continue
                a = detect_unapplied_split(rec["closes"], sd, f)
                if a["verdict"] == "unapplied":
                    findings.append({
                        "ticker": t, "symbol_id": sid, "check": "A_unapplied_split",
                        "split_date": sd, "factor": f, "jump": round(a["jump"], 4),
                        "detail": "上流は分割を記録しているが調整済み系列に適用していない",
                    })

            # 検査B: 手元と上流のスケール乖離
            b = compare_scale(mine, rec["closes"], rec["has_dividend"])
            if b["verdict"] in ("split_suspect", "divergent"):
                findings.append({
                    "ticker": t, "symbol_id": sid, "check": f"B_{b['verdict']}",
                    "split_date": "", "factor": "",
                    "jump": round(b["ratio"], 4) if b["ratio"] else "",
                    "detail": f"手元/上流={b['ratio']:.4f} 配当={rec['has_dividend']} "
                              f"照合{b['rows']}行",
                })

        print(f"  {min(i + BATCH_SIZE, len(names))}/{len(names)} "
              f"検査済み（検出 {len(findings)}件）")
        time.sleep(SLEEP_SEC)

    print("\n" + "=" * 74)
    print(f"検査 {checked:,}銘柄 / 検出 {len(findings)}件 / 取得失敗 {len(failed)}件")

    # 取得した分割記録を保存する（検出ゼロでも保存する。
    # 「分割が無かった」ことも価格アノマリーの分類に要る情報）
    records_path = save_split_records(
        records_path or resolve_default_path(resolve_db_path()), all_splits,
        start=start, years=years, tickers_fetched=checked, failed=failed)
    n_pairs = sum(len(v) for v in all_splits.values())
    print(f"\n  分割記録: {len(all_splits):,}銘柄 / {n_pairs:,}件 → {records_path}")
    print(f"    カバー期間: {start} 以降（これより前の分割は記録されていない）")

    # 黙って落とさない。何を見なかったかを必ず出す
    if skipped_out_of_range:
        print(f"\n  保有期間外のためスキップした分割: {len(skipped_out_of_range)}件"
              f"（上流の古い履歴の問題で、手元データには影響しない）")
        for t, sd, f in skipped_out_of_range[:10]:
            print(f"    {t:<8} {sd} ×{f}")
        if len(skipped_out_of_range) > 10:
            print(f"    …他 {len(skipped_out_of_range) - 10}件")
    if findings:
        df = pd.DataFrame(findings)
        print("\n  検査別の内訳:")
        for k, n in df["check"].value_counts().items():
            print(f"    {k:<22} {n}")
        print("\n  先頭20件:")
        print(df.head(20).to_string(index=False))

        report_dir = report_dir or os.path.join(
            _project_root, "data", "maintenance_reports")
        os.makedirs(report_dir, exist_ok=True)
        path = os.path.join(
            report_dir, f"split_consistency_{datetime.now():%Y%m%d_%H%M%S}.csv")
        df.to_csv(path, index=False, encoding="utf-8")
        print(f"\n  全件: {path}")
    else:
        print("  検出なし。")

    print("\n  ※ 修正は自動で行わない。個別に上流と独立ソースを突き合わせて判断すること")
    print("     手順: .claude/skills/upstream-data-diagnosis/SKILL.md §2 / §6")
    return {"checked": checked, "findings": findings, "failed": failed}


if __name__ == "__main__":
    use_utf8_stdout()
    p = argparse.ArgumentParser(
        description="上流の分割調整が自己矛盾していないかを検査する（報告のみ）")
    p.add_argument("--tickers", default=None, help="対象を絞る（カンマ区切り）")
    p.add_argument("--years", type=float, default=DEFAULT_YEARS,
                   help=f"遡る年数（既定 {DEFAULT_YEARS}）")
    p.add_argument("--records-out", default=None,
                   help="分割記録 JSON の出力先（既定は data/maintenance_reports/）")
    a = p.parse_args()
    res = run([t.strip() for t in a.tickers.split(",")] if a.tickers else None,
              a.years, records_path=a.records_out)
    print(json.dumps({"checked": res["checked"],
                      "findings": len(res["findings"])}, ensure_ascii=False))
