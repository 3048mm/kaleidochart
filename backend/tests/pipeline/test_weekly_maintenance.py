"""
Tests for weekly_maintenance.py script operations.
"""
import os
import sys
import subprocess
import pytest
import sqlite3
from datetime import date, timedelta

from db.models import Base, Symbol, DailyPrice, Indicator, ThemeConstituent
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Paths
PYTHON_EXEC = sys.executable
SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts",
    "weekly_maintenance.py"
)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
LOCK_FILE = os.path.join(PROJECT_ROOT, "update_pipeline.lock")


@pytest.fixture
def temp_lock(tmp_path):
    """テスト専用のロックファイル。

    本番の update_pipeline.lock を使うと、日次パイプラインの実行中に
    テストが必ず失敗する（2026-07-31 に発生。パイプラインが5時間超保持し、
    サブプロセステスト5件が軒並み落ちた）。テストは本番の実行状況から独立させる。
    """
    return str(tmp_path / "test_pipeline.lock")


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary real sqlite file for testing VACUUM and REINDEX."""
    db_file = tmp_path / "test_maintenance.db"
    
    # Initialize basic schemas
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    engine.dispose()
    
    yield str(db_file)
    
    if db_file.exists():
        try:
            os.remove(db_file)
        except:
            pass


class TestWeeklyMaintenancePhysical:
    """Tests the CLI parameters and physical maintenance steps."""

    def test_dry_run_success(self, temp_db, temp_lock):
        """Verify that --dry-run completes successfully without raising errors."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        
        result = subprocess.run(
            [PYTHON_EXEC, SCRIPT_PATH, "--dry-run", "--db-path", temp_db,
             "--lock-file", temp_lock, "--skip-sec"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        
        assert result.returncode == 0
        assert "Database integrity check: OK" in result.stdout
        assert "Dry-run mode: skipping REINDEX and VACUUM" in result.stdout

    def test_fix_mode_success(self, temp_db, temp_lock):
        """Verify that --fix runs REINDEX and VACUUM on the database."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        
        result = subprocess.run(
            [PYTHON_EXEC, SCRIPT_PATH, "--fix", "--db-path", temp_db,
             "--lock-file", temp_lock, "--skip-sec"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        
        assert result.returncode == 0
        assert "Database integrity check: OK" in result.stdout
        assert "Rebuilding indexes (REINDEX)" in result.stdout
        assert "Reclaiming database pages (VACUUM)" in result.stdout

    def test_lock_concurrency_error(self, temp_db, temp_lock):
        """Verify that lock file blocks execution when actively held."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        
        import msvcrt
        
        # Acquire lock in this process
        lock_fd = os.open(temp_lock, os.O_CREAT | os.O_RDWR)
        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
        
        try:
            result = subprocess.run(
                [PYTHON_EXEC, SCRIPT_PATH, "--dry-run", "--db-path", temp_db,
             "--lock-file", temp_lock, "--skip-sec"],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            
            assert result.returncode != 0
            assert "Another instance" in result.stdout or "Another instance" in result.stderr
        finally:
            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
            os.close(lock_fd)
            if os.path.exists(temp_lock):
                try:
                    os.remove(temp_lock)
                except:
                    pass


# Import audit function from scripts to verify granular logic
try:
    from scripts.weekly_maintenance import audit_and_fix_weekly
except ImportError:
    audit_and_fix_weekly = None


class TestWeeklyMaintenanceAudits:
    """TDD tests for the audit and self-healing logic in weekly_maintenance.py."""

    @pytest.fixture
    def setup_audit_db(self):
        """Prepare clean isolated memory DB for auditing tests."""
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db = TestingSessionLocal()
        
        # Seed basic metadata
        spy = Symbol(id=1, ticker="SPY", exchange="US", category="市場", active=1)
        aapl = Symbol(id=2, ticker="AAPL", exchange="US", category="個別", active=1)
        stale = Symbol(id=3, ticker="MSFT", exchange="US", category="個別", active=1)
        empty_theme = Symbol(id=4, ticker="EMPTY_T", exchange="US", category="テーマ", theme_type="theme", active=1)
        
        db.add_all([spy, aapl, stale, empty_theme])
        db.commit()
        
        yield db
        
        db.close()
        Base.metadata.drop_all(engine)

    def test_audit_delisted_symbols(self, setup_audit_db):
        """Verify that symbols staler than 5 trading days relative to SPY are detected."""
        db = setup_audit_db
        d_fresh = date(2026, 7, 15)
        d_stale = date(2026, 7, 1)
        
        db.add_all([
            DailyPrice(symbol_id=1, date=d_fresh, open=100, high=100, low=100, close=100, volume=100),
            DailyPrice(symbol_id=2, date=d_fresh, open=200, high=200, low=200, close=200, volume=200),
            DailyPrice(symbol_id=3, date=d_stale, open=300, high=300, low=300, close=300, volume=300),
        ])
        db.commit()
        
        # Run audit
        report = audit_and_fix_weekly(db, dry_run=True)
        
        assert "MSFT" in report["stale_symbols"]
        assert "EMPTY_T" in report["empty_themes"]

    def test_audit_invalid_constituents(self, setup_audit_db):
        """Verify that constituents pointing to inactive symbols are flagged."""
        db = setup_audit_db
        # Create an inactive symbol
        inactive = Symbol(id=5, ticker="OLD", exchange="US", category="個別", active=0)
        db.add(inactive)
        db.commit()
        
        # Add constituents pointing to active themes but targeting inactive symbols
        db.add_all([
            ThemeConstituent(theme_id=4, symbol_id=5, weight=1.0)
        ])
        db.commit()
        
        report = audit_and_fix_weekly(db, dry_run=True)
        assert len(report["invalid_constituents"]) > 0
        assert report["invalid_constituents"][0]["ticker"] == "OLD"

    def test_scan_and_fix_mismatched_indicators(self, setup_audit_db):
        """Verify that missing indicators on days with prices are backfilled when dry_run=False."""
        db = setup_audit_db
        d = date(2026, 7, 15)
        
        # AAPL has price but no indicator
        db.add_all([
            DailyPrice(symbol_id=1, date=d, open=100, high=100, low=100, close=100, volume=100),
            DailyPrice(symbol_id=2, date=d, open=200, high=200, low=200, close=200, volume=200),
            Indicator(symbol_id=1, date=d, ema_21=100), # SPY has indicator
        ])
        db.commit()
        
        # Run dry run first
        report_dry = audit_and_fix_weekly(db, dry_run=True)
        assert report_dry["mismatched_indicators_count"] == 1
        
        # Indicator still missing in DB
        ind_count = db.query(Indicator).filter(Indicator.symbol_id == 2, Indicator.date == d).count()
        assert ind_count == 0
        
        # Run in fix mode
        report_fix = audit_and_fix_weekly(db, dry_run=False)
        assert report_fix["mismatched_indicators_count"] == 1
        assert report_fix["fixed_indicators_count"] == 1
        
        # Verify indicator exists now
        ind = db.query(Indicator).filter(Indicator.symbol_id == 2, Indicator.date == d).first()
        assert ind is not None

    def test_moderate_drop_is_not_reported_as_split(self, setup_audit_db):
        """-50% の急落は「分割の疑い」として報告しない。

        旧実装は前日比だけで判定していたため、実際の値動きを全部拾っていた。
        実測（2026-08-05・Parquet 全期間 1,098件）では要対応はわずか3件で、
        残りは実際の値動き・低位株の振動・市場全体の急落日・仮想テーマの合成値だった。
        1:2 前後の帯では売買代金比でも暴落と分割を区別できない。
        """
        db = setup_audit_db
        d_old, d_new = date(2026, 7, 14), date(2026, 7, 15)
        db.add_all([
            DailyPrice(symbol_id=1, date=d_old, open=100, high=100, low=100, close=100, volume=100),
            DailyPrice(symbol_id=1, date=d_new, open=100, high=100, low=100, close=100, volume=100),
            DailyPrice(symbol_id=2, date=d_old, open=200, high=200, low=200, close=200, volume=100),
            DailyPrice(symbol_id=2, date=d_new, open=100, high=100, low=100, close=100, volume=100),
        ])
        db.commit()

        report = audit_and_fix_weekly(db, dry_run=True)

        assert report["split_anomalies"] == [], "実際の値動きを要対応として報告している"
        assert sum(report["anomaly_excluded"].values()) >= 1, "検出そのものが効いていない"

    def test_extreme_split_with_continuous_dollar_volume_is_reported(self, setup_audit_db):
        """`SOXS` 型（1146→62・売買代金が連続）は要対応として報告する。

        分割は株数が変わるだけなので売買代金は連続する。極端な比率のときだけ
        この指標が効く。
        """
        db = setup_audit_db
        d_old, d_new = date(2026, 7, 14), date(2026, 7, 15)
        rows = [
            DailyPrice(symbol_id=1, date=d_old, open=100, high=100, low=100, close=100, volume=100),
            DailyPrice(symbol_id=1, date=d_new, open=100, high=100, low=100, close=100, volume=100),
        ]
        # adv21 の下限を超えるため、直前に十分な売買代金の履歴を積む
        for i in range(25):
            d = date(2026, 6, 1) + timedelta(days=i)
            rows.append(DailyPrice(symbol_id=2, date=d, open=1000, high=1000, low=1000,
                                   close=1000, volume=20000))
        rows += [
            DailyPrice(symbol_id=2, date=d_old, open=1000, high=1000, low=1000, close=1000,
                       volume=20000),
            # 1:20 併合。株数が20倍になるので売買代金は連続する
            DailyPrice(symbol_id=2, date=d_new, open=50, high=50, low=50, close=50,
                       volume=400000),
        ]
        db.add_all(rows)
        db.commit()

        report = audit_and_fix_weekly(db, dry_run=True)

        hits = [a for a in report["split_anomalies"] if a["ticker"] == "AAPL"]
        assert hits, f"分割の疑いが報告されていない: {report['split_anomalies']}"
        assert hits[0]["classification"] == "split_suspect"

    def test_detect_fx_weekend_rows(self, setup_audit_db):
        """為替に存在しないはずの土日行を検出し、fix モードで除去する（第3層）。

        書き込み側の営業日フィルタと読み取り側の土日無視で実害は防いでいるが、
        それは「混入しても壊れない」だけ。新たな流入経路が生まれたときに
        気付けることを保証する。
        """
        from db.models import FxRate

        db = setup_audit_db
        db.add_all([
            FxRate(currency_pair="USD/JPY", date=date(2026, 7, 31), rate=160.18),  # 金
            FxRate(currency_pair="USD/JPY", date=date(2026, 8, 1), rate=157.40),   # 土
            FxRate(currency_pair="USD/JPY", date=date(2026, 8, 2), rate=157.50),   # 日
        ])
        db.commit()

        # dry-run: 報告するが消さない
        report = audit_and_fix_weekly(db, dry_run=True)
        assert [r[1] for r in report["fx_weekend_rows"]] == ["2026-08-01", "2026-08-02"]
        assert db.query(FxRate).count() == 3

        # fix: 土日行だけ消え、営業日の行は残る
        report_fix = audit_and_fix_weekly(db, dry_run=False)
        assert len(report_fix["fx_weekend_rows"]) == 2
        remaining = db.query(FxRate).all()
        assert [r.date for r in remaining] == [date(2026, 7, 31)]

    def test_no_fx_weekend_rows_when_clean(self, setup_audit_db):
        """平常時（営業日のみ）は何も報告しない＝ノイズにならない"""
        from db.models import FxRate

        db = setup_audit_db
        db.add(FxRate(currency_pair="USD/JPY", date=date(2026, 7, 31), rate=160.18))
        db.commit()

        report = audit_and_fix_weekly(db, dry_run=True)
        assert report["fx_weekend_rows"] == []


# ---------------------------------------------------------------------------
# 銘柄の鮮度分類（classify_symbol_freshness）の単体テスト
#
# 背景（2026-07-29 発見）:
# 旧実装は「SPY 最新日より5営業日以上古い」だけで上場廃止候補としていたため、
# 履歴が数行しか無い銘柄（供給側にデータが無い＝改称・廃止）と、完全な履歴があって
# 直近1日だけ取りこぼした銘柄（次回実行で自己回復）を区別できず、両者を同列に扱っていた。
# さらに inner join だったため「1行も無い」最も重症な銘柄を取りこぼしていた。
# ---------------------------------------------------------------------------
from datetime import date as _date

# `sys.path.insert(0, backend/scripts)` は使わないこと。pytest は収集時に全テストモジュールを
# import するため、scripts/ が sys.path の先頭に入って以降のモジュール解決を汚染し、
# 無関係なテスト（backtest 側）が順序依存で落ちる。PYTHONPATH=backend 前提の
# `scripts.` プレフィックス形式で読む（このファイル既存の import と同じ作法）。
from scripts.weekly_maintenance import (  # noqa: E402
    _write_sec_section,
    classify_symbol_freshness,
    resolve_report_dir,
    run_sec_corporate_action_sync,
)

SPY_LATEST = _date(2026, 7, 27)


class TestClassifySymbolFreshness:
    def test_up_to_date_is_ok(self):
        assert classify_symbol_freshness(500, _date(2026, 7, 27), SPY_LATEST) == "ok"

    def test_ahead_of_spy_is_ok(self):
        """為替など SPY より進んだ日付を持つケースでも ok 扱い"""
        assert classify_symbol_freshness(500, _date(2026, 7, 28), SPY_LATEST) == "ok"

    def test_one_day_behind_with_full_history_is_lagging(self):
        """回帰: WM / JBHT / MTD 等が 2,090 行を持ちながら1日遅れただけで
        上場廃止候補に混ざっていた。自己回復するので放置が正しい。"""
        assert classify_symbol_freshness(2090, _date(2026, 7, 24), SPY_LATEST) == "lagging"

    def test_long_stale_with_full_history_is_delisted(self):
        """CNCR: 1,804 行あって最終日が1年以上前 = 本当の上場廃止"""
        assert classify_symbol_freshness(1804, _date(2025, 6, 3), SPY_LATEST) == "delisted"

    def test_boundary_of_stale_window(self):
        # 7日ちょうどは境界の内側（まだ delisted ではない）
        assert classify_symbol_freshness(500, _date(2026, 7, 20), SPY_LATEST) == "lagging"
        assert classify_symbol_freshness(500, _date(2026, 7, 19), SPY_LATEST) == "delisted"

    @pytest.mark.parametrize("rows,last", [
        (0, None),                      # BK / ASGN: Yahoo に存在せず1行も無い
        (1, _date(2026, 7, 17)),        # IAC / VSCO: 直近だが1行だけ
        (3, _date(2026, 6, 12)),        # MASI: 3行だけ
        (7, _date(2026, 7, 27)),        # LC: 最新日に追いついていても行数が足りない
    ])
    def test_almost_no_history_is_no_history(self, rows, last):
        """回帰: 供給側にデータが無い銘柄。取り直しても取得できないため
        バックフィルではなく退役が正しい対応。"""
        assert classify_symbol_freshness(rows, last, SPY_LATEST) == "no_history"

    def test_row_count_threshold_boundary(self):
        assert classify_symbol_freshness(20, _date(2026, 7, 27), SPY_LATEST) == "no_history"
        assert classify_symbol_freshness(21, _date(2026, 7, 27), SPY_LATEST) == "ok"

    def test_none_last_date_is_no_history_regardless_of_count(self):
        assert classify_symbol_freshness(9999, None, SPY_LATEST) == "no_history"

    def test_新規IPO銘柄も行数不足ならno_historyになる(self):
        """**これは仕様どおり。**変更してはいけない。

        上場2週間の IPO（10行・データは最新）と、供給側にデータが無い銘柄
        （`LC`: 7行・データは最新）は、`(row_count, last_date, spy_latest)` だけでは
        **原理的に区別できない**。ここで「最新なら ok」に倒すと、
        2026-07-29 に修正した `LC` 型の取りこぼしが復活する。

        新規 IPO の保護は**この関数ではなく `split_by_sec_verdict()` が担う**
        （SEC マスタに載っている銘柄は自動退役 CSV から外れる）。
        レイヤの役割分担: 本関数は「自分の DB から見た状態」だけを述べ、
        「実際に退役してよいか」は SEC との突合で決める。
        """
        assert classify_symbol_freshness(10, SPY_LATEST, SPY_LATEST) == "no_history"

    def test_thresholds_are_configurable(self):
        assert classify_symbol_freshness(
            30, _date(2026, 7, 27), SPY_LATEST, low_history_rows=50) == "no_history"
        assert classify_symbol_freshness(
            500, _date(2026, 7, 24), SPY_LATEST, stale_days=1) == "delisted"


class TestReportDirIsolation:
    """回帰: レポート出力先が監査対象 DB に紐付くこと。

    出力先が `data/maintenance_reports/` 固定だったため、テストが `--db-path <一時DB>` で
    起動したサブプロセスの結果（空 DB なので全項目ゼロ）が本番のレポートを上書きし、
    さらに退役候補 CSV を削除していた（2026-07-29 実際に発生）。
    """

    def test_report_dir_follows_db_path(self, tmp_path):
        target = str(tmp_path / "sub" / "stocktool_test.db")
        assert resolve_report_dir(target) == os.path.join(
            os.path.dirname(os.path.abspath(target)), "maintenance_reports")

    def test_report_dir_falls_back_to_project_data(self):
        assert resolve_report_dir(None) == os.path.join(
            PROJECT_ROOT, "data", "maintenance_reports")

    def test_temp_db_run_does_not_touch_production_reports(self, temp_db, temp_lock, tmp_path):
        """一時 DB を監査しても本番の maintenance_reports に触れないこと。"""
        prod_dir = os.path.join(PROJECT_ROOT, "data", "maintenance_reports")
        before = set(os.listdir(prod_dir)) if os.path.isdir(prod_dir) else set()

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            [PYTHON_EXEC, SCRIPT_PATH, "--dry-run", "--db-path", temp_db,
             "--lock-file", temp_lock, "--skip-sec"],
            env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0

        after = set(os.listdir(prod_dir)) if os.path.isdir(prod_dir) else set()
        assert after == before, "本番の maintenance_reports が変化した"

        # 一時 DB 側に出力されていること
        own_dir = os.path.join(os.path.dirname(temp_db), "maintenance_reports")
        assert os.path.isfile(os.path.join(own_dir, "weekly_maintenance_report.txt"))


# ---------------------------------------------------------------------------
# SEC コーポレートアクション同期の組み込み
# ---------------------------------------------------------------------------
class TestSecSyncIntegration:
    """SEC 突合を週次メンテに載せたときの契約。

    背景: 2026年6〜7月に15件のコーポレートアクションを2ヶ月見逃した。
    週次で自動検知するようにしたが、**SEC はネットワーク依存**なので、
    そこが落ちたときに物理メンテや整合性監査の結果まで失われてはならない。
    """

    def _section(self, sec, dry_run=True):
        import io
        buf = io.StringIO()
        _write_sec_section(buf, sec, dry_run)
        return buf.getvalue()

    def test_sec_failure_does_not_abort_weekly_maintenance(self, monkeypatch):
        """SEC が落ちても例外を投げない。エラーを戻り値で返す。"""
        import scripts.sync_sec_corporate_actions as mod
        monkeypatch.setattr(mod, "run", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))

        got = run_sec_corporate_action_sync(dry_run=True, report_dir="x")

        assert "boom" in got["error"]

    def test_missing_contact_is_reported_not_raised(self, monkeypatch):
        """連絡先未設定（config.local.toml 無し）でも週次メンテ全体は続行する。"""
        import scripts.sync_sec_corporate_actions as mod

        def _raise(**kw):
            raise ValueError("SEC への連絡先が未設定です")
        monkeypatch.setattr(mod, "run", _raise)

        got = run_sec_corporate_action_sync(dry_run=True, report_dir="x")

        assert "連絡先" in got["error"]

    def test_report_separates_applied_from_needs_review(self):
        """適用済みと要判断を分ける。前者は事後確認、後者は行動が要る。"""
        text = self._section({
            "retired": [{"ticker": "CCRN", "evidence": "Form 15-12G (2026-07-27)"}],
            "renamed": [{"ticker": "GAMB", "new_ticker": "GRSD", "new_ticker_rows": 502,
                         "new_last_date": "2026-08-05", "old_last_date": "2026-07-29"}],
            "pending": [{"ticker": "XYZ", "new_ticker": "ABC",
                         "guard_reasons": ["② 新ティッカーの履歴が不足（0行 < 20行）"]}],
            "unknown": [], "master_gap": [], "coexisting": [],
        })

        assert "CCRN" in text and "GAMB" in text
        assert "ACTION REQUIRED" in text
        assert text.index("CCRN") < text.index("ACTION REQUIRED") < text.index("XYZ")

    def test_no_action_bucket_keeps_the_review_list_clean(self):
        """`master_gap` / `coexisting` を要判断に混ぜない。

        `AEP`（一括マスタ未収載の現役銘柄）や `VWDRY`/`VWSYF`（同一 CIK の ADR と原株）は
        毎週必ず検出される。要判断に並べるとレポートが読まれなくなる。
        """
        text = self._section({
            "retired": [], "renamed": [], "pending": [], "unknown": [],
            "master_gap": [{"ticker": "AEP"}],
            "coexisting": [{"ticker": "VWDRY", "new_ticker": "VWSYF"}],
        })

        review = text.split("7-c.")[1].split("7-d.")[0]
        assert "None" in review
        assert "AEP" in text and "VWDRY" in text

    def test_report_records_the_failure_reason(self):
        text = self._section({"error": "HTTP Error 503"})

        assert "503" in text
        assert "config.local.toml" in text

    def test_skipped_sync_is_visible_in_the_report(self):
        """スキップを黙って空欄にしない（検知したのか未実行なのか判別できるように）。"""
        assert "Skipped" in self._section(None)


class TestSecCrossCheckOnRetirementCandidates:
    """SEC が「上場している」と言う銘柄を自動退役リストに載せないこと。

    `RSHO`（Tema ETF, classId C000239058）は SEC マスタに現ティッカーで載っているのに、
    Yahoo からの価格供給だけが 2026-07-17 で止まっている。SEC を見ない鮮度監査は
    これを毎週「上場廃止候補」に挙げ続け、`retire_stale_symbols.py --from-report` で
    **健在な銘柄を退役させる**恐れがある。これは供給側（Yahoo）の問題であって廃止ではない。
    """

    def test_sec_alive_symbols_are_excluded_from_the_csv(self):
        from scripts.weekly_maintenance import split_by_sec_verdict

        rows = [("RSHO", "2026-07-17", 470), ("DEADCO", "2026-05-01", 900)]
        keep, alive = split_by_sec_verdict(rows, {"RSHO", "AAPL"})

        assert [r[0] for r in keep] == ["DEADCO"]
        assert [r[0] for r in alive] == ["RSHO"]

    def test_without_sec_data_nothing_is_filtered(self):
        """SEC 同期が失敗・スキップされたときに候補を握り潰さない。"""
        from scripts.weekly_maintenance import split_by_sec_verdict

        rows = [("RSHO", "2026-07-17", 470)]
        keep, alive = split_by_sec_verdict(rows, None)

        assert keep == rows and alive == []


# ---------------------------------------------------------------------------
# 新規 IPO 銘柄が退役候補に落ちないこと
#
# `ipo_candidates` から採用した銘柄は上場直後で行数が少なく、`classify_symbol_freshness`
# は `no_history`（退役候補）と分類する。これは仕様どおりで（上の
# `test_新規IPO銘柄も行数不足ならno_historyになる` 参照）、実際に退役されないことは
# **`split_by_sec_verdict()` の SEC 突合**が担保する。
#
# 採用した IPO 銘柄は必ず SEC マスタに載っている（そこから検知したため）ので、
# 「SEC 上は健在」として自動退役 CSV から外れる。
# 詳細: `doc/in_progress/ipo_candidates_plan.md` §7
# ---------------------------------------------------------------------------

from scripts.weekly_maintenance import split_by_sec_verdict  # noqa: E402


class TestNewIpoSymbolIsHeldFromRetirement:

    def test_SEC上健在な新規IPOは自動退役CSVから外れる(self):
        # 上場2週間、10行、データは最新 → no_history に分類される銘柄
        rows = [("EROC", _date(2026, 7, 27), 10)]
        keep, held = split_by_sec_verdict(rows, alive_tickers={"EROC", "SPY"})
        assert keep == [], "SEC 上健在な銘柄を自動退役 CSV に載せてはいけない"
        assert [r[0] for r in held] == ["EROC"]

    def test_SECからも消えた銘柄は退役候補に残る(self):
        rows = [("GONE", _date(2025, 1, 10), 5)]
        keep, held = split_by_sec_verdict(rows, alive_tickers={"SPY"})
        assert [r[0] for r in keep] == ["GONE"]
        assert held == []

    def test_SEC同期が失敗したときは握り潰さない(self):
        """照合できないことを「健在の証拠なし」と混同しない（既存の設計）。"""
        rows = [("EROC", _date(2026, 7, 27), 10)]
        keep, held = split_by_sec_verdict(rows, alive_tickers=None)
        assert [r[0] for r in keep] == ["EROC"]
        assert held == []


# ---------------------------------------------------------------------------
# IPO 候補スキャンの週次組み込み
#
# SEC 突合と同じく universe.db に対する操作。**失敗しても週次メンテ全体を落とさない**
# （物理メンテと整合性監査の結果を失う方が困る）という既存の方針に合わせる。
# ---------------------------------------------------------------------------

from scripts.weekly_maintenance import run_ipo_candidate_scan  # noqa: E402


class TestRunIpoCandidateScan:

    def test_dry_runではapplyしない(self, monkeypatch):
        seen = {}

        def fake_run(dry_run, **kw):
            seen["dry_run"] = dry_run
            return {"candidates": 10, "passed": 2, "pending": 2}

        import scripts.scan_ipo_candidates as scan
        monkeypatch.setattr(scan, "run", fake_run)

        r = run_ipo_candidate_scan(dry_run=True)
        assert seen["dry_run"] is True
        assert r["passed"] == 2

    def test_applyで実際に登録する(self, monkeypatch):
        seen = {}

        def fake_run(dry_run, **kw):
            seen["dry_run"] = dry_run
            return {"inserted": 3, "pending_total": 111}

        import scripts.scan_ipo_candidates as scan
        monkeypatch.setattr(scan, "run", fake_run)

        r = run_ipo_candidate_scan(dry_run=False)
        assert seen["dry_run"] is False
        assert r["inserted"] == 3

    def test_失敗しても例外を投げずerrorを返す(self, monkeypatch):
        """**週次メンテ全体を巻き添えにしない。**

        SEC / Yahoo はネットワーク依存で、ここで例外を投げると
        物理メンテや整合性監査の結果まで失われる（`run_sec_corporate_action_sync`
        と同じ方針）。
        """
        def boom(dry_run, **kw):
            raise RuntimeError("Yahoo に絞られた")

        import scripts.scan_ipo_candidates as scan
        monkeypatch.setattr(scan, "run", boom)

        r = run_ipo_candidate_scan(dry_run=False)
        assert "Yahoo に絞られた" in r["error"]

    def test_SEC連絡先未設定でもエラーを返すだけ(self, monkeypatch):
        def boom(dry_run, **kw):
            raise ValueError("SEC への連絡先が未設定です")

        import scripts.scan_ipo_candidates as scan
        monkeypatch.setattr(scan, "run", boom)

        assert run_ipo_candidate_scan(dry_run=True)["error"]
