"""手で適用した価格補正の台帳を読み、検証する。

## なぜ台帳が要るか

上流（Yahoo）が調整しなかった分割や、`ffill` が捏造した行の補正は**人が判断して
適用する**。ところが全期間再構築（`run/tool/refresh_All.bat`）は Yahoo から取り直すので、
**適用した補正はすべて巻き戻る**。`parquet-data-quality` SKILL §9 の再構築後
チェックリストにある項目4「`truncate_symbol_history.py` を再適用」と同じ性質の話。

2026-08-25 の時点で、この知識は**git のコミットログにしか無かった**。
再構築後に「`BYND` は 2026-08-13 より前を ×30」と知っている人がいなくなる。

台帳（既定 `data/price_corrections.toml`）に記録しておき、
`backend/scripts/reapply_corrections.py` で再生する。

> [!IMPORTANT]
> **2026-08-06 に「不要」と判断した補正テーブルとは別物。**
> あちらは「上流の分割記録から**自動で補正値を推定して当てる**仕組み」で、
> 推定値を本番データに書き込むリスクから見送った
> （`doc/completed/split_anomaly_noise_reduction_plan.md` §8）。
> **本台帳は人が適用済みの補正を記録して再生するだけで、新しい値を推定しない。**

## 書式

```toml
[[correction]]
ticker  = "BYND"
kind    = "split"            # `adjust_symbol_split.py` を呼ぶ
before  = "2026-08-13"       # この日より前を補正する
factor  = 30.0               # 1:30 併合なら 30、2:1 分割なら 0.5
reason  = "1:30 併合 (2026-08-14 ET)。Yahoo の系列が歯抜けで取り直しでは直らない"
applied = "2026-08-25"       # 任意。最初に適用した日

[[correction]]
ticker  = "AVB"
kind    = "fabricated_rows"  # `delete_symbol_rows.py --auto-fabricated` を呼ぶ
from    = "2026-08-15"
to      = "2026-08-23"
reason  = "上流欠損を ffill が埋めた行（OHLC 全同値・出来高0）"
applied = "2026-08-25"
```

**適用順は記載順**。並べ替えない（依存関係を人が決められるように）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# 対応する補正の種別。**増やすときは `reapply_corrections.py` の分岐も足すこと。**
KINDS = ("split", "fabricated_rows")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CorrectionError(ValueError):
    """台帳の記述が不正。**再適用を始める前に落とす。**"""


@dataclass(frozen=True)
class Correction:
    """台帳の1エントリ。"""

    ticker: str
    kind: str
    reason: str
    before: str | None = None        # split
    factor: float | None = None      # split
    date_from: str | None = None     # fabricated_rows
    date_to: str | None = None       # fabricated_rows
    applied: str | None = None

    def describe(self) -> str:
        if self.kind == "split":
            return f"{self.ticker}  {self.before} より前を ×{self.factor}"
        return f"{self.ticker}  {self.date_from} 〜 {self.date_to} の捏造行を削除"

    def dedup_key(self) -> tuple:
        """二重登録の判定キー。同じ銘柄が複数回分割することはあるので境界も含める。"""
        return (self.ticker, self.kind, self.before, self.date_from, self.date_to)


def _require(raw: dict, key: str, where: str):
    v = raw.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        raise CorrectionError(f"{where}: `{key}` が必要です。")
    return v


def _check_date(value: str, key: str, where: str) -> str:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise CorrectionError(
            f"{where}: `{key}` の日付は YYYY-MM-DD で書いてください（{value!r}）。")
    return value


def parse_corrections(raw_entries: list[dict]) -> list[Correction]:
    """台帳の生データを検証して `Correction` のリストにする。

    Raises:
        CorrectionError: 必須項目の欠落・不正な値・重複。
            **1件でも不正なら何も適用しない**（部分適用の方が状態を追いにくい）。
    """
    out: list[Correction] = []
    seen: dict[tuple, int] = {}

    for i, raw in enumerate(raw_entries, 1):
        ticker = _require(raw, "ticker", f"correction[{i}]")
        where = f"correction[{i}] ({ticker})"

        kind = _require(raw, "kind", where)
        if kind not in KINDS:
            raise CorrectionError(
                f"{where}: `kind` が不明です（{kind!r}）。"
                f" 使えるのは {', '.join(KINDS)} です。")

        # 理由の記録がこの台帳の存在意義。空欄を許すと数ヶ月後に判断を再現できない。
        reason = _require(raw, "reason", where)

        before = factor = date_from = date_to = None
        if kind == "split":
            before = _check_date(_require(raw, "before", where), "before", where)
            factor = _require(raw, "factor", where)
            if not isinstance(factor, (int, float)) or factor <= 0:
                raise CorrectionError(
                    f"{where}: `factor` は正の数で書いてください（{factor!r}）。")
            if float(factor) == 1.0:
                raise CorrectionError(
                    f"{where}: `factor` が 1 では何も変わりません。"
                    f" 意図した比率を書いてください。")
            factor = float(factor)
        else:
            date_from = _check_date(_require(raw, "from", where), "from", where)
            date_to = _check_date(_require(raw, "to", where), "to", where)
            if date_from > date_to:
                raise CorrectionError(
                    f"{where}: 範囲が逆です（from={date_from} > to={date_to}）。")

        applied = raw.get("applied")
        if applied is not None:
            applied = _check_date(str(applied), "applied", where)

        c = Correction(ticker=str(ticker), kind=kind, reason=str(reason).strip(),
                       before=before, factor=factor,
                       date_from=date_from, date_to=date_to, applied=applied)

        key = c.dedup_key()
        if key in seen:
            raise CorrectionError(
                f"{where}: correction[{seen[key]}] と重複しています（{c.describe()}）。"
                f" 同じ補正を2回並べると再適用で二重にかかる危険があります。")
        seen[key] = i
        out.append(c)

    return out


def load_corrections(path: str) -> list[Correction]:
    """台帳ファイルを読む。

    ファイルが無ければ空リストを返す（台帳をまだ作っていない環境でも
    再適用スクリプトが落ちないように）。
    """
    if not os.path.exists(path):
        return []
    import tomli

    with open(path, "rb") as f:
        data = tomli.load(f)
    return parse_corrections(data.get("correction", []))
