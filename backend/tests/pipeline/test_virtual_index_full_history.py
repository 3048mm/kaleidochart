"""仮想テーマ指数の再合成が Parquet 全期間を使うことのテスト。

## 何が起きたか（2026-08-07）

`build_all_virtual_indexes_prices` は構成銘柄の価格を **SQLite の `daily_prices`**
から読む。SQLite はホットキャッシュで**直近730日しか持たない**。

2026-08-07 07:49 に構成銘柄の変更をきっかけに全170テーマが `rebuild` モードで
再合成され、当時の SQLite 最古日 **2024-08-06 で指数が基準値1000から振り直された**。
それが Parquet の古い履歴の上にマージされ、継ぎ目に偽の段差ができた。

```
_HLTHCB_  2024-08-06  close=1019.65  当日リターン -98.2%   ← 直前は ~55,700
_BLCK3F_  2024-08-06  close=1033.16  当日リターン -92.5%
_BLOK_    2024-08-06  close=1015.56  当日リターン -91.5%
```

実測: 170本中 **165本が 2024-08-06 に終値 ~1000**、**152本が同日に大きな段差**。

これは T4 で既知の問題（「SQLite に存在する日付しか計算できない」）と同じ根本原因。
`parquet_recompute.py` のモジュール docstring 参照。

## 直し方

`rebuild` モードのときだけ、構成銘柄の価格を **Parquet マスター（全期間）**から読む。
`incremental` モードは直前の指数値を種にして継ぎ足すだけなので SQLite で足りる。
"""

import json
import os
import sqlite3
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from db.models import Base, DailyPrice, Symbol, ThemeConstituent  # noqa: E402
from pipeline.orchestrator import build_all_virtual_indexes_prices  # noqa: E402

# 構成銘柄の履歴は2020年の1年分、SQLite に残すのは最後の40営業日だけ
ALL_DAYS = 250
HOT_DAYS = 40


def _business_days(n):
    out, d = [], date(2020, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _prices_frame():
    """構成銘柄2本。日中値を持たせる（OHLC が同値だと合成の欠陥を見逃す）。"""
    days = _business_days(ALL_DAYS)
    rows = []
    for sid, drift in ((10, 1.001), (11, 1.002)):
        c = 100.0
        for d in days:
            c *= drift
            rows.append({"symbol_id": sid, "date": d.strftime("%Y-%m-%d"),
                         "open": c * 0.996, "high": c * 1.012,
                         "low": c * 0.987, "close": c, "volume": 1000.0})
    return pd.DataFrame(rows), days


@pytest.fixture
def env(tmp_path):
    """SQLite は直近 HOT_DAYS だけ / Parquet は全 ALL_DAYS を持つ状態を作る。"""
    px, days = _prices_frame()
    hot_from = days[-HOT_DAYS].strftime("%Y-%m-%d")

    db_path = str(tmp_path / "stocktool.db")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        Symbol(id=10, ticker="AAA", exchange="NASDAQ", category="個別", active=1),
        Symbol(id=11, ticker="BBB", exchange="NASDAQ", category="個別", active=1),
        Symbol(id=101, ticker="_THEME_", exchange="VIRTUAL", category="テーマ",
               theme_type="virtual", active=1),
        ThemeConstituent(theme_id=101, symbol_id=10),
        ThemeConstituent(theme_id=101, symbol_id=11),
    ])
    for _, r in px[px["date"] >= hot_from].iterrows():
        session.add(DailyPrice(
            symbol_id=int(r["symbol_id"]),
            date=date.fromisoformat(r["date"]),
            open=r["open"], high=r["high"], low=r["low"],
            close=r["close"], volume=r["volume"]))
    session.commit()

    # Parquet マスター（db と同じディレクトリの parquet_master/ に置かれる規約）
    pdir = tmp_path / "parquet_master"
    pdir.mkdir()
    files = {}
    for key, name, frame in [
        ("prices", "prices", px),
        ("symbols", "symbols", pd.DataFrame([
            {"id": 10, "ticker": "AAA", "active": 1},
            {"id": 11, "ticker": "BBB", "active": 1},
            {"id": 101, "ticker": "_THEME_", "active": 1}])),
        ("tc", "theme_constituents", pd.DataFrame([
            {"theme_id": 101, "symbol_id": 10},
            {"theme_id": 101, "symbol_id": 11}])),
    ]:
        f = pdir / f"{name}_20200101_000000.parquet"
        frame.to_parquet(f, index=False)
        files[key] = str(f)
    (pdir / "latest_master.json").write_text(
        json.dumps(files, ensure_ascii=False), encoding="utf-8")

    return {"session": session, "db_path": db_path,
            "hash_file": str(tmp_path / "hashes.json"),
            "first_day": days[0], "hot_from": hot_from, "all_days": days}


