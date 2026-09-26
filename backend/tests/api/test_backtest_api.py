import os
import json
import pytest
from fastapi.testclient import TestClient

# Import our app from api.server.
from api.server import app

@pytest.fixture
def mock_output_dir(tmp_path, monkeypatch):
    """
    Creates a mock output directory structure with fake backtest results.
    Matches the actual backtest CSV/JSON output formats.
    """
    # Create two scenario directories
    sc1_dir = tmp_path / "scenario_alpha"
    sc1_dir.mkdir()
    
    sc2_dir = tmp_path / "scenario_beta"
    sc2_dir.mkdir()
    
    # 1. Summary JSON (Matches actual stocktool format, lacks cagr directly to test dynamic calculation)
    sc1_summary = {
        "start_date": "2026-05-01",
        "end_date": "2026-05-20",
        "initial_capital": 10000.0,
        "final_capital": 10255.0,
        "profit_factor": 1.25,
        "max_drawdown": {
            "pct": 18.0,
            "amount": 1800.0
        },
        "win_rate": 0.48,
        "total_trades": 80,
        "yearly_performance": {
            "2026": {
                "total_trades": 80,
                "win_rate": 0.48,
                "net_pnl": 255.0,
                "avg_pnl_pct": 0.5,
                "profit_factor": 1.25,
                "spy_return_pct": 3.0
            }
        }
    }
    sc2_summary = {
        "start_date": "2026-05-01",
        "end_date": "2026-05-20",
        "initial_capital": 10000.0,
        "final_capital": 14020.0,
        "profit_factor": 1.43,
        "max_drawdown": {
            "pct": 12.0,
            "amount": 1200.0
        },
        "win_rate": 0.55,
        "total_trades": 120,
        "yearly_performance": {
            "2026": {
                "total_trades": 120,
                "win_rate": 0.55,
                "net_pnl": 4020.0,
                "avg_pnl_pct": 3.35,
                "profit_factor": 1.43,
                "spy_return_pct": 5.2
            }
        }
    }
    
    with open(sc1_dir / "scenario_summary.json", "w", encoding="utf-8") as f:
        json.dump(sc1_summary, f)
    with open(sc2_dir / "scenario_summary.json", "w", encoding="utf-8") as f:
        json.dump(sc2_summary, f)
        
    # 2. Equity Curve CSV (Matches actual stocktool headers 'total_equity', 'cash', 'date')
    sc1_equity = "date,cash,invested,total_equity,positions,held_tickers\n2026-05-20,10000.0,0.0,10000.0,0,\n2026-05-21,9800.0,400.0,10200.0,1,AAPL\n"
    sc2_equity = "date,cash,invested,total_equity,positions,held_tickers\n2026-05-20,10000.0,0.0,10000.0,0,\n2026-05-21,9500.0,1000.0,10500.0,1,MSFT\n"
    
    with open(sc1_dir / "scenario_equity_curve.csv", "w", encoding="utf-8") as f:
        f.write(sc1_equity)
    with open(sc2_dir / "scenario_equity_curve.csv", "w", encoding="utf-8") as f:
        f.write(sc2_equity)
        
    # 3. Trade Logs CSV (Matches actual stocktool headers)
    sc1_trades = "ticker,entry_date,exit_date,entry_price,exit_price,shares,amount,exit_reason,pnl_pct\nAAPL,2026-05-19,2026-05-20,150.0,150.0,10,1500.0,strategy,0.0\n"
    sc2_trades = "ticker,entry_date,exit_date,entry_price,exit_price,shares,amount,exit_reason,pnl_pct\nMSFT,2026-05-19,2026-05-20,300.0,300.0,5,1500.0,strategy,0.0\nMSFT,2026-05-20,2026-05-21,300.0,320.0,5,1600.0,stop_loss,0.0667\n"
    
    with open(sc1_dir / "scenario_trade_logs.csv", "w", encoding="utf-8") as f:
        f.write(sc1_trades)
    with open(sc2_dir / "scenario_trade_logs.csv", "w", encoding="utf-8") as f:
        f.write(sc2_trades)
        
    # Set different modification times to test 'latest' resolution.
    os.utime(str(sc1_dir), (1700000000, 1700000000))
    os.utime(str(sc2_dir), (1700000050, 1700000050))
    
    import api.backtest_router
    monkeypatch.setattr(api.backtest_router, "OUTPUT_DIR", str(tmp_path))
        
    return tmp_path

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def test_get_scenarios(client, mock_output_dir):
    """Test GET /api/backtest/scenarios returns a list of scenario directory names."""
    resp = client.get("/api/backtest/scenarios")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert "scenario_alpha" in data
    assert "scenario_beta" in data

