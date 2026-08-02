"""為替の営業日判定とレート解決の検証（indicators/fx_calendar.py）

背景: 株価の T2 取り込みは `row['date'] <= spy_latest_date` で Yahoo の
「当日・部分バー」を落とすが、fx_rates にはその上限が無い。2026-08-01(土) に
157.40 が本番へ混入し、金曜終値 160.18 と 1.7% 乖離した。

対策は2面ある。ここでは両方を検証する。
  - 書き込み側で土日行を作らない（test_fx_rates_refactoring.py の TEST-F）
  - 読み取り側が土日行を無視する（本ファイル）
"""

import os
import sys
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from db.models import Base, FxRate
from indicators.fx_calendar import is_fx_trading_day, resolve_fx_rate


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fx_calendar_test.db'}")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _add(db, d: date, rate: float):
    db.add(FxRate(currency_pair="USD/JPY", date=d, rate=rate))
    db.commit()


# ============================================================
# is_fx_trading_day
# ============================================================
@pytest.mark.parametrize("d", [
    date(2026, 7, 27),  # 月
    date(2026, 7, 28),  # 火
    date(2026, 7, 29),  # 水
    date(2026, 7, 30),  # 木
    date(2026, 7, 31),  # 金
])
def test_weekdays_are_trading_days(d):
    assert is_fx_trading_day(d) is True


@pytest.mark.parametrize("d", [date(2026, 8, 1), date(2026, 8, 2)])  # 土, 日
def test_weekend_is_not_trading_day(d):
    assert is_fx_trading_day(d) is False


def test_none_is_not_trading_day():
    """日付が取れなかった行を「営業日」と誤判定しない"""
    assert is_fx_trading_day(None) is False


def test_us_holiday_is_still_trading_day():
    """為替は米国株の休場日でも動く。株式の取引日カレンダーと一致させてはいけない。"""
    assert is_fx_trading_day(date(2026, 7, 3)) is True  # 独立記念日の振替（金）


# ============================================================
# resolve_fx_rate — 土日行が混入していても影響を受けない
# ============================================================
def test_saturday_row_is_ignored_and_friday_rate_is_used(db_session):
    """本番で実際に起きたケース: 土曜行 157.40 があっても金曜 160.18 を返す"""
    _add(db_session, date(2026, 7, 31), 160.18)  # 金
    _add(db_session, date(2026, 8, 1), 157.40)   # 土（混入した無効行）

    # 最新レート取得（target_date 省略）
    assert resolve_fx_rate(db_session) == 160.18

    # 土曜の取引に適用すべきレートも金曜終値
    assert resolve_fx_rate(db_session, target_date=date(2026, 8, 1)) == 160.18

    # 日曜も同様
    assert resolve_fx_rate(db_session, target_date=date(2026, 8, 2)) == 160.18


def test_target_date_uses_latest_trading_day_at_or_before(db_session):
    _add(db_session, date(2026, 7, 30), 159.00)
    _add(db_session, date(2026, 7, 31), 160.18)

    assert resolve_fx_rate(db_session, target_date=date(2026, 7, 30)) == 159.00
    assert resolve_fx_rate(db_session, target_date=date(2026, 7, 31)) == 160.18


def test_falls_back_to_oldest_trading_day_when_target_precedes_data(db_session):
    """取引日がデータ開始より前の場合は最古の営業日レートで代替する"""
    _add(db_session, date(2026, 7, 31), 160.18)

    assert resolve_fx_rate(db_session, target_date=date(2020, 1, 1)) == 160.18


def test_returns_default_when_no_rates(db_session):
    assert resolve_fx_rate(db_session, default=150.0) == 150.0


def test_returns_default_when_only_weekend_rows_exist(db_session):
    """土日行しか無い＝有効なレートが1件も無い。ダミー値を返すより default が安全。"""
    _add(db_session, date(2026, 8, 1), 157.40)
    _add(db_session, date(2026, 8, 2), 157.50)

    assert resolve_fx_rate(db_session, default=150.0) == 150.0


def test_other_currency_pair_is_not_mixed_in(db_session):
    _add(db_session, date(2026, 7, 31), 160.18)
    db_session.add(FxRate(currency_pair="EUR/JPY", date=date(2026, 7, 31), rate=175.0))
    db_session.commit()

    assert resolve_fx_rate(db_session, pair="USD/JPY") == 160.18
    assert resolve_fx_rate(db_session, pair="EUR/JPY") == 175.0
