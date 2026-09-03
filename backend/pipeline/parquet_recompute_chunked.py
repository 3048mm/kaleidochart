"""全銘柄・全期間の T3 再計算を、メモリを一度に確保せず行うための薄いラッパー。

## なぜ必要か

`parquet_recompute.recompute_indicators()` は銘柄ごとの結果を `out` リストに溜めて
最後に `pd.concat` する。**全銘柄（約600万行 × 66列）で呼ぶと落ちる。**

2026-09-01 に rotate の `pd.concat` が同じ規模で失敗している:

    Unable to allocate 1.77 GiB for an array with shape (39, 6082624)
    and data type float64. Storing SQL only.

このとき空きメモリは 28.7GB あった。**総量不足ではなく連続領域の断片化**なので
「メモリを増やせば直る」類ではない。そもそも一度に確保しない形にする。

## 設計

- 銘柄をチャンクに割り、チャンクごとに `recompute_indicators()` を呼んで **yield** する
- 呼び出し側は受け取った DataFrame を逐次 Parquet へ書き出して捨てられる
- **計算の意味は `recompute_indicators()` に委ねる**（再計算の正しさの実装を二重に持たない）

## SPY の扱い

`recompute_indicators()` は RS の基準として SPY の系列を必要とする。
価格フレームを絞り込むときに **SPY を必ず含める**こと。落とすと、そのチャンクの
銘柄だけ RS 列が全て NULL になる（例外は出ないので気付けない）。
"""
from typing import Iterable, Iterator, List

import pandas as pd

from pipeline.parquet_recompute import recompute_indicators

# 1チャンクあたりの銘柄数。実測 6M行/3,236銘柄 なら 1銘柄あたり約1,880行なので、
# 200銘柄 ≒ 38万行 × 66列 ≒ 200MB 程度に収まる。
DEFAULT_CHUNK_SIZE = 200


def recompute_indicators_chunked(
    symbol_ids: Iterable[int],
    prices: pd.DataFrame,
    indicator_columns: List[str],
    spy_id: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Iterator[pd.DataFrame]:
    """銘柄をチャンクに割って T3 を再計算し、チャンクごとに yield する。

    分割しても結果は分割しない場合と完全に一致する（銘柄ごとの計算は独立）。

    Args:
        symbol_ids: 再計算する銘柄。
        prices:     `symbol_id` / `date` / OHLCV を持つ **全銘柄分**の DataFrame。
                    SPY の系列を RS 計算に使うため対象銘柄だけでは足りない。
        indicator_columns: 出力する指標列（既存 indicators の列順に合わせる）。
        spy_id:     SPY の symbol_id。
        chunk_size: 1チャンクあたりの銘柄数。

    Yields:
        `symbol_id` / `date` + 指標列 の DataFrame（チャンク1つ分）。
    """
    ids = [int(s) for s in symbol_ids]
    if not ids:
        return
    if chunk_size < 1:
        raise ValueError(f"chunk_size は1以上である必要があります: {chunk_size}")

    # 銘柄ごとの行を毎回スキャンすると O(全行 × 銘柄数) になる。
    # 一度だけグループ化して、チャンクごとに必要な分だけ結合する。
    by_sid = {sid: df for sid, df in prices.groupby("symbol_id", sort=False)}
    spy_px = by_sid.get(spy_id)

    for i in range(0, len(ids), chunk_size):
        batch = ids[i:i + chunk_size]
        frames = [by_sid[s] for s in batch if s in by_sid]
        if not frames:
            continue
        # **SPY を必ず含める。** 落とすとこのチャンクの RS が全て NULL になる
        if spy_px is not None and spy_id not in batch:
            frames.append(spy_px)
        px = pd.concat(frames, ignore_index=True)

        out = recompute_indicators(batch, px, indicator_columns, spy_id)
        if not out.empty:
            yield out