def test_get_scenario_summary_specific(client, mock_output_dir):
    """Test GET /api/backtest/scenario/{name}/summary returns the JSON summary."""
    resp = client.get("/api/backtest/scenario/scenario_alpha/summary")
    assert resp.status_code == 200
    data = resp.json()
    # CAGR calculated dynamically: (10255/10000)^(365.25/19) - 1. Just check it has some value.
    assert data["cagr"] > 0
    assert data["profit_factor"] == 1.25
    assert data["max_drawdown"] == -0.18  # Convert 18.0 (%) pct to -0.18
    # scenario_alpha の JSON には avg_trade_pnl_pct が無いので、
    # 同ディレクトリの scenario_trade_logs.csv (pnl_pct=0.0 の1件) からフォールバック算出される
    assert data["avg_trade_pnl_pct"] == 0.0

def test_get_scenario_summary_cagr_in_json_is_percent_converted_to_ratio(client, mock_output_dir):
    """scenario_summary.json の `cagr` はパーセント（scenario_reporter が ×100 で保存）。

    API は他の経路（cagr 欠落時の動的計算・Monte Carlo 集計）と同じく**比率**で返す。
    パーセントのまま返すと、フロント（`summary.cagr * 100`）で Run 個別タブだけ
    100倍表示（7.29% → 729%）になる。
    """
    sc = mock_output_dir / "scenario_with_cagr"
    sc.mkdir()
    (sc / "scenario_summary.json").write_text(json.dumps({
        "start_date": "2022-01-01", "end_date": "2026-03-26",
        "initial_capital": 100000.0, "final_capital": 134684.02,
        "cagr": 7.29,  # パーセント表記（実ファイルと同じ）
        "profit_factor": 1.36, "win_rate": 0.35, "total_trades": 502,
        "max_drawdown": {"pct": 49.69, "amount": 51044.62},
    }), encoding="utf-8")
    resp = client.get("/api/backtest/scenario/scenario_with_cagr/summary")
    assert resp.status_code == 200
    assert resp.json()["cagr"] == pytest.approx(0.0729)


def test_get_scenario_trades_pnl_pct_over_100pct_is_converted(client, mock_output_dir):
    """scenario_trade_logs.csv の pnl_pct は常に比率（scenario_portfolio が比率で書く）。

    旧実装は「|x| < 1 なら比率、それ以外はパーセント」と推測していたため、
    +100% 以上の取引（比率 >= 1）だけ ×100 されず 1/100 で表示された
    （実例: ABVX +676.9% が +6.77% と表示）。
    """
    sc = mock_output_dir / "scenario_big_winner"
    sc.mkdir()
    (sc / "scenario_summary.json").write_text(json.dumps({
        "start_date": "2025-07-01", "end_date": "2025-07-31",
        "initial_capital": 100000.0, "final_capital": 227000.0,
        "total_trades": 3, "win_rate": 0.67, "profit_factor": 2.0,
        "max_drawdown": {"pct": 5.0, "amount": 5000.0},
    }), encoding="utf-8")
    (sc / "scenario_trade_logs.csv").write_text(
        "ticker,entry_date,exit_date,entry_price,exit_price,shares,amount,exit_reason,pnl_pct\n"
        "ABVX,2025-07-14,2025-07-23,8.83,68.6,1417,12512.1,sma50_atr_exit,6.768969\n"
        "DBL,2025-07-01,2025-07-10,10.0,20.0,100,1000.0,sma50_atr_exit,1.0\n"
        "SOC,2025-07-18,2025-07-23,31.69,28.11,591,18728.8,stop_loss,-0.112969\n",
        encoding="utf-8")
    resp = client.get("/api/backtest/scenario/scenario_big_winner/trades")
    assert resp.status_code == 200
    by_ticker = {t["ticker"]: t["pnl_pct"] for t in resp.json() if t["action"] == "SELL"}
    assert by_ticker["ABVX"] == pytest.approx(676.8969)
    assert by_ticker["DBL"] == pytest.approx(100.0)  # 境界: ちょうど2倍
    assert by_ticker["SOC"] == pytest.approx(-11.2969)


