"""
Google Sheets Importer Module

Google スプレッドシート（公開閲覧リンク）から直接マスタデータを取得し、
universe.db との差分計算 (Preview) および 本反映 (Execute) を行うモジュール。
"""

import os
import re
import csv
import io
import urllib.request
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from typing import Any, Dict, List, Tuple
from sqlalchemy.orm import Session

from db.models_universe import SymbolMaster, ThemeMember
from api.universe_router import _derive_theme_type

logger = logging.getLogger(__name__)

# スプレッドシート由来の行に付く source 値。
# replace モードで削除してよいのはこの値を持つ行だけで、それ以外
# （`pipeline` が登録した銘柄・手動追加・手動改称の結果）は保護する。
SHEET_SOURCE = "spreadsheet_url"

# 対象シートとカテゴリの定義
SHEET_CONFIG = {
    "MarketList": "市場",
    "LeadingList": "指標",
    "SectorList": "セクタ",
    "ThemeList": "テーマ",
    "StockList": "個別",
    "LeverageList": "レバレッジ",
}


def _is_sheet_owned(row) -> bool:
    """その行をスプレッドシート import が所有している（＝replace で消してよい）か。

    `source` が NULL の行は出所が不明なので**保護側に倒す**。
    universe.db はユーザー資産（`agent_execution_rules.md` §10.1）で、
    消えた手動編集は再生成できないため、判断に迷ったら残す。
    """
    return getattr(row, "source", None) == SHEET_SOURCE


def extract_spreadsheet_id(url: str) -> str:
    """スプレッドシートの URL から SPREADSHEET_ID を抽出する"""
    # 形式: https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit...
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not match:
        raise ValueError("無効な Google スプレッドシート URL です。'/spreadsheets/d/{ID}' 形式の URL を指定してください。")
    return match.group(1)


