"""
Google Sheets Exporter Module

universe.db の最新銘柄マスタおよびテーマ構成データを
gspread と credentials.json を使用して指定された Google スプレッドシートへ直接書き出す。
"""

import os
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from typing import Any, Dict, List
from sqlalchemy.orm import Session

from db.models_universe import SymbolMaster, ThemeMember
from data_collection.sheet_importer import extract_spreadsheet_id

logger = logging.getLogger(__name__)

# 対象シートとカテゴリの定義
SHEET_CONFIG = {
    "市場": "MarketList",
    "指標": "LeadingList",
    "セクタ": "SectorList",
    "テーマ": "ThemeList",
    "個別": "StockList",
    "レバレッジ": "LeverageList",
}


def export_universe_to_spreadsheet(
    db: Session,
    spreadsheet_url: str,
    credentials_path: str | None = None,
) -> Dict[str, Any]:
    """universe.db の全データを Google スプレッドシートへエクスポートする"""
    if credentials_path is None:
        # プロジェクトルートの credentials.json を参照
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        credentials_path = os.path.join(project_root, "credentials.json")

    if not os.path.exists(credentials_path):
        raise FileNotFoundError(
            f"認証ファイル 'credentials.json' が見つかりません。{credentials_path} を確認してください。"
        )

    # 1. Google API 認証 & スプレッドシート接続
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
    client = gspread.authorize(creds)

    spreadsheet_id = extract_spreadsheet_id(spreadsheet_url)
    spreadsheet = client.open_by_key(spreadsheet_id)

    # 2. universe.db からデータ取得
    symbols = db.query(SymbolMaster).filter(SymbolMaster.active == 1).order_by(SymbolMaster.ticker).all()
    members = db.query(ThemeMember).all()

    # 個別銘柄ごとの所属テーマタグマップを作成 { member_ticker: ["SOXX", "SMH"] }
    stock_themes_map: Dict[str, List[str]] = {}
    for m in members:
        stock_themes_map.setdefault(m.member_ticker.upper(), []).append(m.theme_ticker.upper())

    # カテゴリごとに分類
    categorized: Dict[str, List[SymbolMaster]] = {cat: [] for cat in SHEET_CONFIG.keys()}
    for s in symbols:
        cat = s.category if s.category in categorized else "個別"
        categorized[cat].append(s)

    exported_details: Dict[str, int] = {}
    total_exported = 0

    # 3. シートごとに書き込み
    for category, sheet_title in SHEET_CONFIG.items():
        sym_list = categorized.get(category, [])

        try:
            worksheet = spreadsheet.worksheet(sheet_title)
        except gspread.exceptions.WorksheetNotFound:
            # ワークシートが存在しない場合は自動作成
            worksheet = spreadsheet.add_worksheet(title=sheet_title, rows=1000, cols=10)

        # 既存データの1〜3行目を取得し、3行未満の場合は3行になるようにパディング(補正)
        existing_values = worksheet.get_all_values()
        header_rows = existing_values[:3]
        default_headers = [
            ["", "", "", "", "", ""],
            ["", "", "", "", "", ""],
            ["カテゴリ", "業界/業種", "名称", "取引所コード", "ティッカー", "属性(タグ)"]
        ]
        while len(header_rows) < 3:
            header_rows.append(default_headers[len(header_rows)])

        # 4行目以降のデータ行を作成
        rows_data = []
        for idx, s in enumerate(sym_list, start=1):
            # タグ (F列) の決定
            if category == "個別":
                # 個別銘柄の場合: sector_etf および所属テーマETF名のリストを出力
                theme_list = sorted(list(set(stock_themes_map.get(s.ticker.upper(), []))))
                if s.sector_etf and s.sector_etf.upper() not in theme_list:
                    theme_list.insert(0, s.sector_etf.upper())
                tags_str = ", ".join(theme_list)
            elif category == "テーマ":
                # テーマの場合: セクタETFのタグを出力
                tags_str = s.sector_etf or ""
            else:
                # その他のカテゴリの場合: sector_etf を出力
                tags_str = s.sector_etf or ""

            row = [
                s.category or category,  # A列: カテゴリ
                s.industry or "",        # B列: 業界/業種
                s.name or "",            # C列: 名称
                s.exchange or "",        # D列: 取引所コード
                s.ticker,                # E列: ティッカー
                tags_str,                # F列: 属性(タグ)
            ]
            rows_data.append(row)

        # ヘッダー + 新しいデータ行を一括セット
        full_content = header_rows + rows_data
        
        # セルを一括更新
        worksheet.clear()
        if full_content:
            worksheet.update(range_name="A1", values=full_content)

        count = len(rows_data)
        exported_details[sheet_title] = count
        total_exported += count
        logger.info(f"Exported {count} items to sheet '{sheet_title}'")

    return {
        "status": "success",
        "total_exported": total_exported,
        "details": exported_details,
    }