def test_get_scenario_summary_latest(client, mock_output_dir):
    """Test GET /api/backtest/scenario/latest/summary dynamically resolves to the latest folder (scenario_beta)."""
    resp = client.get("/api/backtest/scenario/latest/summary")
    assert resp.status_code == 200
    data = resp.json()
    # scenario_beta has higher mtime, so it should be loaded
    assert data["cagr"] > 0
    assert data["profit_factor"] == 1.43
    assert data["max_drawdown"] == -0.12  # Convert 12.0 (%) pct to -0.12
    assert data["yearly_performance"] is not None
    assert data["yearly_performance"]["2026"]["spy_return_pct"] == 5.2
    # scenario_beta の CSV フォールバック: pnl_pct=[0.0, 0.0667] の平均 * 100
    expected_avg = round((0.0 + 0.0667) / 2 * 100, 2)
    assert data["avg_trade_pnl_pct"] == pytest.approx(expected_avg)

def test_get_scenario_equity(client, mock_output_dir):
    """Test GET /api/backtest/scenario/{name}/equity returns parsed CSV data as JSON."""
    resp = client.get("/api/backtest/scenario/scenario_beta/equity")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["date"] == "2026-05-20"
    assert float(data[0]["equity"]) == 10000.0
    assert float(data[1]["equity"]) == 10500.0

def test_get_scenario_trades(client, mock_output_dir):
    """Test GET /api/backtest/scenario/{name}/trades returns parsed CSV trades."""
    resp = client.get("/api/backtest/scenario/scenario_beta/trades")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["ticker"] == "MSFT"
    assert data[0]["action"] == "SELL"
    assert data[1]["ticker"] == "MSFT"
    assert data[1]["action"] == "SELL"
    # PnL multiplied by 100
    assert float(data[1]["pnl_pct"]) == 6.67

def test_get_scenario_not_found(client, mock_output_dir):
    """Test GET of a non-existent scenario returns 404."""
    resp = client.get("/api/backtest/scenario/scenario_non_existent/summary")
    assert resp.status_code == 404
    assert "detail" in resp.json()


# ============================================================
# avg_trade_pnl_pct: CSV フォールバックヘルパーの単体テスト
# ============================================================

from api.backtest_router import _avg_trade_pnl_pct_from_logs, _resolve_avg_trade_pnl_pct


def test_avg_trade_pnl_pct_from_logs_computes_average(tmp_path):
    """CSV の pnl_pct から % 換算した単純平均を算出する。"""
    csv_content = (
        "ticker,entry_date,exit_date,entry_price,exit_price,shares,amount,exit_reason,pnl_pct\n"
        "AAPL,2026-01-01,2026-01-05,100.0,110.0,10,1000.0,strategy,0.10\n"
        "MSFT,2026-01-02,2026-01-06,200.0,190.0,10,2000.0,stop_loss,-0.05\n"
    )
    (tmp_path / "scenario_trade_logs.csv").write_text(csv_content, encoding="utf-8")

    result = _avg_trade_pnl_pct_from_logs(str(tmp_path))
    # (0.10 + -0.05) / 2 * 100 = 2.5
    assert result == pytest.approx(2.5)


def test_avg_trade_pnl_pct_from_logs_missing_csv_returns_none(tmp_path):
    """CSV が存在しない場合は例外を出さず None を返す。"""
    result = _avg_trade_pnl_pct_from_logs(str(tmp_path))
    assert result is None


def test_avg_trade_pnl_pct_from_logs_malformed_rows_are_skipped(tmp_path):
    """壊れた行 (pnl_pct が非数値・空) はスキップして残りから算出する。"""
    csv_content = (
        "ticker,entry_date,exit_date,entry_price,exit_price,shares,amount,exit_reason,pnl_pct\n"
        "AAPL,2026-01-01,2026-01-05,100.0,110.0,10,1000.0,strategy,0.20\n"
        "MSFT,2026-01-02,2026-01-06,200.0,190.0,10,2000.0,stop_loss,not_a_number\n"
        "GOOG,2026-01-03,2026-01-07,300.0,300.0,10,3000.0,unknown,\n"
    )
    (tmp_path / "scenario_trade_logs.csv").write_text(csv_content, encoding="utf-8")

    result = _avg_trade_pnl_pct_from_logs(str(tmp_path))
    # 有効行は AAPL の 0.20 のみ -> 20.0
    assert result == pytest.approx(20.0)