def fetch_sheet_csv(spreadsheet_id: str, sheet_name: str) -> List[List[str]]:
    """Google Sheets CSV Export URL から特定のシートの CSV データを取得する"""
    csv_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote(sheet_name)}"
    req = urllib.request.Request(
        csv_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read().decode("utf-8")
            reader = csv.reader(io.StringIO(content))
            return list(reader)
    except urllib.error.HTTPError as he:
        if he.code in (401, 403):
            raise PermissionError("アクセス権限エラー: スプレッドシートの共有設定を「リンクを知っている全員（閲覧）」に変更してください。")
        elif he.code == 404:
            raise FileNotFoundError("指定されたスプレッドシートが見つかりません。URLをご確認ください。")
        else:
            logger.warning(f"シート '{sheet_name}' 取得エラー ({he.code}): {he.reason}")
            return []
    except Exception as e:
        logger.warning(f"シート '{sheet_name}' の取得に失敗しました: {e}")
        return []


def fetch_all_sheets_data(spreadsheet_url: str) -> Dict[str, List[List[str]]]:
    """gspread (credentials.json) または 公開 CSV エクスポートで全シートの行データを取得する"""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    creds_path = os.path.join(project_root, "credentials.json")

    raw_sheets: Dict[str, List[List[str]]] = {}
    spreadsheet_id = extract_spreadsheet_id(spreadsheet_url)

    # 1. credentials.json による gspread 接続を試行 (パイプライン同等)
    if os.path.exists(creds_path):
        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
            client = gspread.authorize(creds)
            spreadsheet = client.open_by_key(spreadsheet_id)

            for sheet_title, cat_name in SHEET_CONFIG.items():
                try:
                    ws = spreadsheet.worksheet(sheet_title)
                    values = ws.get_all_values()
                    if len(values) >= 4:
                        raw_sheets[sheet_title] = values[3:]
                except gspread.exceptions.WorksheetNotFound:
                    logger.warning(f"シート '{sheet_title}' が見つかりません。")

            if raw_sheets:
                return raw_sheets
        except Exception as e:
            logger.warning(f"gspread 接続に失敗しました。公開 CSV エクスポートへフォールバックします: {e}")

    # 2. 公開 CSV エクスポートによるフォールバック
    for sheet_title, cat_name in SHEET_CONFIG.items():
        rows = fetch_sheet_csv(spreadsheet_id, sheet_title)
        if rows and len(rows) >= 4:
            raw_sheets[sheet_title] = rows[3:]

    return raw_sheets


def parse_symbols_from_url(url: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """スプレッドシート URL から全シンボルとテーマ構成銘柄をパースする

    Returns:
        (symbols_list, theme_members_list)
    """
    raw_sheet_rows = fetch_all_sheets_data(url)

    # 全ティッカーのカテゴリマップを作成 { ticker: category }
    symbol_cat_map: Dict[str, str] = {}
    for sheet_title, cat_name in SHEET_CONFIG.items():
        data_rows = raw_sheet_rows.get(sheet_title, [])
        for r in data_rows:
            if len(r) > 4 and r[4].strip():
                symbol_cat_map[r[4].strip().upper()] = cat_name

    symbols: List[Dict[str, Any]] = []
    theme_members: List[Dict[str, Any]] = []
    seen_keys = set()
    seen_member_keys = set()

    for sheet_title, category in SHEET_CONFIG.items():
        data_rows = raw_sheet_rows.get(sheet_title, [])
        for r in data_rows:
            # 配列長を補正
            r = r + [""] * max(0, 6 - len(r))
            asset_class = r[1].strip()  # B列: 業界/業種
            name = r[2].strip()         # C列: 名称
            exchange = r[3].strip()     # D列: 取引所
            ticker = r[4].strip()       # E列: ティッカー
            tags = r[5].strip()         # F列: 属性(タグ)

            if not ticker:
                continue

            theme_type = _derive_theme_type(exchange, category)
            key = (ticker.upper(), exchange.upper())

            sector_etf_val = None
            if category == "個別" and tags:
                tag_list = [t.strip().upper() for t in re.split(r"[,;\s]+", tags) if t.strip()]
                theme_candidates = []
                for tag in tag_list:
                    tag_cat = symbol_cat_map.get(tag)
                    if tag_cat in ("セクタ", "市場", "指標") and sector_etf_val is None:
                        sector_etf_val = tag
                    else:
                        theme_candidates.append(tag)

                for t_ticker in theme_candidates:
                    m_key = (t_ticker.upper(), ticker.upper())
                    if m_key not in seen_member_keys:
                        seen_member_keys.add(m_key)
                        theme_members.append({
                            "theme_ticker": t_ticker,
                            "member_ticker": ticker.upper(),
                        })
            else:
                sector_etf_val = tags if tags else None

            if key not in seen_keys:
                seen_keys.add(key)
                symbols.append({
                    "ticker": ticker.upper(),
                    "exchange": exchange,
                    "name": name,
                    "category": category,
                    "industry": asset_class,
                    "theme_type": theme_type,
                    "sector_etf": sector_etf_val,
                })

    # theme_members を親として持つ銘柄 (IBIT, CPER, BLOK 等) は、
    # 架空の VIRTUAL レコードを作らず、実在取引所 (NASDAQ/NYSEARCA) の実在ETFとして category='テーマ' に統一・確定する
    parent_tickers = {m["theme_ticker"].upper() for m in theme_members}
    for s in symbols:
        if s["ticker"].upper() in parent_tickers:
            s["category"] = "テーマ"
            if not s["theme_type"]:
                s["theme_type"] = "etf"

    return symbols, theme_members


def preview_import_diff(
    db: Session,
    parsed_symbols: List[Dict[str, Any]],
    parsed_members: List[Dict[str, Any]],
    mode: str = "upsert",
) -> Dict[str, Any]:
    """現在 DB (universe.db) のデータと比較し、差分オブジェクトを作成する

    Returns:
        {
            "summary": { "added": N, "updated": N, "deleted": N, "members_added": N },
            "added": [...],
            "updated": [...],
            "deleted": [...],
            "members_added": [...]
        }
    """
    existing_symbols = db.query(SymbolMaster).all()
    existing_map = {(s.ticker.upper(), (s.exchange or "").upper()): s for s in existing_symbols}
    existing_by_ticker = {s.ticker.upper(): s for s in existing_symbols}

    parsed_map = {(s["ticker"], s["exchange"].upper()): s for s in parsed_symbols}
    parsed_by_ticker = {s["ticker"]: s for s in parsed_symbols}

    added = []
    updated = []
    deleted = []

    # 1. 新規追加 & 更新の計算
    for item in parsed_symbols:
        ticker = item["ticker"]
        ex = item["exchange"].upper()
        existing = existing_map.get((ticker, ex)) or existing_by_ticker.get(ticker)

        if not existing:
            added.append({
                "ticker": item["ticker"],
                "exchange": item["exchange"],
                "name": item["name"],
                "category": item["category"],
                "industry": item["industry"],
                "sector_etf": item["sector_etf"],
            })
        else:
            # 変更点の比較
            changes = {}
            if (existing.name or "") != (item["name"] or ""):
                changes["name"] = {"old": existing.name, "new": item["name"]}
            if (existing.category or "") != (item["category"] or ""):
                changes["category"] = {"old": existing.category, "new": item["category"]}
            if (existing.industry or "") != (item["industry"] or ""):
                changes["industry"] = {"old": existing.industry, "new": item["industry"]}
            if (existing.sector_etf or "") != (item["sector_etf"] or ""):
                changes["sector_etf"] = {"old": existing.sector_etf, "new": item["sector_etf"]}

            if changes:
                updated.append({
                    "id": existing.id,
                    "ticker": existing.ticker,
                    "changes": changes,
                })

    # 2. 完全置換モード時の削除対象
    #    スプレッドシート由来の行だけを削除対象にする（保護の詳細は SHEET_SOURCE 参照）。
    #    プレビューと実行で対象がズレると「消えないはずの行が消えた」事故に気づけないため、
    #    ここでも execute_import_diff と同じ条件を使う。
    if mode == "replace":
        for existing in existing_symbols:
            if not _is_sheet_owned(existing):
                continue
            if existing.ticker.upper() not in parsed_by_ticker:
                deleted.append({
                    "id": existing.id,
                    "ticker": existing.ticker,
                    "name": existing.name,
                    "category": existing.category,
                })

    # 3. テーマ構成銘柄の差分
    existing_members = db.query(ThemeMember).all()
    existing_member_set = {(m.theme_ticker.upper(), m.member_ticker.upper()) for m in existing_members}
    
    members_added = []
    for pm in parsed_members:
        key = (pm["theme_ticker"], pm["member_ticker"])
        if key not in existing_member_set:
            members_added.append(pm)

    return {
        "summary": {
            "added": len(added),
            "updated": len(updated),
            "deleted": len(deleted),
            "members_added": len(members_added),
        },
        "added": added,
        "updated": updated,
        "deleted": deleted,
        "members_added": members_added,
    }


def execute_import_diff(
    db: Session,
    parsed_symbols: List[Dict[str, Any]],
    parsed_members: List[Dict[str, Any]],
    mode: str = "upsert",
) -> Dict[str, Any]:
    """解析したデータを DB (universe.db) に適用する"""
    protected_symbols: List[str] = []
    if mode == "replace":
        if len(parsed_symbols) < 10:
            raise ValueError(
                f"取得できたデータが極めて少ないため({len(parsed_symbols)}件)、データ誤消去を防止するため置換処理をキャンセルしました。"
                "スプレッドシートのアクセス権限（「リンクを知っている全員（閲覧）」）をご確認ください。"
            )
        # 完全置換: スプレッドシート由来の行だけを消す。
        # 旧実装は無条件に全 DELETE していたため、パイプラインが登録した銘柄や
        # 手動追加・手動改称の結果が import のたびに消えていた
        # （痕跡: ticker_history に _DRONE_（旧 ARKX）が残るのに symbols_master に実体が無い）。
        # T1 のソースがスプレッドシートから universe.db へ移った今、universe.db は
        # 「ユーザー資産」であり復元できない。保護は必須。
        protected_symbols = [
            s.ticker for s in db.query(SymbolMaster).all() if not _is_sheet_owned(s)
        ]
        db.query(ThemeMember).filter(ThemeMember.source == SHEET_SOURCE).delete(
            synchronize_session=False
        )
        db.query(SymbolMaster).filter(SymbolMaster.source == SHEET_SOURCE).delete(
            synchronize_session=False
        )
        db.flush()

    # 1. 銘柄 Upsert
    added_count = 0
    updated_count = 0

    for s_item in parsed_symbols:
        ticker = s_item["ticker"]
        ex = s_item["exchange"]

        existing = db.query(SymbolMaster).filter(
            SymbolMaster.ticker == ticker
        ).first()

        if existing:
            existing.exchange = ex
            existing.name = s_item["name"]
            existing.category = s_item["category"]
            existing.industry = s_item["industry"]
            existing.theme_type = s_item["theme_type"]
            existing.sector_etf = s_item["sector_etf"]
            existing.active = 1
            existing.source = "spreadsheet_url"
            updated_count += 1
        else:
            sym = SymbolMaster(
                ticker=ticker,
                exchange=ex,
                name=s_item["name"],
                category=s_item["category"],
                industry=s_item["industry"],
                theme_type=s_item["theme_type"],
                sector_etf=s_item["sector_etf"],
                active=1,
                source="spreadsheet_url",
            )
            db.add(sym)
            added_count += 1

    db.flush()

    # 2. テーマ構成銘柄の登録
    members_added_count = 0
    seen_inserted_members = set()

    for m_item in parsed_members:
        tt = m_item["theme_ticker"]
        mt = m_item["member_ticker"]
        key = (tt.upper(), mt.upper())

        if key in seen_inserted_members:
            continue

        exists = db.query(ThemeMember).filter(
            ThemeMember.theme_ticker == tt,
            ThemeMember.member_ticker == mt
        ).first()

        if not exists:
            tm = ThemeMember(
                theme_ticker=tt,
                member_ticker=mt,
                weight=1.0,
                source="spreadsheet_url",
            )
            db.add(tm)
            seen_inserted_members.add(key)
            members_added_count += 1

    db.flush()

    if protected_symbols:
        logger.info(
            f"replace: スプレッドシート由来でない {len(protected_symbols)} 件を保護しました "
            f"({', '.join(sorted(protected_symbols)[:10])}"
            f"{' ほか' if len(protected_symbols) > 10 else ''})"
        )

    return {
        "status": "success",
        "added": added_count,
        "updated": updated_count,
        "members_added": members_added_count,
        "protected": sorted(protected_symbols),
        "mode": mode,
    }
