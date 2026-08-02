"""完全再構築の前に Parquet マスターを削除ではなく退避する。

## なぜ削除してはいけないか

Parquet の T2 価格系列は「原本」（`agent_execution_rules.md` §10.1）であり、
完全再構築は上流（Yahoo）から取り直す。**上流が系列を切り落とした銘柄は
再構築で永久に失われる。**

2026-08-02 に実際に発生した: 17銘柄（`BLD` TopBuild(S&P500) / `LC` LendingClub /
`SCVL` Shoe Carnival など、いずれも現役の上場企業）の全履歴が 1〜11 行まで消えた。
Yahoo が銘柄レコードを作り直して `firstTradeDate` を打ち直したのが原因で、
取り直しても直らない。旧世代が `data/_bk/` に偶然残っていたため復旧できたが、
残っていなければ 8 年分の履歴を失っていた。

退避しておけば `restore_truncated_symbol_history.py` で継ぎ直せる。

## なぜバッチではなく Python か

`.bat` に `call :label` / `goto` を書くと、UTF-8 の多バイト文字を含むファイルで
cmd のラベル探索が壊れる（実測: `The system cannot find the batch label specified`）。
再構築前の最後の砦になる処理をその脆さの上に載せない。

Usage:
    python backend/scripts/archive_parquet_master.py
    python backend/scripts/archive_parquet_master.py --dry-run
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)

ARCHIVE_PREFIX = "parquet_master_pre_rebuild_"


def archive(data_dir: str, dry_run: bool = False) -> str | None:
    """`parquet_master` を `parquet_master_pre_rebuild_<日時>` へ改名する。

    Returns:
        退避先のパス。退避対象が無ければ None。
    """
    src = os.path.join(data_dir, "parquet_master")
    if not os.path.isdir(src):
        print(f"[SKIP] {src} が存在しません。退避するものがありません。")
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(data_dir, f"{ARCHIVE_PREFIX}{stamp}")
    size_mb = sum(
        os.path.getsize(os.path.join(src, f))
        for f in os.listdir(src)
        if os.path.isfile(os.path.join(src, f))
    ) / 1e6

    print(f"退避元: {src}  ({size_mb:,.0f} MB)")
    print(f"退避先: {dst}")
    if dry_run:
        print("[DRY-RUN] 何もしていません。")
        return None

    # copy ではなく move。コピーだとディスクを二重に食う上、
    # 「退避したつもりで元が残っている」状態を作ると再構築が旧世代を拾う。
    shutil.move(src, dst)
    print(f"\n退避しました（削除はしていません）。")
    print("再構築の完了後、上流が切り落とした銘柄の履歴を復元してください:")
    print(f"  python backend/scripts/restore_truncated_symbol_history.py --dry-run --backup-dir {dst}")
    return dst


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="再構築前に Parquet マスターを退避する")
    p.add_argument("--dry-run", action="store_true", help="退避せず内容だけ表示")
    p.add_argument("--data-dir", default=os.path.join(_project_root, "data"),
                   help="data ディレクトリ（既定: リポジトリの data/）")
    a = p.parse_args()
    try:
        archive(a.data_dir, dry_run=a.dry_run)
    except Exception as e:
        print(f"[ERROR] 退避に失敗しました: {e}")
        print("        データ損失を避けるため、再構築は中止してください。")
        sys.exit(1)