def test_avg_trade_pnl_pct_from_logs_empty_trades_returns_none(tmp_path):
    """ヘッダーのみで取引が0件の CSV は None を返す。"""
    csv_content = "ticker,entry_date,exit_date,entry_price,exit_price,shares,amount,exit_reason,pnl_pct\n"
    (tmp_path / "scenario_trade_logs.csv").write_text(csv_content, encoding="utf-8")

    result = _avg_trade_pnl_pct_from_logs(str(tmp_path))
    assert result is None


def test_resolve_avg_trade_pnl_pct_prefers_json_value(tmp_path):
    """JSON に avg_trade_pnl_pct があれば CSV を読まずそれを使う。"""
    # CSV が無くても JSON の値が優先されることを確認
    summary_data = {"avg_trade_pnl_pct": 5.14}
    result = _resolve_avg_trade_pnl_pct(str(tmp_path), summary_data)
    assert result == 5.14


def test_resolve_avg_trade_pnl_pct_falls_back_to_csv(tmp_path):
    """JSON に無ければ CSV から算出する。"""
    csv_content = (
        "ticker,entry_date,exit_date,entry_price,exit_price,shares,amount,exit_reason,pnl_pct\n"
        "AAPL,2026-01-01,2026-01-05,100.0,110.0,10,1000.0,strategy,0.10\n"
    )
    (tmp_path / "scenario_trade_logs.csv").write_text(csv_content, encoding="utf-8")

    summary_data = {}  # avg_trade_pnl_pct が無い旧形式の想定
    result = _resolve_avg_trade_pnl_pct(str(tmp_path), summary_data)
    assert result == pytest.approx(10.0)


# ============================================================
# avg_trade_pnl_pct: Monte Carlo 集計（B案 = run ごとの平均の平均）
# ============================================================

@pytest.fixture
def mc_scenario_dir(tmp_path, monkeypatch):
    """
    output/scenario/A/full_position/run_0, run_1 の3階層構造を模した Monte Carlo フィクスチャ。
    run 間で取引数を非対称にし、A案（全トレード合算）と B案（run 平均の平均）で
    異なる値になるようにする。
    """
    base = tmp_path / "scenario" / "A" / "full_position"

    run0 = base / "run_0"
    run0.mkdir(parents=True)
    run0_summary = {
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "initial_capital": 100000.0,
        "final_capital": 110000.0,
        "profit_factor": 1.5,
        "max_drawdown": {"pct": 10.0, "amount": 10000.0},
        "win_rate": 1.0,
        "total_trades": 1,
    }
    with open(run0 / "scenario_summary.json", "w", encoding="utf-8") as f:
        json.dump(run0_summary, f)
    # run_0: 1件のみ, pnl_pct=0.10 -> avg 10.0%
    (run0 / "scenario_trade_logs.csv").write_text(
        "ticker,entry_date,exit_date,entry_price,exit_price,shares,amount,exit_reason,pnl_pct\n"
        "AAPL,2026-01-01,2026-01-05,100.0,110.0,10,1000.0,strategy,0.10\n",
        encoding="utf-8"
    )

    run1 = base / "run_1"
    run1.mkdir(parents=True)
    run1_summary = {
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "initial_capital": 100000.0,
        "final_capital": 90000.0,
        "profit_factor": 0.5,
        "max_drawdown": {"pct": 20.0, "amount": 20000.0},
        "win_rate": 0.0,
        "total_trades": 3,
    }
    with open(run1 / "scenario_summary.json", "w", encoding="utf-8") as f:
        json.dump(run1_summary, f)
    # run_1: 3件, pnl_pct=-0.10 x3 -> avg -10.0%
    (run1 / "scenario_trade_logs.csv").write_text(
        "ticker,entry_date,exit_date,entry_price,exit_price,shares,amount,exit_reason,pnl_pct\n"
        "MSFT,2026-01-01,2026-01-05,200.0,180.0,10,2000.0,stop_loss,-0.10\n"
        "GOOG,2026-01-02,2026-01-06,200.0,180.0,10,2000.0,stop_loss,-0.10\n"
        "AMZN,2026-01-03,2026-01-07,200.0,180.0,10,2000.0,stop_loss,-0.10\n",
        encoding="utf-8"
    )

    import api.backtest_router
    monkeypatch.setattr(api.backtest_router, "OUTPUT_DIR", str(tmp_path))
    return tmp_path


