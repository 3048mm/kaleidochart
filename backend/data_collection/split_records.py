"""上流が記録している株式分割を、レポート間で共有できる形に保存・読み出しする。

## なぜ要るか

分割記録は **手元のどこにも保存されていなかった**。DB にも Parquet にも splits 列は無く、
`actions=True` で取得しているのは `scripts/scan_split_consistency.py` の1箇所だけで、
検出に使ったあと捨てていた。

そのため `indicators/price_anomaly.py` の分類器は「その日に分割があったか」を知らないまま
段差を分類していた。結果、本物のデータ破損 (`IESC` / `WLFC`) がレポートに入っていながら
`market_wide` / `real_move` として捨てられていた（`doc/issue_list.md` P1）。

このモジュールは、その記録を2つの監査経路
（`scripts/scan_price_anomalies.py` = Parquet 全期間 /
`scripts/weekly_maintenance.py` = SQLite 730日）で共有するための永続化層。

## 置き場と形式の判断

- **DB スキーマにしない**。用途はレポート専用で、いつでも再取得できる。
  スキーマ変更にすると種別 B になりサンドボックス手順が要る（割に合わない）。
- 保存先は他の監査レポートと同じ `data/maintenance_reports/`。
  **`data/` は git 管理外**なので、新規チェックアウトには存在しない前提で扱う。

## 無い場合と壊れている場合で扱いを変える

| 状態 | 扱い | なぜ |
| :--- | :--- | :--- |
| ファイルが無い | ``None`` を返す | git 管理外なので、無いこと自体は異常ではない |
| JSON が壊れている / 形が違う | **例外** | `get_latest_master_files()` が読み込み失敗を握り潰して Parquet の全期間履歴を捨てた前例（2026-09-09 解決）と同じ型を作らない |

> [!IMPORTANT]
> **BOM を付けて書かない。** BOM 付き JSON は `json.load()` が
> `Unexpected UTF-8 BOM` で落ちる。呼び出し側が例外を握り潰していると
> 無関係な `TypeError` として現れて原因に辿り着けない
> （CLAUDE.md「Coding conventions」）。PowerShell で生成しないこと。
"""

from __future__ import annotations

import json
import os
from datetime import datetime

# 他の監査レポート（price_anomalies.csv 等）と同じディレクトリに置く
REPORT_DIRNAME = "maintenance_reports"
DEFAULT_FILENAME = "split_records.json"


def resolve_default_path(db_path: str) -> str:
    """`db_path` の隣の `maintenance_reports/` に置く既定パスを返す。

    `scan_price_anomalies.py` のレポート出力先と同じ導出（`db_path` の
    ディレクトリ基準）にそろえてある。
    """
    data_dir = os.path.dirname(os.path.abspath(db_path))
    return os.path.join(data_dir, REPORT_DIRNAME, DEFAULT_FILENAME)


def save_split_records(path: str, splits: dict, *, start: str, years: float,
                       tickers_fetched: int, failed: list) -> str:
    """分割記録を JSON で保存する。

    Args:
        path: 出力先。親ディレクトリが無ければ作る。
        splits: ``{ticker: [(split_date, factor), ...]}``。
        start / years: 取得のカバー期間。**これが無いと「記録に無い」と
            「期間外で見ていない」を読み手が区別できない。**
        tickers_fetched: 取得を試みた銘柄数。
        failed: 取得できなかった銘柄。**黙って落とさず記録する。**

    Returns:
        書き出したパス。
    """
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "start": start,
        "years": years,
        "tickers_fetched": int(tickers_fetched),
        "failed": list(failed or []),
        # タプルは JSON では配列になる。読み出し側で (date, factor) へ戻す
        "splits": {t: [[str(d), float(f)] for d, f in pairs]
                   for t, pairs in (splits or {}).items() if pairs},
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # **一時ファイルへ書いてから差し替える。** 直接上書きすると、書き込み中の
    # 中断（Ctrl+C・電源断）で**切り詰まった JSON が残る**。読み出しは fail-loud
    # なので、そうなると価格アノマリー・週次メンテの両方が手動削除まで止まる。
    tmp = f"{path}.tmp"
    # BOM を付けない・改行は LF（Windows の既定 CRLF に流されない）
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)      # 同一ボリューム内なら原子的
    return path


def load_split_records(path: str) -> dict | None:
    """分割記録を読み出す。

    Returns:
        保存した dict。**ファイルが無ければ ``None``**（異常ではない）。

    Raises:
        ValueError: JSON が壊れている、または `splits` を持たない。
            **「記録ゼロ」に読み替えない。** 静かに従来分類へ戻ると、
            ゲートがあるように見えて何も見ていない状態になる。
    """
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"分割記録 {path} を JSON として読めません: {e}") from e
    if not isinstance(data, dict) or "splits" not in data:
        raise ValueError(
            f"分割記録 {path} の形式が想定と違います（'splits' キーが無い）。"
            f" 再生成: scripts/scan_split_consistency.py")
    return data


def split_map(records: dict | None) -> dict:
    """照合に使う ``{ticker: [(date, factor)]}`` を取り出す。

    記録が無ければ空 dict を返す。**呼び出し側に `None` 分岐を強いない**
    （分岐が増えるほど「記録が無いのに黙って通す」経路が生まれる）。
    """
    if not records:
        return {}
    return {t: [(str(d), float(f)) for d, f in pairs]
            for t, pairs in (records.get("splits") or {}).items()}
