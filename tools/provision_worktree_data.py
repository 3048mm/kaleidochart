"""ワークツリーの `data/` をプロビジョニングする。

ワークツリーの `data/` は git 管理の TOML 数件しか無く、そのまま実行すると
**エラーにならず空DBが新規作成される**（`doc/agent_execution_rules.md` §10.3）。
このスクリプトはワークツリーに「どこのデータを使うか」を明示的に記録し、
必要なら使い捨ての sandbox を構築する。

    python tools/provision_worktree_data.py <worktree> --mode read
    python tools/provision_worktree_data.py <worktree> --mode write [--light] [--with-optuna]

`--mode read`（既定）
    `config.local.toml` を生成するだけ。本番 data は `prod_root` として記録され、
    **読み取り専用**で参照される。書き込みは `paths.ensure_writable()` が止める。
    対象: バックテスト・スクリーナー式の変更・API 読み取り・フロントエンド（種別 A）

`--mode write`
    加えて `data/sandbox/` を構築する。対象: スキーマ変更・indicator 追加・
    パイプライン変更（種別 B / C）。

## 設計上の注意（実測に基づく）

- **PowerShell では書かない**。`Set-Content -Encoding utf8` は BOM を付け、
  `json.load()` が `Unexpected UTF-8 BOM` で落ちる。しかも
  `get_latest_master_files()` はそれを握り潰して `None` を返すため、
  無関係な場所で `TypeError` になり原因が分からなくなる。
- **`latest_master.json` は絶対パスを持つ**。単純コピーすると sandbox の
  ポインタが本番ファイルを指し続け、しかも読めてしまうので気づけない。
  必ず sandbox 側のパスへ書き換える。
- **ハードリンクが張れなければ中断する**。黙って実コピーにフォールバックすると
  数GBを消費する。ハードリンクは同一ボリューム・NTFS が条件。
- **シンボリックリンク／ジャンクションは使わない**。前者は管理者権限が要り、
  後者は `git worktree remove` が辿ってリンク先を全削除する（実測）。

詳細: `doc/completed/worktree_data_provisioning_plan.md`
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys

#: `--mode write` で sandbox にコピーする補助ファイル（git 管理外）
AUX_FILES = ("virtual_theme_hashes.json",)

#: ユーザー資産DB。**必ず実コピー**する（本番を指させない。§10.1）
USER_ASSET_DBS = ("user_data.db", "universe.db")


# --- リポジトリの解決 -----------------------------------------------------

def find_main_repo_root(worktree: str) -> str:
    """ワークツリーから本体チェックアウトのルートを求める。

    `git rev-parse --git-common-dir` は本体の `.git` を返すので、その親が本体ルート。
    """
    out = subprocess.run(
        ["git", "-C", worktree, "rev-parse", "--path-format=absolute",
         "--git-common-dir"],
        check=True, capture_output=True, text=True).stdout.strip()
    return os.path.dirname(os.path.abspath(out))


def load_paths_module():
    """`backend/paths.py` を読み込む（パス解決の権威を再実装しないため）。

    **このスクリプト自身と同じリポジトリから**読む。本体側から読むと、
    merge 前のワークツリーで実行したときに本体に `paths.py` が無く落ちる。
    モジュールの実体はどこから来ても構わない（`repo_root` を明示的に渡して
    使うので、参照するファイルは常に本体側になる）。
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target = os.path.join(here, "backend", "paths.py")
    spec = importlib.util.spec_from_file_location("_stocktool_paths", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- config.local.toml ----------------------------------------------------

#: 生成ブロックの目印。再実行時にこの行ごと取り除いて重複を防ぐ。
GENERATED_MARKER = "# tools/provision_worktree_data.py が生成。手で編集しないこと。"


def strip_data_section(text: str) -> str:
    """既存の `[data]` セクションだけを取り除く（他セクションのコメントは保つ）。

    冪等性のため、生成ブロックの目印コメントも併せて落とす
    （落とさないと再実行のたびにコメントだけが積み上がる）。
    """
    out, skipping = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            skipping = stripped == "[data]"
        if skipping or stripped == GENERATED_MARKER:
            continue
        out.append(line)
    return "\n".join(out).rstrip() + "\n" if out else ""


def write_config_local(worktree: str, main_root: str,
                       data_root: str, prod_root: str) -> str:
    """ワークツリーの `config.local.toml` を生成する。

    本体の内容（`[sec] contact` / `[tls] ca_bundle`）を丸ごと引き継いだうえで
    `[data]` を付け替える。ワークツリーには git 管理外のため配布されず、
    その結果 `resolve_contact()` が ValueError になっていた問題も同時に解消する。
    """
    src = os.path.join(main_root, "config.local.toml")
    base = ""
    if os.path.exists(src):
        with open(src, encoding="utf-8-sig") as f:
            base = strip_data_section(f.read())

    body = base
    if body and not body.endswith("\n"):
        body += "\n"
    body += (
        f"\n{GENERATED_MARKER}\n"
        "[data]\n"
        f'root = "{data_root.replace(os.sep, "/")}"\n'
        f'prod_root = "{prod_root.replace(os.sep, "/")}"\n'
    )

    dst = os.path.join(worktree, "config.local.toml")
    # BOM なし UTF-8 / LF
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    return dst


# --- sandbox の構築 -------------------------------------------------------

def link_parquet_master(src_dir: str, dst_dir: str) -> int:
    """本番 Parquet の最新世代をハードリンクし、ポインタを sandbox へ書き換える。"""
    pointer_src = os.path.join(src_dir, "latest_master.json")
    if not os.path.exists(pointer_src):
        raise SystemExit(f"本番の latest_master.json が見つかりません: {pointer_src}")

    os.makedirs(dst_dir, exist_ok=True)
    with open(pointer_src, encoding="utf-8-sig") as f:
        pointer = json.load(f)

    new_pointer, linked = {}, 0
    for key, src in pointer.items():
        if not os.path.exists(src):
            raise SystemExit(
                f"ポインタが指す Parquet がありません: {src}\n"
                f"  本番側の世代が prune された可能性があります。")
        dst = os.path.join(dst_dir, os.path.basename(src))
        if not os.path.exists(dst):
            try:
                os.link(src, dst)
            except OSError as e:
                # 実コピーへ**フォールバックしない**（数GBを黙って消費するため）
                raise SystemExit(
                    f"ハードリンクを作成できませんでした: {dst}\n"
                    f"  原因: {e}\n"
                    f"  ハードリンクは同一ボリューム上の NTFS でのみ作成できます。\n"
                    f"  本番 data とワークツリーが別ドライブに無いか確認してください。")
            linked += 1
        new_pointer[key] = dst
        print(f"  [hardlink] {key:<10} {os.path.basename(src)}")

    with open(os.path.join(dst_dir, "latest_master.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(new_pointer, f, ensure_ascii=False, indent=2)
    print(f"  [pointer ] {len(new_pointer)} エントリを sandbox のパスへ書き換え")

    for src in glob.glob(os.path.join(src_dir, "data_version_*.json")):
        dst = os.path.join(dst_dir, os.path.basename(src))
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
    return linked


def is_empty_sqlite(path: str) -> bool:
    """「中身が無い」SQLite かどうか（テーブルが無い / 全テーブル 0 行 / 壊れている）。

    冪等性の判定に使う。**単なる存在チェックでは不十分**だった:
    pytest が残した 139,264 バイトの「スキーマだけの空DB」を
    「既存」とみなしてスキップしてしまい、塞ぎたかった空DBがそのまま残った。
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return True
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        if not tables:
            return True
        for t in tables:
            if conn.execute(f'SELECT 1 FROM "{t}" LIMIT 1').fetchone() is not None:
                return False
        return True
    except sqlite3.DatabaseError:
        return True
    finally:
        conn.close()


def copy_sqlite(src: str, dst: str) -> None:
    """SQLite を**バックアップ API**で複製する。

    ファイルコピーではなく `Connection.backup()` を使うのは、WAL モードで
    稼働中の DB をコピーすると `-wal` の内容を取りこぼした不整合なコピーに
    なりうるため（本番では API サーバや daily update が同時に動く）。

    既存の複製は上書きしない（冪等）が、**中身が空なら作り直す**。
    """
    if os.path.exists(dst):
        if not is_empty_sqlite(dst):
            print(f"  [skip    ] {os.path.basename(dst)}（既存）")
            return
        print(f"  [replace ] {os.path.basename(dst)}（空DBだったので作り直す）")
        os.remove(dst)
        for suffix in ("-wal", "-shm"):
            side = dst + suffix
            if os.path.exists(side):
                os.remove(side)
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        src_conn.execute("PRAGMA busy_timeout = 30000")
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    size_mb = os.path.getsize(dst) / (1024 * 1024)
    print(f"  [copy    ] {os.path.basename(dst)}  ({size_mb:,.0f} MB)")


def build_sandbox(worktree: str, main_data: str, *,
                  light: bool, with_optuna: bool) -> str:
    sandbox = os.path.join(worktree, "data", "sandbox")
    os.makedirs(sandbox, exist_ok=True)

    print("Parquet マスター:")
    link_parquet_master(os.path.join(main_data, "parquet_master"),
                        os.path.join(sandbox, "parquet_master"))

    print("SQLite:")
    if light:
        print("  [light   ] stocktool.db は create_sandbox.py で別途構築してください")
    else:
        copy_sqlite(os.path.join(main_data, "stocktool.db"),
                    os.path.join(sandbox, "stocktool.db"))

    for name in USER_ASSET_DBS:
        src = os.path.join(main_data, name)
        if os.path.exists(src):
            copy_sqlite(src, os.path.join(sandbox, name))

    if with_optuna:
        src = os.path.join(main_data, "optimization_trials.db")
        if os.path.exists(src):
            copy_sqlite(src, os.path.join(sandbox, "optimization_trials.db"))

    for name in AUX_FILES:
        src = os.path.join(main_data, name)
        dst = os.path.join(sandbox, name)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            print(f"  [copy    ] {name}")

    return sandbox


# --- エントリポイント -----------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("worktree", help="対象ワークツリーのパス（'.' 可）")
    ap.add_argument("--mode", choices=("read", "write"), default="read",
                    help="read: 設定のみ（既定） / write: sandbox も構築する")
    ap.add_argument("--light", action="store_true",
                    help="write 時に stocktool.db をコピーしない（軽量抽出を別途行う）")
    ap.add_argument("--with-optuna", action="store_true",
                    help="write 時に optimization_trials.db もコピーする")
    args = ap.parse_args(argv)

    worktree = os.path.abspath(args.worktree)
    if not os.path.isdir(worktree):
        raise SystemExit(f"ディレクトリがありません: {worktree}")

    main_root = find_main_repo_root(worktree)
    if os.path.normcase(main_root) == os.path.normcase(worktree):
        raise SystemExit(
            f"{worktree} は本体チェックアウトです。\n"
            f"  本体の data/ は本番そのものなのでプロビジョニングは不要です。")

    paths = load_paths_module()
    main_data = paths.get_data_root(main_root)
    if not os.path.isdir(main_data):
        raise SystemExit(f"本番 data が見つかりません: {main_data}")

    print(f"本体      : {main_root}")
    print(f"本番 data : {main_data}")
    print(f"ワークツリー: {worktree}")
    print(f"モード    : {args.mode}\n")

    if args.mode == "write":
        data_root = build_sandbox(worktree, main_data,
                                  light=args.light, with_optuna=args.with_optuna)
    else:
        data_root = os.path.join(worktree, "data")
        os.makedirs(data_root, exist_ok=True)

    conf = write_config_local(worktree, main_root, data_root, main_data)
    print(f"\n[config  ] {conf}")
    print(f"  [data] root      = {data_root}")
    print(f"  [data] prod_root = {main_data}")
    print("\n完了。本番データへの書き込みは paths.ensure_writable() が拒否します。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