def test_get_scenario_summary_monte_carlo_uses_b_case_average(client, mc_scenario_dir):
    """
    Monte Carlo 集計は B案（run ごとの平均を出してから、その平均）を使うこと。
    A案（全トレード合算の単純平均）とは異なる値になる非対称フィクスチャで検証する。
    """
    resp = client.get("/api/backtest/scenario/A__full_position/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_monte_carlo"] is True

    # B案: run_0 の平均(10.0) と run_1 の平均(-10.0) の平均 = 0.0
    assert data["avg_trade_pnl_pct_avg"] == pytest.approx(0.0)
    assert data["avg_trade_pnl_pct"] == pytest.approx(0.0)

    # A案（全トレード合算）だと (0.10 - 0.10*3) / 4 * 100 = -5.0 になり、B案とは異なる値になるはず
    a_case_value = ((0.10 - 0.10 - 0.10 - 0.10) / 4) * 100
    assert data["avg_trade_pnl_pct_avg"] != pytest.approx(a_case_value)

# ============================================================
# Run Info（PERIOD / TRADING DAYS / INITIAL CAP / TAX RATE）
# フロントの折りたたみ Run Info バーが両タブで同じ項目を出せることを担保する
# ============================================================

from api.backtest_router import _count_trading_days, _resolve_consider_tax
from backtest.common_constraints import load_tax_rate


def test_scenario_summary_exposes_run_info(client, mock_output_dir):
    """単発 run の summary が Run Info 4項目 + 総リターンを返すこと。"""
    resp = client.get("/api/backtest/scenario/scenario_alpha/summary")
    assert resp.status_code == 200
    data = resp.json()

    assert data["start_date"] == "2026-05-01"
    assert data["end_date"] == "2026-05-20"
    assert data["initial_capital"] == 10000.0
    # scenario_equity_curve.csv のデータ行数（ヘッダを除く2行）
    assert data["trading_days"] == 2
    # run_params に consider_tax が無い旧 run は backtest_config.toml へフォールバックする
    assert data["consider_tax"] == pytest.approx(load_tax_rate())
    # (10255 / 10000 - 1) * 100
    assert data["total_return_pct"] == pytest.approx(2.55)


def test_monte_carlo_summary_exposes_run_info(client, mc_scenario_dir):
    """MC グループの集約 summary も Run Info を返すこと（先頭 run の設定が代表値）。"""
    resp = client.get("/api/backtest/scenario/A__full_position/summary")
    assert resp.status_code == 200
    data = resp.json()

    assert data["is_monte_carlo"] is True
    assert data["start_date"] == "2026-01-01"
    assert data["end_date"] == "2026-12-31"
    assert data["initial_capital"] == 100000.0
    assert data["consider_tax"] == pytest.approx(load_tax_rate())
    # 総リターンは final_capital の幾何平均 sqrt(110000 * 90000) = 99498.74 に対して 約 -0.50%
    expected_geo_final = (110000.0 * 90000.0) ** 0.5
    expected_return_pct = (expected_geo_final / 100000.0 - 1.0) * 100.0
    assert data["total_return_pct"] == pytest.approx(expected_return_pct)
    assert data["final_capital"] == pytest.approx(expected_geo_final)
    assert data["final_capital_geo"] == pytest.approx(expected_geo_final)
    assert data["final_capital_avg"] == pytest.approx(100000.0)
    assert data["final_capital_med"] == pytest.approx(100000.0)

    # CAGR: 代表値は幾何平均
    assert data["cagr"] == pytest.approx(data["cagr_geo"])
    assert data["cagr_geo"] < data["cagr_avg"]  # AM-GM 不等式: 幾何平均 < 相加平均


