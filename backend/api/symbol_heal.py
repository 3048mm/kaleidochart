"""symbol_id 自己修復（heal）の共通コア。

watchlist / portfolio の heal_*_ids から呼ばれ、以下を一元的に提供する:

1. **修復コア**: T1 再同期で symbols.id が変わった項目を ticker/exchange から再解決。
   解決不能（上場廃止・ticker変更等）は symbol_id を NULL 化する。
2. **安全弁**: 解決不能率が閾値（30%）を超えた場合は一切書き込まない。
   大量解決不能は「接続先 symbols が不完全」（Sandbox 誤接続・T1 同期途中）の
   シグナルであり、NULL 化も再マッピングも破壊的になるため（2026-07-04 の
   I-7 事故の再発防止。詳細: doc/current_in_development/heal_ids_hardening_plan.md）。
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


def heal_symbol_references(db: Session, user_db: Session, items: Iterable, kind: str) -> HealResult:
    """items の symbol_id を stocktool DB の symbols と突合して修復する。

    Args:
        db: stocktool DB セッション（symbols の読み取りのみ）
        user_db: user_data DB セッション（修復時に commit される）
        items: `symbol_id` / `ticker` / `exchange` 属性を持つ ORM オブジェクト列
        kind: スロットルキー用の種別名（"watchlist", "portfolio" 等）
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

    # 第1パス: 分類のみ（この時点では書き込まない）
    to_fix = []  # (item, new_symbol_id or None)
    unresolved = 0
    for it in items:
        current = by_id.get(it.symbol_id) if it.symbol_id is not None else None
        if current is not None and current == (it.ticker, it.exchange):
            continue  # 正常

        new_id = by_ticker_exchange.get((it.ticker, it.exchange))
        if new_id is None:
            new_id = by_ticker.get(it.ticker)

        if new_id is None:
            unresolved += 1
            if it.symbol_id is not None:
                to_fix.append((it, None))
        else:
            to_fix.append((it, new_id))

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
    for it, new_id in to_fix:
        it.symbol_id = new_id
        if new_id is None:
            nulled += 1
        else:
            healed += 1

    if to_fix:
        user_db.commit()
        if nulled:
            logger.info(f"heal({kind}): {healed}件を再マッピング、{nulled}件を解決不能として NULL 化しました。")
    else:
        # clean（修復ゼロ・安全弁非発動）の場合のみスロットル記録
        _clean_heal_at[throttle_key] = now

    return HealResult(total=total, healed=healed, nulled=nulled, unresolved=unresolved)
