"""moomoo API を独立ソースとして、yfinance側の分割記録と突き合わせる（第3段階）。

## なぜ必要か

`scan_split_consistency.py`（第1・2段階）は yfinance 自身の自己矛盾（記録はあるのに
未適用）と手元/上流のスケール乖離を検出できるが、**上流(yfinance)が分割記録
そのものを持たない銘柄は原理的に検出できない**（`SOXS`/`UAVS`等299件。
`.claude/skills/upstream-data-diagnosis/SKILL.md` §6 限界1）。

本スクリプトは moomoo API（`.claude/skills/moomoo-api/SKILL.md`）から分割記録
（`MoomooClient.get_rehab()`）を取得し、`data_collection/split_records.py` が
保存している yfinance側の記録と突き合わせる。**メタデータ（分割の日付・比率）
同士の突き合わせに限定し、価格の実際のジャンプとの比較（ノイズを含む）はしない**
（`doc/in_progress/moomoo_split_verification_plan.md` §2.2 の設計判断）。

## やらないこと

- **自動修正しない。報告のみ。**（`scan_split_consistency.py`と同じ方針。
  2026-09-04のMNST事故＝「上流一致を歯止めにした結果、壊れた上流に合わせて
  正しいデータを壊した」の教訓を踏まえる）
- **全銘柄バッチスキャンはしない。** `--tickers` で明示指定した銘柄だけを照合する
  （moomoo認証の手動性を踏まえ、週次自動化は時期尚早という判断）
- moomoo記録を恒久的にキャッシュしない（都度APIを叩く。CSVレポートのみ出力する）

## 比の変換仕様（実測で確認済み）

moomoo の `split_ratio` と yfinance の `Stock Splits`（本スクリプトでは `factor` と
呼ぶ）は、`moomoo_split_ratio == 1.0 / yfinance_factor` で一貫する
（forward/reverse split とも）。独立の実測は `AAPL`（4:1 forward split）と
`STKH`（1:3 reverse split）の2件。

Usage:
    $env:PYTHONPATH="backend"
    ..\\..\\..\\venv\\Scripts\\python.exe backend\\scripts\\verify_split_with_moomoo.py --tickers SOXS,STKH
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as _date
from datetime import datetime

import pandas as pd

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
for _p in (_project_root, _backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import paths  # noqa: E402
from data_collection.moomoo_client import (  # noqa: E402
    MoomooApiError,
    MoomooClient,
    MoomooNotConnectedError,
)
from data_collection.split_records import (  # noqa: E402
    load_split_records,
    split_map,
)
from indicators.price_anomaly import (  # noqa: E402
    MIN_DISCRIMINABLE_GAP,
    SPLIT_DATE_WINDOW_DAYS,
)
from scripts.scan_price_anomalies import use_utf8_stdout  # noqa: E402
from scripts.scan_split_consistency import SPLIT_RATIO_TOLERANCE  # noqa: E402

# --- 比の変換仕様 -------------------------------------------------------------


def expected_ratio_from_yfinance_factor(factor: float) -> float:
    """yfinance の `factor` を moomoo `split_ratio` と同じ意味の値へ変換する。

    実測（`.claude/skills/moomoo-api/SKILL.md` §3「比の変換仕様」）で
    `moomoo_split_ratio == 1.0 / yfinance_factor` が forward/reverse とも
    一貫することを確認済み。
    """
    return 1.0 / factor


def is_discriminable_ratio(ratio: float, gap: float = MIN_DISCRIMINABLE_GAP) -> bool:
    """比が1.0から十分離れており、株式配当（factor≈1.0）と区別できるか。

    `scan_split_consistency.py::detect_unapplied_split` と同じ考え方
    （同じ閾値 `MIN_DISCRIMINABLE_GAP` を再利用し、2箇所に別の値を書かない）。
    """
    return abs(ratio - 1.0) >= gap


def filter_discriminable_yfinance_events(
    events: list[tuple[str, float]], *, gap: float = MIN_DISCRIMINABLE_GAP,
) -> list[tuple[str, float]]:
    """yfinance側の記録から、株式配当のように識別不能な factor を除いておく。

    除かずに突き合わせると、これらは moomoo 側の実際の分割と対応づかず
    無意味な `yfinance_only` として報告され続ける（ノイズ）。
    """
    return [(d, f) for d, f in events
            if is_discriminable_ratio(expected_ratio_from_yfinance_factor(f), gap)]


# --- 突き合わせ判定ロジック ----------------------------------------------------


def _to_ordinal(date_str: str) -> int:
    return _date(int(date_str[0:4]), int(date_str[5:7]), int(date_str[8:10])).toordinal()


def match_split_events(
    moomoo_events: list[tuple[str, float]],
    yfinance_events: list[tuple[str, float]],
    *,
    date_window_days: int = SPLIT_DATE_WINDOW_DAYS,
    ratio_tolerance: float = SPLIT_RATIO_TOLERANCE,
) -> list[dict]:
    """moomoo側とyfinance側の分割記録を日付窓＋比率許容幅で突き合わせる。

    Args:
        moomoo_events: `[(ex_div_date, split_ratio), ...]`（`extract_moomoo_split_events` の出力）。
        yfinance_events: `[(date, factor), ...]`（呼び出し側で
            `filter_discriminable_yfinance_events` 済みであることを期待する）。
        date_window_days: 日付の対応づけに許容するズレ（既定は `price_anomaly.py` の
            `SPLIT_DATE_WINDOW_DAYS` を流用。`WLFC` の実測1日ズレの前例に対応できる幅）。
        ratio_tolerance: 比率の一致判定の許容幅（既定は `scan_split_consistency.py` の
            `SPLIT_RATIO_TOLERANCE`。メタデータ同士は本来ほぼ厳密に一致するはずなので
            タイトな値を流用する。価格ジャンプの判定に使う `SPLIT_JUMP_TOLERANCE` とは
            性質が異なるため混同しない — 詳細は計画書 §3「2種類の許容幅を混同しない」）。

    Returns:
        各要素は `{"verdict", "moomoo_date", "moomoo_ratio", "yfinance_date",
        "yfinance_factor", "yfinance_expected_ratio"}`。

        - ``"match"``          … 対応する記録があり、比率も一致
        - ``"ratio_mismatch"`` … 日付は対応するが比率が食い違う（要人間確認）
        - ``"moomoo_only"``    … moomooにのみ記録がある（**第3段階の主目的**）
        - ``"yfinance_only"``  … yfinanceにのみ記録がある（非対称の逆方向）

    Note:
        **どちらが正しいかは判定しない**（`compare_scale()` と同じ方針）。
        比率と判断材料を返すだけにする。
    """
    yf_remaining = list(yfinance_events)
    rows: list[dict] = []

    for m_date, m_ratio in moomoo_events:
        best = None
        for yf_date, yf_factor in yf_remaining:
            days_off = abs(_to_ordinal(yf_date) - _to_ordinal(m_date))
            if days_off <= date_window_days and (best is None or days_off < best[0]):
                best = (days_off, yf_date, yf_factor)

        if best is None:
            rows.append({
                "verdict": "moomoo_only", "moomoo_date": m_date, "moomoo_ratio": m_ratio,
                "yfinance_date": None, "yfinance_factor": None,
                "yfinance_expected_ratio": None,
            })
            continue

        _, yf_date, yf_factor = best
        yf_remaining.remove((yf_date, yf_factor))
        expected = expected_ratio_from_yfinance_factor(yf_factor)
        verdict = "match" if abs(m_ratio - expected) <= expected * ratio_tolerance else "ratio_mismatch"
        rows.append({
            "verdict": verdict, "moomoo_date": m_date, "moomoo_ratio": m_ratio,
            "yfinance_date": yf_date, "yfinance_factor": yf_factor,
            "yfinance_expected_ratio": expected,
        })

    for yf_date, yf_factor in yf_remaining:
        rows.append({
            "verdict": "yfinance_only", "moomoo_date": None, "moomoo_ratio": None,
            "yfinance_date": yf_date, "yfinance_factor": yf_factor,
            "yfinance_expected_ratio": expected_ratio_from_yfinance_factor(yf_factor),
        })

    return rows


# --- moomooの生データからのイベント抽出 -----------------------------------------


def extract_moomoo_split_events(rehab_df: pd.DataFrame) -> list[tuple[str, float]]:
    """`MoomooClient.get_rehab()` の生データから分割行だけを抜き出す。

    `get_rehab()` は配当・特別配当等も同じテーブルで返す（`split_ratio` は
    それらの行では NaN になる）。moomoo は拆合股(split)と送股(stock dividend)を
    別フィールドで管理しているため、yfinance と違い**ここでは株式配当混入を
    心配しなくてよい**（`split_ratio` が立っている行は常に本物の分割・併合）。
    """
    if rehab_df is None or rehab_df.empty:
        return []
    df = rehab_df.dropna(subset=["split_ratio"])
    return list(zip(df["ex_div_date"].astype(str), df["split_ratio"].astype(float)))


# --- 銘柄単位のオーケストレーション ---------------------------------------------


def verify_ticker(ticker: str, moomoo_events: list[tuple[str, float]],
                   yfinance_events: list[tuple[str, float]]) -> dict:
    """1銘柄ぶんの突き合わせ結果をまとめる。

    yfinance側は突き合わせ前に株式配当ノイズを除いておく
    （呼び出し側にこの前処理を強いない。忘れるとノイズだらけのレポートになる）。
    """
    yf_filtered = filter_discriminable_yfinance_events(yfinance_events)
    events = match_split_events(moomoo_events, yf_filtered)
    return {
        "ticker": ticker,
        "events": events,
        # 「一致」でも「不一致」でもない第3の状態。取り違えないよう明示のフラグにする
        # （events が空リストなのは「判定不能」であって「一致した」ではない）。
        "no_records_either_source": not moomoo_events and not yf_filtered,
    }


# --- 取得と実行 -----------------------------------------------------------


def resolve_yfinance_records_path() -> str:
    """yfinance側の分割記録（本番専用。読み取り専用参照）のパスを返す。

    本番を読むこと自体が目的の監査ツールのため `require_prod_data_root()` を使う
    （`get_prod_data_root()` の `None` 許容は本ツールには合わない。
    `backend/paths.py` の docstring 参照）。
    """
    root = paths.require_prod_data_root()
    return os.path.join(root, "maintenance_reports", "split_records.json")


def resolve_report_dir() -> str:
    """レポート出力先（**ワークツリーローカル**。本番 `data/` は読み取り専用のため書けない）。"""
    return os.path.join(_project_root, "data", "maintenance_reports")


def run(tickers: list[str], report_dir: str | None = None,
        client: MoomooClient | None = None) -> dict:
    """`--tickers` で指定した銘柄を照合する。

    Note:
        **`client` を自分で生成した場合は必ず `close()` する。** OpenD への
        TCP接続を握ったままだとプロセスが終了せずハングする
        （2026-09-11実測: `run()` 呼び出し後にプロセスが終了せず、外側の
        `timeout` に強制終了されて初めて気づいた）。呼び出し側が渡した
        `client` はここでは閉じない（呼び出し側が使い回す前提のため）。
    """
    owns_client = client is None
    client = client or MoomooClient()
    try:
        return _run(tickers, report_dir or resolve_report_dir(), client)
    finally:
        if owns_client:
            client.close()


def _run(tickers: list[str], report_dir: str, client: MoomooClient) -> dict:
    records_path = resolve_yfinance_records_path()
    yfinance_records = split_map(load_split_records(records_path))

    print("=" * 74)
    print(f"moomoo 独立照合  {len(tickers)}銘柄")
    print("**報告のみ。データは一切変更しない。**")
    print("=" * 74)

    results = []
    failed = []
    for t in tickers:
        try:
            moomoo_events = extract_moomoo_split_events(client.get_rehab(t))
        except (MoomooApiError, MoomooNotConnectedError) as e:
            failed.append(t)
            print(f"  [WARN] {t}: moomoo取得に失敗 ({e})")
            continue
        result = verify_ticker(t, moomoo_events, yfinance_records.get(t, []))
        results.append(result)

        if result["no_records_either_source"]:
            print(f"  {t:<8} 両ソースとも記録なし（判定不能）")
        else:
            for ev in result["events"]:
                print(f"  {t:<8} {ev['verdict']:<15} "
                      f"moomoo={ev['moomoo_date']}/{ev['moomoo_ratio']} "
                      f"yfinance={ev['yfinance_date']}/{ev['yfinance_factor']}")

    rows = [{"ticker": r["ticker"], **ev} for r in results for ev in r["events"]]
    rows += [{"ticker": r["ticker"], "verdict": "no_records_either_source",
              "moomoo_date": None, "moomoo_ratio": None,
              "yfinance_date": None, "yfinance_factor": None,
              "yfinance_expected_ratio": None}
             for r in results if r["no_records_either_source"]]

    path = None
    if rows:
        os.makedirs(report_dir, exist_ok=True)
        path = os.path.join(
            report_dir, f"moomoo_verification_{datetime.now():%Y%m%d_%H%M%S}.csv")
        pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")
        print(f"\n  全件: {path}")

    if failed:
        print(f"\n  取得失敗: {len(failed)}銘柄 ({', '.join(failed)})")

    print("\n  ※ 修正は自動で行わない。判断は人間が行うこと")
    return {"results": results, "failed": failed, "report_path": path}


if __name__ == "__main__":
    use_utf8_stdout()
    p = argparse.ArgumentParser(
        description="moomoo APIを独立ソースとして分割記録を照合する（報告のみ）")
    p.add_argument("--tickers", required=True, help="対象を指定する（カンマ区切り・必須）")
    p.add_argument("--report-dir", default=None,
                   help="CSVレポートの出力先（既定はワークツリーローカルの data/maintenance_reports/）")
    a = p.parse_args()
    res = run([t.strip() for t in a.tickers.split(",")], report_dir=a.report_dir)
    print(json.dumps({"checked": len(res["results"]), "failed": len(res["failed"])},
                      ensure_ascii=False))