def test_get_scenario_summary_monte_carlo_cagr_geometric_mean(client, tmp_path, monkeypatch):
    """3本の非対称 MC run で幾何平均・中央値・相加平均が正確に算出されることの検証。"""
    import api.backtest_router
    monkeypatch.setattr(api.backtest_router, "OUTPUT_DIR", str(tmp_path))

    base = tmp_path / "scenario" / "B1" / "full_position"
    base.mkdir(parents=True)

    # 1年（365.25日）で計算を簡単にする
    # run0: 100000 -> 144000 (CAGR +44%)
    # run1: 100000 -> 100000 (CAGR 0%)
    # run2: 100000 -> 64000  (CAGR -36%)
    caps = [144000.0, 100000.0, 64000.0]
    for idx, f_cap in enumerate(caps):
        r_dir = base / f"run_{idx}"
        r_dir.mkdir(parents=True)
        summary_data = {
            "start_date": "2025-01-01",
            "end_date": "2026-01-01",
            "initial_capital": 100000.0,
            "final_capital": f_cap,
            "profit_factor": 1.2,
            "max_drawdown": {"pct": 10.0, "amount": 10000.0},
            "win_rate": 0.5,
            "total_trades": 10,
        }
        with open(r_dir / "scenario_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary_data, f)
        (r_dir / "scenario_trade_logs.csv").write_text("ticker\n", encoding="utf-8")

    resp = client.get("/api/backtest/scenario/B1__full_position/summary")
    assert resp.status_code == 200
    data = resp.json()

    # 相加平均 CAGR: (0.44 + 0.0 - 0.36) / 3 = 0.08 / 3 ≈ 0.02667
    # 幾何平均 CAGR: (1.44 * 1.0 * 0.64) ** (1/3) - 1 = (0.9216) ** (1/3) - 1 ≈ 0.97314 - 1 = -0.02686
    # 中央値 CAGR: 0.0
    import math
    expected_geo = (1.44 * 1.0 * 0.64) ** (1.0 / 3.0) - 1.0
    expected_avg = (0.44 + 0.0 - 0.36) / 3.0
    expected_med = 0.0

    # 1年が厳密には (2026-01-01 - 2025-01-01).days / 365.25 = 365 / 365.25 なので
    # backtest_router の years 計算に合わせる
    years = 365 / 365.25
    cagr_0 = (144000.0 / 100000.0) ** (1 / years) - 1.0
    cagr_1 = (100000.0 / 100000.0) ** (1 / years) - 1.0
    cagr_2 = (64000.0 / 100000.0) ** (1 / years) - 1.0
    exact_geo = math.exp((math.log(1 + cagr_0) + math.log(1 + cagr_1) + math.log(1 + cagr_2)) / 3.0) - 1.0
    exact_avg = (cagr_0 + cagr_1 + cagr_2) / 3.0
    exact_med = cagr_1

    assert data["cagr"] == pytest.approx(exact_geo)
    assert data["cagr_geo"] == pytest.approx(exact_geo)
    assert data["cagr_avg"] == pytest.approx(exact_avg)
    assert data["cagr_med"] == pytest.approx(exact_med)
    assert data["cagr_max"] == pytest.approx(cagr_0)
    assert data["cagr_min"] == pytest.approx(cagr_2)
    # AM-GM 不等式: 幾何平均 < 相加平均
    assert data["cagr_geo"] < data["cagr_avg"]


def test_get_scenario_summary_monte_carlo_cagr_bankruptcy_guard(client, tmp_path, monkeypatch):
    """破産（final_capital <= 0 や cagr <= -1.0）の run が混ざっても例外にならず安全に下限ガードが働くこと。"""
    import api.backtest_router
    monkeypatch.setattr(api.backtest_router, "OUTPUT_DIR", str(tmp_path))

    base = tmp_path / "scenario" / "B2" / "full_position"
    base.mkdir(parents=True)

    caps = [120000.0, 0.0]  # run1 は完全破産
    for idx, f_cap in enumerate(caps):
        r_dir = base / f"run_{idx}"
        r_dir.mkdir(parents=True)
        summary_data = {
            "start_date": "2025-01-01",
            "end_date": "2026-01-01",
            "initial_capital": 100000.0,
            "final_capital": f_cap,
            "profit_factor": 1.0,
            "max_drawdown": {"pct": 100.0, "amount": 100000.0},
            "win_rate": 0.0,
            "total_trades": 5,
        }
        with open(r_dir / "scenario_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary_data, f)
        (r_dir / "scenario_trade_logs.csv").write_text("ticker\n", encoding="utf-8")

    resp = client.get("/api/backtest/scenario/B2__full_position/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_monte_carlo"] is True
    # エラーで落ちずに幾何平均・代表値が計算されていること
    assert data["cagr_geo"] is not None
    assert data["final_capital_geo"] is not None
    assert data["cagr_geo"] <= data["cagr_avg"]


def test_resolve_consider_tax_prefers_run_params(monkeypatch):
    """run_params に consider_tax があれば config より優先する（実行時の値が正）。"""
    assert _resolve_consider_tax({"run_params": {"consider_tax": 0.35}}) == pytest.approx(0.35)
    # 0.0（税なし）も「記録された値」として尊重し、config にフォールバックしない
    assert _resolve_consider_tax({"run_params": {"consider_tax": 0.0}}) == pytest.approx(0.0)


def test_count_trading_days_missing_csv_returns_none(tmp_path):
    """equity CSV が無い run では例外を出さず None を返す。"""
    assert _count_trading_days(str(tmp_path)) is None
