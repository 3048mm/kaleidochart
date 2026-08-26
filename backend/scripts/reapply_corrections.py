"""台帳に記録した価格補正を再適用する。

## いつ使うか

**全期間再構築（`run/tool/refresh_All.bat`）の直後。**
再構築は Yahoo から取り直すので、手で当てた補正はすべて巻き戻る。
`parquet-data-quality` SKILL §9 の再構築後チェックリストにある項目4
「`truncate_symbol_history.py` を再適用」と同じ性質の作業。

台帳は `data/price_corrections.toml`（git 管理下）。

## 「全部試して、当たらないものは弾かれる」運用

`adjust_symbol_split` は**冪等ではない**（2回かければ ×900 になる）が、
接合部の検算があるので**既に補正済みの系列に当てようとすると自分で止まる**。
そのため本スクリプトは台帳の全件を順に試し、

  - 適用できた                     → APPLIED
  - 検算に落ちた（既に正しい等）   → SKIPPED（**失敗ではない**）
  - それ以外の失敗                 → FAILED（終了コード 1）

と分けて集計する。**まず `--dry-run` で何が起きるか見てから `--apply`。**

> [!IMPORTANT]
> **これは補正値を推定する仕組みではない。** 台帳に人が書いた比率をそのまま
> 再生するだけで、上流の記録から値を割り出したりはしない
> （その案は 2026-08-06 に見送っている。
>  `doc/completed/split_anomaly_noise_reduction_plan.md` §8）。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\reapply_corrections.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\reapply_corrections.py --apply
"""

import argparse
import os
import sys

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from pipeline.pipeline_lock import pipeline_lock  # noqa: E402
from pipeline.price_corrections import (  # noqa: E402
    Correction,
    CorrectionError,
    load_corrections,
)
from scripts.adjust_symbol_split import SeamCheckError  # noqa: E402
from scripts.adjust_symbol_split import run as _adjust_run  # noqa: E402
from scripts.delete_symbol_rows import run as _delete_run  # noqa: E402
from scripts.scan_price_anomalies import use_utf8_stdout  # noqa: E402

DEFAULT_LEDGER = os.path.join("data", "price_corrections.toml")

APPLIED = "applied"
SKIPPED = "skipped"
FAILED = "failed"

_LABEL = {APPLIED: "適用", SKIPPED: "適用不要", FAILED: "失敗"}


# テストから差し替えられるよう、呼び出しを薄い関数に包む
def _run_split(**kw):
    return _adjust_run(**kw)


def _run_delete(**kw):
    return _delete_run(**kw)


def apply_one(c: Correction, dry_run: bool, db_path: str | None = None) -> str:
    """補正1件を対応するスクリプトへ流す。

    Returns:
        `APPLIED` / `SKIPPED` / `FAILED`。

    Note:
        **`SKIPPED` は失敗ではない。** 接合部の検算に落ちるのは「既に補正済み」か
        「比率が違う」で、前者は再適用として正常。後者は出力に理由が出る。
    """
    try:
        if c.kind == "split":
            outcome = _run_split(ticker=c.ticker, before=c.before, factor=c.factor,
                                 reason=c.reason, dry_run=dry_run, db_path=db_path)
        elif c.kind == "fabricated_rows":
            # **日付は明示せず自動検出させる。** 再構築後に上流が返す欠損の位置は
            # 前回と同じとは限らないので、範囲だけ渡して
            # 「OHLC 全同値かつ出来高0」を探させる方が正しい。
            outcome = _run_delete(ticker=c.ticker, dates=None, auto=True,
                                  date_from=c.date_from, date_to=c.date_to,
                                  reason=c.reason, dry_run=dry_run, db_path=db_path)
        else:
            raise ValueError(f"未対応の補正種別です: {c.kind!r}")

        # **何もしなかったものを「適用」と数えない。** 対象行が0件でも `run()` は
        # 正常終了するので、戻り値で区別する（2026-08-26 に `AVB` が捏造行0件なのに
        # 「適用」と報告され、集計が嘘をついた）。
        if outcome == "noop":
            return SKIPPED
    except SeamCheckError as e:
        print(f"    [適用不要] {e}")
        return SKIPPED
    except SystemExit as e:
        if not e.code:
            return APPLIED
        print(f"    [失敗] スクリプトが終了コード {e.code} で中断しました")
        return FAILED
    except ValueError:
        raise
    except Exception as e:                       # noqa: BLE001
        print(f"    [失敗] {type(e).__name__}: {e}")
        return FAILED
    return APPLIED


def summarize(results: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in results:
        out[r] = out.get(r, 0) + 1
    return out


def exit_code_for(summary: dict[str, int]) -> int:
    """1件でも失敗したら非ゼロ。検収で見落とさないため。"""
    return 1 if summary.get(FAILED) else 0


def run(ledger_path: str, dry_run: bool, db_path: str | None = None) -> int:
    path = ledger_path if os.path.isabs(ledger_path) \
        else os.path.join(_project_root, ledger_path)

    print("=" * 74)
    print(f"補正の再適用  (dry-run={dry_run})")
    print("=" * 74)
    print(f"[台帳] {path}")

    try:
        corrections = load_corrections(path)
    except CorrectionError as e:
        print(f"\n[ERROR] 台帳の記述が不正です。**何も適用していません。**\n  {e}")
        return 1

    if not corrections:
        print("\n台帳に補正がありません。終了します。")
        return 0

    print(f"\n登録されている補正 {len(corrections)}件:")
    for i, c in enumerate(corrections, 1):
        applied = f"  （初回適用 {c.applied}）" if c.applied else ""
        print(f"  {i}. {c.describe()}{applied}")
        print(f"     理由: {c.reason}")

    results = []
    for i, c in enumerate(corrections, 1):
        print(f"\n{'-' * 74}\n[{i}/{len(corrections)}] {c.describe()}\n{'-' * 74}")
        r = apply_one(c, dry_run=dry_run, db_path=db_path)
        results.append(r)
        print(f"  → {_LABEL[r]}")

    s = summarize(results)
    print("\n" + "=" * 74)
    print("結果: " + " / ".join(f"{_LABEL[k]} {v}件" for k, v in s.items()))
    if s.get(FAILED):
        print("**失敗した補正があります。** 上の出力を確認してください。")
    if dry_run:
        print("（--dry-run のため書き込んでいません）")
    return exit_code_for(s)


if __name__ == "__main__":
    use_utf8_stdout()
    p = argparse.ArgumentParser(
        description="台帳に記録した価格補正を再適用する（全期間再構築の直後に使う）")
    p.add_argument("--ledger", default=DEFAULT_LEDGER,
                   help=f"台帳のパス（既定 {DEFAULT_LEDGER}）")
    p.add_argument("--db-path", default=None, help="書き込み先 SQLite を明示指定")
    p.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    p.add_argument("--apply", action="store_true", help="実際に書き込む")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    # 各スクリプトの `__main__` ではなく `run()` を直接呼ぶので、ロックはここで1回だけ取る
    with pipeline_lock("reapply_corrections"):
        sys.exit(run(a.ledger, dry_run=not a.apply, db_path=a.db_path))
