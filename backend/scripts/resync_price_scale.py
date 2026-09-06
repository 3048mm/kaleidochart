"""上流が全履歴を再調整したのに手元の既存行が古いスケールのまま残った状態を直す。

## `adjust_symbol_split.py` との違い（取り違えると悪化する）

| | `adjust_symbol_split.py` | 本スクリプト |
|---|---|---|
| 上流の状態 | **調整していない**（生値のジャンプが残る） | **調整済み**（上流が正しい） |
| 係数の出どころ | 人間が申告（`--factor`） | **上流の実測**（行ごと） |
| 使う場面 | 上流が直してくれない | 上流は直っているのに手元が追随していない |

上流が壊れている銘柄に本スクリプトを当てると**壊れた値を取り込む**ので、
切り分けは `.claude/skills/upstream-data-diagnosis/SKILL.md` §2 で先に行うこと。

## いつ要るか（2026-09-04 の実例）

価格履歴の充足（`backfill_price_history.py`）で 2017年分を上流から取り直したところ、
2018-04-02 の接合部に段差が出た。全期間で手元/上流の比を測ると:

```
=== APH  照合 2428 行 ===
  2017-01-03 〜 2018-03-29  (  312行)  手元/上流 = 1.0000   ← 充足した分（正しい）
  2018-04-02 〜 2026-09-01  ( 2116行)  手元/上流 = 2.0000   ← 既存分（古いスケール）
```

分割日に上流は**全履歴を再調整する**が、こちらのマージは
`drop_duplicates(keep='last')` で**行を書き換えない**ため既存行が取り残される。
充足前は全体が同じ倍率だったので段差として観測できず、正しいスケールの
2017年分が入って初めて露出した。MNST(0.5) / RUSHA(1.5) / VISN(1.7541) /
IEP(1.0723) も同じ形で、うち MNST・RUSHA は途中に正常行が混ざるため
**単一の factor では表現できない**（＝`adjust_symbol_split.py` の接合部検算に通らない）。

## やること

  1. 上流から全期間を取り直し、日付ごとに 上流/手元 の比を測る
  2. 比が `--tolerance` を超える行だけ OHLC を `×比`、volume を逆方向に補正
     （`market_cap` は触らない。分割で時価総額は変わらない）
  3. **事後条件を検算** — 補正後に上流と一致しない行が残っていたら書かずに落ちる
  4. 所属する仮想テーマを全期間再合成する
  5. Parquet の新世代を書き出してポインタを更新
  6. `virtual_theme_hashes.json` から対象テーマを落とす

T3 / T4 / SQLite ホットキャッシュへの反映は**行わない**。直後に
`update_pipeline.py --rebuild-from T3` を回す前提で、そちらが Parquet 基準で
T3・T4 を作り直し、`run_production_restore()` で SQLite を再構築する。

> [!IMPORTANT]
> **単独で使うなら T3 以降を必ず別途回すこと。** 価格だけ直して放置すると
> 指標が古いスケールのまま残り、SQLite にも伝播しない。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\resync_price_scale.py \\
        --tickers MNST,APH,RUSHA,VISN,IEP --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\resync_price_scale.py \\
        --tickers MNST,APH,RUSHA,VISN,IEP --apply
"""

import argparse
import os
import sys