def _run(env_):
    build_all_virtual_indexes_prices(
        env_["session"],
        [{"ticker": "_THEME_", "exchange": "VIRTUAL"}],
        {("_THEME_", "VIRTUAL"): 101},
        hash_file_path=env_["hash_file"],
        db_path=env_["db_path"],
    )
    env_["session"].commit()
    return (env_["session"].query(DailyPrice)
            .filter(DailyPrice.symbol_id == 101)
            .order_by(DailyPrice.date).all())


def test_rebuild_covers_the_full_parquet_history(env):
    """**再合成は SQLite の730日窓ではなく Parquet 全期間から作ること。**

    ここが SQLite 窓のままだと、指数が窓の左端で 1000 に振り直され、
    Parquet に残る古い履歴との継ぎ目に偽の段差ができる（2026-08-07 の事故）。
    """
    rows = _run(env)
    assert rows, "1行も作られていない"
    first = rows[0].date
    # 初日はリターンが取れないので2営業日目から始まる
    assert first <= env["all_days"][1], (
        f"指数が {first} からしか無い（Parquet は {env['all_days'][0]} から）。"
        f" SQLite の窓だけで再合成している")
    assert len(rows) == len(env["all_days"]) - 1


def test_rebuild_does_not_restart_at_the_hot_cache_boundary(env):
    """ホットキャッシュ境界で基準値 1000 に戻っていないこと。

    2026-08-07 の事故の直接的な指紋がこれ（165/170 本が境界日に終値 ~1000）。
    """
    rows = _run(env)
    by_date = {r.date.strftime("%Y-%m-%d"): r.close for r in rows}
    boundary = env["hot_from"]
    assert boundary in by_date, "境界日の行が無い（テストの前提が壊れている）"
    assert not (950 < by_date[boundary] < 1060), (
        f"境界日 {boundary} の終値が {by_date[boundary]:.2f} で基準値 1000 近傍。"
        f" 窓の左端で振り直している")


def test_index_is_continuous_across_the_hot_cache_boundary(env):
    """境界日に偽の段差が無いこと。"""
    rows = _run(env)
    s = pd.Series({r.date.strftime("%Y-%m-%d"): r.close for r in rows}).sort_index()
    ret = (s / s.shift(1)).dropna()
    worst = ret.sub(1).abs().max()
    assert worst < 0.05, (
        f"最大 {worst:.1%} の段差がある（構成銘柄は日率 0.1〜0.2% しか動かない）: "
        f"{ret.sub(1).abs().idxmax()}")


def test_incremental_mode_still_uses_the_hot_cache(env):
    """差分モードは直前の指数値を種にするだけなので SQLite で足りる。

    ここまで Parquet を読みに行くと日次更新が毎回全期間を舐めて遅くなる。
    """
    _run(env)                      # 1回目 = rebuild（ハッシュを保存する）
    before = _run(env)             # 2回目 = incremental
    assert before, "差分モードで行が消えている"
    s = pd.Series({r.date.strftime("%Y-%m-%d"): r.close for r in before}).sort_index()
    ret = (s / s.shift(1)).dropna()
    assert ret.sub(1).abs().max() < 0.05, "差分モードで段差ができている"
