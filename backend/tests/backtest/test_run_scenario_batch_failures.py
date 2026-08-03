"""シナリオバッチの失敗検知テスト（backtest/run_scenario_batch.py）

## 背景（2026-07-29 発見）

`run_single_mc_scenario` は例外時に**出力を書かず None を返すだけ**なので、
失敗した run の出力ディレクトリには**前回バッチの結果がそのまま残る**。
下流の集計はディレクトリの存在しか見ないため、古い結果が新しい結果に混ざる。

実例: 流動性ハード制約フィックス後の全戦略再実行で、B2 の full_position/run_9 だけ
他より1日以上古いタイムスタンプで取り残されていた。たまたま別のチェックで気づけたが、
通常の指標比較では発見できない。
"""

import os
import sys
import time

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for p in (project_root, backend_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from backtest.run_scenario_batch import (  # noqa: E402
    DEFAULT_PORTFOLIO,
    find_stale_run_outputs,
    report_batch_failures,
    resolve_portfolio_params,
)


def _make_run_dir(root, strat, model, run_idx, mtime=None):
    d = os.path.join(root, "output", "scenario", strat, model, f"run_{run_idx}")
    os.makedirs(d, exist_ok=True)
    f = os.path.join(d, "scenario_summary.json")
    with open(f, "w", encoding="utf-8") as fh:
        fh.write("{}")
    if mtime is not None:
        os.utime(f, (mtime, mtime))
    return d


# ---------------------------------------------------------------------------
# find_stale_run_outputs
# ---------------------------------------------------------------------------
def test_detects_leftover_output_from_previous_batch(tmp_path):
    """失敗した run に前回の出力が残っていたら検出する（これが本丸）"""
    batch_start = time.time()
    _make_run_dir(str(tmp_path), "B2", "full_position", 9, mtime=batch_start - 86400)

    stale = find_stale_run_outputs([("B2", "full_position", 9)], str(tmp_path), batch_start)

    assert len(stale) == 1
    strat, model, run_idx, path, mtime = stale[0]
    assert (strat, model, run_idx) == ("B2", "full_position", 9)
    assert mtime < batch_start


def test_no_output_dir_is_not_stale(tmp_path):
    """出力が無い＝集計に混ざらないので警告しない（ノイズにしない）"""
    stale = find_stale_run_outputs([("B2", "full_position", 9)], str(tmp_path), time.time())
    assert stale == []


def test_fresh_output_is_not_stale(tmp_path):
    """このバッチで書かれた出力は stale ではない。

    失敗と判定されても出力が新しい場合（部分的に書けた等）は古い結果の混入ではない。
    """
    batch_start = time.time() - 100
    _make_run_dir(str(tmp_path), "B2", "full_position", 9, mtime=time.time())

    stale = find_stale_run_outputs([("B2", "full_position", 9)], str(tmp_path), batch_start)
    assert stale == []


def test_empty_dir_is_not_stale(tmp_path):
    """空ディレクトリだけ残っているケースで落ちない"""
    os.makedirs(os.path.join(str(tmp_path), "output", "scenario", "B2", "full_position", "run_9"))
    stale = find_stale_run_outputs([("B2", "full_position", 9)], str(tmp_path), time.time())
    assert stale == []


def test_only_failed_runs_are_examined(tmp_path):
    """成功した run の古い出力は対象外（失敗した run だけを見る）"""
    batch_start = time.time()
    _make_run_dir(str(tmp_path), "B2", "full_position", 3, mtime=batch_start - 86400)

    stale = find_stale_run_outputs([("B2", "full_position", 9)], str(tmp_path), batch_start)
    assert stale == []


# ---------------------------------------------------------------------------
# report_batch_failures
# ---------------------------------------------------------------------------
def test_returns_true_when_no_failures(tmp_path, capsys):
    assert report_batch_failures([], 10, str(tmp_path), time.time()) is True
    assert "全 run が成功" in capsys.readouterr().out


def test_returns_false_and_reports_when_failures(tmp_path, capsys):
    """失敗があれば False を返す（呼び出し元が非ゼロ終了できるように）"""
    batch_start = time.time()
    _make_run_dir(str(tmp_path), "B2", "full_position", 9, mtime=batch_start - 86400)

    ok = report_batch_failures(
        [("B2", "full_position", 9), ("B2", "full_position", 4)],
        10, str(tmp_path), batch_start,
    )
    out = capsys.readouterr().out

    assert ok is False
    assert "2 件" in out
    assert "B2 / full_position" in out
    assert "前回の結果が残っており" in out, "汚染リスクが報告されていない"


def test_reports_no_contamination_when_outputs_absent(tmp_path, capsys):
    """失敗はしたが出力が残っていない場合は、その旨を明示する"""
    ok = report_batch_failures([("B2", "full_position", 9)], 10, str(tmp_path), time.time())
    out = capsys.readouterr().out

    assert ok is False
    assert "出力は残っていません" in out


# ---------------------------------------------------------------------------
# resolve_portfolio_params — ジョブ単位のポートフォリオ設定
#
# 旧実装は max_positions=8 / stop_loss_pct=-0.08 等を全ジョブ共通のハードコードで
# 渡しており、「戦略Xは保有数を絞った方が CAGR が伸びるか」のような
# ポートフォリオ構成側の比較検証が構造的にできなかった。
# ---------------------------------------------------------------------------
def test_defaults_when_no_override():
    """[job.portfolio] が無ければ従来のハードコード値と同じ。

    既存ジョブの挙動が変わらないことの保証。
    """
    assert resolve_portfolio_params({"name": "A"}) == DEFAULT_PORTFOLIO
    assert DEFAULT_PORTFOLIO["max_positions"] == 8
    assert DEFAULT_PORTFOLIO["stop_loss_pct"] == -0.08
    assert DEFAULT_PORTFOLIO["profit_target_pct"] == 0.20


def test_partial_override_keeps_other_defaults():
    p = resolve_portfolio_params({"name": "B2_narrow", "portfolio": {"max_positions": 4}})

    assert p["max_positions"] == 4
    assert p["stop_loss_pct"] == DEFAULT_PORTFOLIO["stop_loss_pct"], "指定していない項目が既定から外れた"


def test_unknown_key_raises():
    """キー名の誤記を黙って無視しない。

    無視すると「設定したのに効かない」まま比較検証を進めてしまう
    （D-2/I-6 と同型のサイレント失敗）。
    """
    with pytest.raises(ValueError, match="未知のキー"):
        resolve_portfolio_params({"name": "B2", "portfolio": {"max_position": 4}})


def test_returned_dict_is_isolated():
    """戻り値を書き換えても既定値が汚染されないこと（並列実行で共有されるため）"""
    p = resolve_portfolio_params({"name": "A"})
    p["max_positions"] = 99

    assert DEFAULT_PORTFOLIO["max_positions"] == 8
