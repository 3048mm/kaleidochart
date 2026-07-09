# 昇格（deploy_after_merge）用の Parquet 世代コピー・swap・ロールバック処理。
# MVCC 世代（タイムスタンプ付きファイル + latest_master.json ポインタ）の
# ファイル操作だけを担う純粋なモジュール。パイプライン計算には関与しない。
# 詳細: doc/agent_execution_rules.md §10 / doc/in_progress/deploy_after_merge_plan.md
import os
import re
import json
import shutil
import logging

from pipeline.parquet_cache_manager import (
    get_pointer_file_path,
    get_latest_master_files,
    update_pointer_with_retry,
)

_TS_PATTERN = re.compile(r"_(\d{8}_\d{6})\.parquet$")


def read_generation(parquet_dir: str) -> dict | None:
    """parquet_dir の latest_master.json が指す世代（テーブル名→パスの dict）を返す。無ければ None。"""
    return get_latest_master_files(get_pointer_file_path(parquet_dir))


def generation_timestamp(pointer: dict) -> str | None:
    """ポインタ内のファイル名から世代タイムスタンプ（YYYYMMDD_HHMMSS）を抽出する。"""
    for path in pointer.values():
        m = _TS_PATTERN.search(os.path.basename(str(path)))
        if m:
            return m.group(1)
    return None


def _copy_pointed_files(pointer: dict, src_dir: str, dst_dir: str) -> dict:
    """ポインタが指す実体ファイルを basename 基準で src_dir → dst_dir へコピーし、
    dst_dir を指す新しいポインタ dict を返す。"""
    os.makedirs(dst_dir, exist_ok=True)
    new_pointer = {}
    for key, path in pointer.items():
        base = os.path.basename(str(path))
        src = os.path.join(src_dir, base)
        dst = os.path.join(dst_dir, base)
        if not os.path.exists(src):
            raise FileNotFoundError(f"世代ファイルが見つかりません: {src}")
        shutil.copy2(src, dst)
        new_pointer[key] = dst
    return new_pointer


def copy_generation(src_dir: str, dst_dir: str, logger: logging.Logger) -> dict:
    """src_dir の最新世代を dst_dir へ丸ごとコピーし、dst_dir のポインタを書く。
    昇格ワークスペースの準備（本番 Parquet → data/tmp/deploy/）に使う。"""
    pointer = read_generation(src_dir)
    if pointer is None:
        raise FileNotFoundError(f"latest_master.json が見つかりません: {src_dir}")
    new_pointer = _copy_pointed_files(pointer, src_dir, dst_dir)
    if not update_pointer_with_retry(get_pointer_file_path(dst_dir), new_pointer, logger):
        raise OSError(f"コピー先ポインタの書き込みに失敗しました: {dst_dir}")
    logger.info(f"世代コピー完了: {src_dir} -> {dst_dir}（{len(new_pointer)} ファイル）")
    return new_pointer


def promote_generation(workspace_dir: str, prod_dir: str, logger: logging.Logger) -> tuple[dict | None, dict]:
    """ワークスペースで再生成された世代を本番へ昇格する。
    実体ファイルを本番ディレクトリへコピーした後、ポインタをアトミックに差し替える。
    旧世代の実体は削除しない（ロールバック用に保持。prune は別途）。
    戻り値: (旧ポインタ or None, 新ポインタ)"""
    ws_pointer = read_generation(workspace_dir)
    if ws_pointer is None:
        raise FileNotFoundError(f"ワークスペースに世代がありません: {workspace_dir}")
    old_pointer = read_generation(prod_dir)

    new_pointer = _copy_pointed_files(ws_pointer, workspace_dir, prod_dir)

    # prune（clean_old_parquet_versions）互換のため世代別 JSON も書く
    ts = generation_timestamp(new_pointer)
    if ts:
        version_file = os.path.join(prod_dir, f"data_version_{ts}.json")
        with open(version_file, "w", encoding="utf-8") as f:
            json.dump(new_pointer, f, ensure_ascii=False, indent=2)

    if not update_pointer_with_retry(get_pointer_file_path(prod_dir), new_pointer, logger):
        raise OSError(f"本番ポインタの更新に失敗しました: {prod_dir}")
    logger.info(f"昇格完了: 世代 {ts} が本番ポインタになりました（旧世代は保持）")
    return old_pointer, new_pointer


def nearest_existing_dir(path: str) -> str:
    """path から親方向に辿り、最初に存在するディレクトリを返す。
    shutil.disk_usage 等、存在するパスを要求する API に未作成パス
    （例: 初回実行時の data/tmp/deploy）を渡す前の解決に使う。"""
    probe = os.path.abspath(path)
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:  # ドライブルートまで到達
            break
        probe = parent
    return probe


def is_pipeline_lock_held(lock_file: str) -> bool:
    """update_pipeline.lock が実行中プロセスに保持されているかを判定する。

    update_pipeline.py の release_lock はロック解除のみでファイルを削除しないため、
    ファイルの存在では「実行中」と「残骸」を区別できない。
    msvcrt の非ブロッキングロックが取得できるかで判定する（Windows 前提）。"""
    if not os.path.exists(lock_file):
        return False
    import msvcrt
    try:
        fd = os.open(lock_file, os.O_RDWR)
    except OSError:
        return True  # open すらできない = 他プロセスが排他保持している
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def rollback_generation(prod_dir: str, old_pointer: dict | None, logger: logging.Logger) -> bool:
    """本番ポインタを旧世代へ戻す。旧ポインタが無い場合は False。"""
    if not old_pointer:
        logger.error("ロールバック不能: 旧世代のポインタがありません")
        return False
    missing = [p for p in old_pointer.values()
               if not os.path.exists(os.path.join(prod_dir, os.path.basename(str(p))))]
    if missing:
        logger.error(f"ロールバック不能: 旧世代ファイルが欠損しています: {missing}")
        return False
    if not update_pointer_with_retry(get_pointer_file_path(prod_dir), old_pointer, logger):
        return False
    logger.warning(f"ロールバック実行: 本番ポインタを世代 {generation_timestamp(old_pointer)} へ戻しました")
    return True
