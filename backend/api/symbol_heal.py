"""symbol_id 自己修復（heal）の共通コア。

watchlist / portfolio の heal_*_ids から呼ばれ、以下を一元的に提供する:

1. **修復コア**: T1 再同期で symbols.id が変わった項目を ticker/exchange から再解決。
   解決不能（上場廃止・ticker変更等）は symbol_id を NULL 化する。
2. **安全弁**: 解決不能率が閾値（30%）を超えた場合は一切書き込まない。
   大量解決不能は「接続先 symbols が不完全」（Sandbox 誤接続・T1 同期途中）の
   シグナルであり、NULL 化も再マッピングも破壊的になるため（2026-07-04 の
   I-7 事故の再発防止。詳細: doc/completed/heal_ids_hardening_plan.md）。
3. **スロットル**: 前回の heal が clean（修復ゼロ・安全弁非発動）だった場合のみ、
   TTL 内の再実行をスキップする。通常運用では GET のたびの全件走査が
   事実上なくなる一方、修復発生直後や異常検知中は毎回実行される。
"""
import logging
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from db.models import Symbol

logger = logging.getLogger(__name__)

# 解決不能率がこの値を超えたら安全弁が発動し、一切書き込まない
HEAL_MAX_UNRESOLVED_RATIO = 0.3
# 前回 clean だった場合に再実行をスキップする期間（秒）
HEAL_CLEAN_TTL_SECONDS = 3600

# スロットル記録: {(kind, stocktool DB URL, user DB URL): 最終 clean 時刻 (monotonic)}
_clean_heal_at: dict = {}

# 改称を追っても `ticker` を書き換えないテーブル。
# `position_history` は「その時どの銘柄を売買したか」の記録であり、
# 後から現行ティッカーへ書き換えると取引履歴として不正確になる。
# （`symbol_id` は現行へ解決する。価格の参照はそちらを使うため）
TICKER_IMMUTABLE_TYPES = frozenset({"PositionHistory"})

# 改称の連鎖を辿る上限。壊れた履歴（循環）で無限ループしないための保険。
MAX_RENAME_HOPS = 10


def resolve_current_ticker(ticker: str, rename_map: dict, max_hops: int = MAX_RENAME_HOPS):
    """旧ティッカーから現行ティッカーを引く。多段改称（A→B→C）も辿る。

    Args:
        rename_map: `old_ticker → current_ticker`（`universe.db.ticker_history` 由来）

    Returns:
        現行ティッカー。改称が無ければ ``None``。
    """
    seen = {ticker}
    cur = ticker
    for _ in range(max_hops):
        nxt = rename_map.get(cur)
        if nxt is None or nxt in seen:
            break
        seen.add(nxt)
        cur = nxt
    return cur if cur != ticker else None


def load_rename_map() -> dict:
    """`universe.db` の `ticker_history` から `old_ticker → current_ticker` を読む。

    **解決不能が出たときだけ呼ぶこと。** 正常時のリクエストパスに
    universe.db への接続を持ち込まないため（呼び出しは遅延評価）。
    """
    try:
        from db.database_universe import get_universe_db
        from db.models_universe import TickerHistory

        with get_universe_db() as udb:
            rows = udb.query(TickerHistory.old_ticker, TickerHistory.current_ticker).all()
        return {r.old_ticker: r.current_ticker for r in rows}
    except Exception as e:  # noqa: BLE001 — 追随は最善努力。失敗しても heal 本体は動かす
        logger.warning(f"ticker_history を読めませんでした（改称の追随をスキップ）: {e}")
        return {}


def reset_heal_throttle() -> None:
    """スロットル記録をクリアする（テスト用・強制再チェック用）。"""
    _clean_heal_at.clear()


@dataclass
class HealResult:
    total: int = 0
    healed: int = 0       # 正しい id へ再マッピングした件数
    nulled: int = 0       # 解決不能で NULL 化した件数
    unresolved: int = 0   # 解決不能な項目数（既に NULL のものも含む）
    skipped: Optional[str] = None  # None | "throttled" | "safety_valve"


