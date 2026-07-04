"""backend/tests 全体に適用される共通フィクスチャ。"""
import pytest


@pytest.fixture(autouse=True)
def _reset_heal_throttle():
    """heal のスロットル記録（モジュールレベル状態）をテスト間で漏らさない。

    スロットルキーには DB URL が含まれるが、テストは同じ in-memory URL を
    使い回すため、リセットしないと前のテストの clean 記録が次のテストの
    heal をスキップさせてしまう。
    """
    from api.symbol_heal import reset_heal_throttle
    reset_heal_throttle()
    yield