import pandas as pd

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
for _p in (_project_root, _backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tomli  # noqa: E402
import yfinance as yf  # noqa: E402

from data_collection.tls_trust import ensure_ca_bundle  # noqa: E402
from pipeline.parquet_cache_manager import (  # noqa: E402
    get_latest_master_files,
    get_parquet_master_dir,
    get_pointer_file_path,
)
from pipeline.parquet_maintenance import (  # noqa: E402
    assert_ticker as _assert_ticker,
    connect_hot_cache as _connect,
    drop_virtual_theme_hashes,
    resolve_db_path,
    write_master_generation,
)
from pipeline.parquet_recompute import (  # noqa: E402
    find_affected_virtual_themes,
    rebuild_virtual_index_prices,
)
from scripts.scan_price_anomalies import use_utf8_stdout  # noqa: E402

_PRICE_COLUMNS = ("open", "high", "low", "close")

# この比率までのズレは当日の値動き・端数として許容し、補正しない。
DEFAULT_TOLERANCE = 0.02

# 補正後にこの割合以上の行が上流と一致しなければ中断する。
REQUIRED_MATCH_RATIO = 0.99

# 上流と重なる行がこれ未満なら、比較材料が足りないので中断する。
MIN_OVERLAP_ROWS = 200

VIRTUAL_THEME_HASH_FILE = os.path.join("data", "virtual_theme_hashes.json")


class ResyncError(RuntimeError):
    """検算に失敗した。**迂回できるようにしないこと。**"""


def fetch_upstream(ticker: str, start: str) -> pd.DataFrame:
    """上流の全期間を取り直す。

    `auto_adjust` は**既定（True）のまま**にする。`fetcher.py` が既定で取得して
    いるので、ここで `False` にすると分割・配当調整の有無が食い違い、接合部に
    系統的な段差を作る（2026-09-04 に実際に作り込み、409銘柄が ±25%超になった）。
    """
    raw = yf.download(ticker, start=start, progress=False, threads=False)
    if raw is None or raw.empty:
        raise ResyncError(
            f"{ticker}: 上流が空を返した。yfinance はレート制限(429)も "
            f"`possibly delisted` として握り潰すので、時間を置いて試すこと。")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    out = raw.reset_index()
    out["date"] = pd.to_datetime(out["Date"]).dt.strftime("%Y-%m-%d")
    return out[["date", "Close"]].rename(columns={"Close": "up_close"})


def measure_factors(ours: pd.DataFrame, upstream: pd.DataFrame,
                    tolerance: float) -> pd.DataFrame:
    """日付ごとの補正係数 `上流/手元` を返す。

    許容範囲内の行は係数 1.0（＝補正しない）。上流に無い日付も 1.0 にして
    **触らない** — 上流の欠測を理由に手元の行を消したり歪めたりしない。
    """
    m = ours[["date", "close"]].merge(upstream, on="date", how="left")
    m["matched"] = m["up_close"].notna() & (m["close"] > 0) & (m["up_close"] > 0)

    ratio = pd.Series(1.0, index=m.index)
    ratio[m["matched"]] = m.loc[m["matched"], "up_close"] / m.loc[m["matched"], "close"]

    m["corrected"] = m["matched"] & ((ratio - 1.0).abs() > tolerance)
    m["factor"] = ratio.where(m["corrected"], 1.0)
    return m[["date", "factor", "matched", "corrected", "up_close"]]


def apply_factors(prices: pd.DataFrame, symbol_id: int,
                  factors: pd.DataFrame) -> pd.DataFrame:
    """OHLC を `×factor`、volume を `÷factor` する。`market_cap` は触らない。"""
    out = prices.copy()
    mask = out["symbol_id"] == symbol_id
    f = out.loc[mask, "date"].map(
        factors.set_index("date")["factor"]).fillna(1.0).astype(float)
    for col in _PRICE_COLUMNS:
        if col in out.columns:
            out.loc[mask, col] = out.loc[mask, col] * f
    if "volume" in out.columns:
        out.loc[mask, "volume"] = out.loc[mask, "volume"] / f
    return out


def verify_after(prices: pd.DataFrame, symbol_id: int, upstream: pd.DataFrame,
                 ticker: str, tolerance: float) -> dict:
    """補正後に上流と一致しているかを検算する。**唯一の歯止め。**"""
    ours = prices[prices["symbol_id"] == symbol_id][["date", "close"]]
    m = ours.merge(upstream, on="date", how="inner")
    m = m[(m["close"] > 0) & (m["up_close"] > 0)]
    if len(m) < MIN_OVERLAP_ROWS:
        raise ResyncError(
            f"{ticker}: 上流と重なる行が {len(m)} 行しかない"
            f"（最低 {MIN_OVERLAP_ROWS} 行）。照合材料が足りないので中断する。")
    off = int(((m["up_close"] / m["close"] - 1.0).abs() > tolerance).sum())
    match_ratio = 1.0 - off / len(m)
    if match_ratio < REQUIRED_MATCH_RATIO:
        raise ResyncError(
            f"{ticker}: 補正後もまだ {off}/{len(m)} 行が上流と一致しない"
            f"（一致率 {match_ratio:.2%} < {REQUIRED_MATCH_RATIO:.0%}）。"
            f"上流側が壊れている可能性があるので upstream-data-diagnosis §2 で"
            f"切り分けること。")
    return {"rows": len(m), "off": off, "match_ratio": match_ratio}


def run(tickers: list[str], start: str, tolerance: float, dry_run: bool,
        db_path: str | None = None) -> None:
    ensure_ca_bundle()

    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    db_path = resolve_db_path(config, db_path)
    parquet_dir = get_parquet_master_dir(db_path)
    print(f"[環境] SQLite  : {os.path.abspath(db_path)}")
    print(f"[環境] Parquet : {os.path.abspath(parquet_dir)}")

    pointer_file = get_pointer_file_path(parquet_dir)
    cur = get_latest_master_files(pointer_file)
    if not cur:
        raise ResyncError("latest_master.json を解決できません。")

    sym = pd.read_parquet(cur["symbols"])
    px = pd.read_parquet(cur["prices"])
    px["date"] = pd.to_datetime(px["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    print("=" * 74)
    print(f"価格スケールの上流再同期  {len(tickers)}銘柄  (dry-run={dry_run})")
    print("=" * 74)

    sids: list[int] = []
    new_px = px
    for ticker in tickers:
        hit = sym[sym["ticker"] == ticker]
        if hit.empty:
            raise ResyncError(f"{ticker} が symbols にありません。")
        sid = int(hit["id"].iloc[0])

        # **書き込む前に id 体系の一致を確認する。** 全期間再構築は `symbols.id` を
        # 再採番するため、照合せずに進めると別銘柄を壊す（2026-08-06 に 2,378件が変化）。
        con = _connect(db_path)
        try:
            _assert_ticker(con, sid, ticker)
        finally:
            con.close()

        ours = new_px[new_px["symbol_id"] == sid][["date", "close"]].sort_values("date")
        up = fetch_upstream(ticker, start)
        f = measure_factors(ours, up, tolerance)
        n_corr = int(f["corrected"].sum())
        n_unmatched = int((~f["matched"]).sum())
        med = float(f.loc[f["corrected"], "factor"].median()) if n_corr else 1.0

        print(f"\n[{ticker}] symbol_id={sid}  手元 {len(ours):,}行 "
              f"{ours['date'].min()} 〜 {ours['date'].max()}")
        print(f"    補正対象 {n_corr:,}行  中央値 ×{med:.4f}  "
              f"／ 上流に無い日付 {n_unmatched:,}行（触らない）")

        if not n_corr:
            print("    補正不要（既に上流と一致）")
            continue

        new_px = apply_factors(new_px, sid, f)
        v = verify_after(new_px, sid, up, ticker, tolerance)
        print(f"    検算: {v['rows']:,}行中 不一致 {v['off']}行 "
              f"（一致率 {v['match_ratio']:.2%}）→ OK")
        sids.append(sid)

    if not sids:
        print("\n補正対象がありません。何も書いていません。")
        return

    tc = pd.read_parquet(cur["tc"])
    theme_ids = find_affected_virtual_themes(sids, tc, sym)
    if theme_ids:
        names = sym[sym["id"].isin(theme_ids)]
        print(f"\n[+] 再合成が必要な仮想テーマ {len(theme_ids)}件")
        for _, t in names.iterrows():
            print(f"    {t['id']:>5}  {t['ticker']:<12} {t.get('name', '')}")

    if dry_run:
        print("\n[DRY-RUN] 書き込んでいません。")
        return

    if theme_ids:
        rebuilt = rebuild_virtual_index_prices(theme_ids, new_px, tc)
        keep = new_px[~new_px["symbol_id"].isin(theme_ids)]
        rebuilt = rebuilt.reindex(columns=new_px.columns)
        new_px = pd.concat([keep, rebuilt], ignore_index=True)
        new_px = new_px.sort_values(["symbol_id", "date"]).reset_index(drop=True)
        print(f"    仮想テーマ {len(theme_ids)}件を再合成 → {len(rebuilt):,}行")

    # 行数が減っていたら書かない（マージ事故の検出。2026-09-01 に rotate の OOM で
    # 指標が 610万行 → 159万行に切り詰められた前例がある）
    if len(new_px) < len(px):
        raise ResyncError(
            f"補正後の行数が減っている（{len(px):,} → {len(new_px):,}）。"
            f"書き込みを中止する。")

    files = write_master_generation(parquet_dir, pointer_file, cur,
                                    {"prices": new_px}, label="resync_price_scale")
    print(f"\n[+] 新世代を公開: {os.path.basename(files['prices'])}")

    if theme_ids:
        n = drop_virtual_theme_hashes(
            os.path.join(_project_root, VIRTUAL_THEME_HASH_FILE), theme_ids, dry_run)
        print(f"[+] virtual_theme_hashes.json から {n}件を削除")

    print("\n" + "=" * 74)
    print("価格のみ修正した。**T3 以降は未反映。**")
    print("  .\\venv\\Scripts\\python.exe backend\\scripts\\update_pipeline.py"
          " --rebuild-from T3 --skip-fetch")
    print("=" * 74)


if __name__ == "__main__":
    use_utf8_stdout()
    p = argparse.ArgumentParser(
        description="上流が再調整した価格に手元の既存行を追随させる")
    p.add_argument("--tickers", required=True, help="対象ティッカー（カンマ区切り）")
    p.add_argument("--start", default="2016-01-01", help="上流を取り直す開始日")
    p.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                   help=f"補正しないズレの上限（相対、既定 {DEFAULT_TOLERANCE}）")
    p.add_argument("--db-path", default=None, help="SQLite を明示指定")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    g.add_argument("--apply", action="store_true", help="実際に書き込む")
    a = p.parse_args()
    run([t.strip() for t in a.tickers.split(",") if t.strip()],
        a.start, a.tolerance, a.dry_run, a.db_path)