def heal_symbol_references(db: Session, user_db: Session, items: Iterable, kind: str,
                           rename_map=None) -> HealResult:
    """items の symbol_id を stocktool DB の symbols と突合して修復する。

    Args:
        db: stocktool DB セッション（symbols の読み取りのみ）
        user_db: user_data DB セッション（修復時に commit される）
        items: `symbol_id` / `ticker` / `exchange` 属性を持つ ORM オブジェクト列
        kind: スロットルキー用の種別名（"watchlist", "portfolio" 等）
        rename_map: `old_ticker → current_ticker` の辞書、またはそれを返す callable。
            省略時は `load_rename_map()`（`universe.db.ticker_history`）。
            **ticker が解決できなかったときだけ評価される。**
    """
    items = list(items)
    total = len(items)
    if total == 0:
        return HealResult(total=0)

    throttle_key = (kind, str(db.get_bind().url), str(user_db.get_bind().url))
    now = time.monotonic()
    last_clean = _clean_heal_at.get(throttle_key)
    if last_clean is not None and (now - last_clean) < HEAL_CLEAN_TTL_SECONDS:
        return HealResult(total=total, skipped="throttled")

    # symbols を1クエリ・必要カラムのみで取得（N+1 とフルカラム ORM ロードの回避）
    rows = db.query(Symbol.id, Symbol.ticker, Symbol.exchange).all()
    by_id = {r.id: (r.ticker, r.exchange) for r in rows}
    by_ticker_exchange = {}
    by_ticker = {}
    for r in rows:
        by_ticker_exchange[(r.ticker, r.exchange)] = r.id
        by_ticker.setdefault(r.ticker, r.id)

    # ticker_history は解決不能が出たときだけ読む（正常時のリクエストパスを重くしない）
    _renames = {"loaded": False, "map": {}}

    def _renames_map() -> dict:
        if not _renames["loaded"]:
            src = rename_map if rename_map is not None else load_rename_map
            _renames["map"] = src() if callable(src) else src
            _renames["loaded"] = True
        return _renames["map"]

    # 第1パス: 分類のみ（この時点では書き込まない）
    to_fix = []  # (item, new_symbol_id or None, new_ticker or None)
    unresolved = 0
    for it in items:
        current = by_id.get(it.symbol_id) if it.symbol_id is not None else None
        if current is not None and current == (it.ticker, it.exchange):
            continue  # 正常

        new_id = by_ticker_exchange.get((it.ticker, it.exchange))
        if new_id is None:
            new_id = by_ticker.get(it.ticker)

        new_ticker = None
        if new_id is None:
            # ticker が変わった可能性。NULL 化する前に改称履歴を辿る
            renamed = resolve_current_ticker(it.ticker, _renames_map())
            if renamed is not None:
                new_id = by_ticker_exchange.get((renamed, it.exchange)) or by_ticker.get(renamed)
                if new_id is not None and type(it).__name__ not in TICKER_IMMUTABLE_TYPES:
                    new_ticker = renamed

        if new_id is None:
            unresolved += 1
            if it.symbol_id is not None:
                to_fix.append((it, None, None))
        else:
            to_fix.append((it, new_id, new_ticker))

    # 安全弁: 大量解決不能は接続先 symbols の不完全を疑い、一切書き込まない
    if unresolved / total > HEAL_MAX_UNRESOLVED_RATIO:
        logger.warning(
            f"heal({kind}): SAFETY VALVE — {unresolved}/{total} 件の ticker が"
            f"接続中の symbols から解決できないため、修復を中止しました。"
            f"接続先 DB（STOCKTOOL_DB_PATH / STOCKTOOL_USER_DB_PATH）の組み合わせ、"
            f"または T1 同期の状態を確認してください。"
        )
        return HealResult(total=total, unresolved=unresolved, skipped="safety_valve")

    # 第2パス: 書き込み
    healed = 0
    nulled = 0
    renamed_n = 0
    for it, new_id, new_ticker in to_fix:
        it.symbol_id = new_id
        if new_ticker is not None:
            logger.info(f"heal({kind}): 改称に追随 {it.ticker} → {new_ticker}")
            it.ticker = new_ticker
            renamed_n += 1
        if new_id is None:
            nulled += 1
        else:
            healed += 1

    if to_fix:
        user_db.commit()
        if nulled or renamed_n:
            logger.info(f"heal({kind}): {healed}件を再マッピング（うち改称追随 {renamed_n}件）、"
                        f"{nulled}件を解決不能として NULL 化しました。")
    else:
        # clean（修復ゼロ・安全弁非発動）の場合のみスロットル記録
        _clean_heal_at[throttle_key] = now

    return HealResult(total=total, healed=healed, nulled=nulled, unresolved=unresolved)
