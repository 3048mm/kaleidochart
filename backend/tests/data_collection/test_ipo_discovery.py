"""IPO 候補検知のテスト（data_collection/ipo_discovery.py）

## なぜこの仕組みが要るか

`universe.db` の個別銘柄は 2026-04 頃のスプレッドシートを初期インポートしたもので、
**それ以降に IPO した銘柄が一切入っていない**。既存の SEC 週次同期
（`sec_corporate_actions.py`）は universe.db → SEC の方向にしか走査しておらず、
「改称」「上場廃止」は検知できるが「新規上場」は構造的に検知できない。

本モジュールは逆方向（SEC → universe.db）に走査して IPO 候補を拾う。

## テストは実測ケースで固定する

作り物ではなく、2026-08-27 に SEC マスタ 10,388 ティッカーと
Yahoo chart API の層別サンプル 440 件を実測して判明した事象を使う。
計画レビュー中に**実測で否定された設計**がそのままテストになっている:

  - `instrumentType` / `longName` では株式クラスを判定できない
    （`SCAG` 普通株も `SCAGW` ワラントも `EQUITY` / "Scage Future" を返す）
  - 取引所は完全一致でないと `NYSEArca` が `NYSE` に前方一致で混入する
  - SPAC の兄弟ティッカーは語幹が伸びる（`JAB` → `JABRR` / `JABRU` / `JABRW`）
  - 「上場日 >= 基準日」だけでは絞れない
    （`SCAG` 2025-06-27 に対し ワラント `SCAGW` が 2026-07-17 と**より新しい**）

詳細: `doc/in_progress/ipo_candidates_plan.md` §2.2
"""

import os
import sys

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_collection.ipo_discovery import (  # noqa: E402
    ALLOWED_EXCHANGES,
    classify_cik_group,
    confirm_with_quote,
    detect_ipo_candidates,
    has_unit_sibling,
    is_allowed_exchange,
    is_non_common_line,
    name_flags,
)


# ---------------------------------------------------------------------------
# classify_cik_group — CIK 配下のティッカー群から普通株を1本選ぶ
# ---------------------------------------------------------------------------

class TestClassifyCikGroup:
    """SEC は株式クラスを教えてくれないので、ティッカーの構造から推定する。"""

    def test_単一ティッカーはそのまま普通株(self):
        # 典型的な IPO。Class B/C は創業者保有でティッカーを持たないため1本だけ
        r = classify_cik_group(["EROC"], "ERock, Inc.")
        assert r.ticker == "EROC"
        assert r.excluded is False
        assert "spac" not in r.flags

    def test_SPACの兄弟は語幹が伸びる(self):
        """`JAB` の兄弟は `JAB`+suffix ではなく `JABR`+suffix になる。

        SPAC は4文字の語幹でユニット・ワラントを上場し、普通株だけ NYSE 向けに
        3文字へ短縮する。ベース完全一致で判定すると**実測 262 件を取りこぼす**。
        """
        r = classify_cik_group(
            ["JAB", "JABRR", "JABRU", "JABRW"], "JAB Acquisition Corp I")
        assert r.ticker == "JAB"
        assert r.excluded is False        # 除外ではなくフラグ
        assert "spac" in r.flags

    @pytest.mark.parametrize("tickers,base", [
        (["NCO", "NCOOR", "NCOOU", "NCOOW"], "NCO"),
        (["CAQ", "CAQUU", "CAQUW"], "CAQ"),
        (["GIX", "GIXXR", "GIXXU"], "GIX"),
        (["MYX", "MYXXR", "MYXXU", "MYXXW"], "MYX"),
        (["EURK", "EURKR", "EURKU"], "EURK"),
        (["CXII", "CXIIU", "CXIIW"], "CXII"),
        (["SHOT", "SHOTR", "SHOTU"], "SHOT"),
    ])
    def test_SPAC構造は普通株を選んで残す(self, tickers, base):
        r = classify_cik_group(tickers, "Some Corp")
        assert r.ticker == base
        assert r.excluded is False
        assert "spac" in r.flags

    def test_ユニットが無くワラントだけならSPACとみなさない(self):
        """**ユニットは合併時に消滅する。** ワラントだけ残るのは de-SPAC 済み。

        `Scage Future` は SPAC 合併を終えた EV 企業で、実業会社として候補に
        出したい。ワラント兄弟だけで SPAC 扱いすると
        「de-SPAC で生まれた実業会社を取り逃がす」という計画の目的に反する。
        """
        r = classify_cik_group(["SCAG", "SCAGW"], "Scage Future")
        assert r.ticker == "SCAG"       # 普通株の選抜は従来どおり効く
        assert r.excluded is False
        assert "spac" not in r.flags

    @pytest.mark.parametrize("tickers,name", [
        (["INV", "INVLW"], "Innventure, Inc."),
        (["HPAI", "HPAIW"], "Helport AI Ltd"),
        (["FOXX", "FOXXW"], "Foxx Development Holdings Inc."),
        (["KWM", "KWMWW"], "K Wave Media Ltd."),
        (["GCL", "GCLWW"], "GCL Global Holdings Ltd"),
        (["YDES", "YDESW"], "YD Bio Ltd"),
    ])
    def test_deSPAC済みの実業会社にフラグを付けない(self, tickers, name):
        r = classify_cik_group(tickers, name)
        assert r.ticker == tickers[0]
        assert r.flags == set()

    def test_ユニットがあれば社名が実業風でもSPAC(self):
        """`CH4 Natural Solutions Corp` は社名に Acquisition / Merger を含まないが
        `MTNE-UN` を持つので現役 SPAC。社名だけでは判定できない実例。
        """
        r = classify_cik_group(
            ["MTNE", "MTNE-UN", "MTNE-WT"], "CH4 Natural Solutions Corp")
        assert r.ticker == "MTNE"
        assert "spac" in r.flags

    def test_ダッシュ形式のユニット_ワラントも同じ扱い(self):
        # `KPET-UN` / `KPET-WT` は社名に Acquisition を含まないため
        # 兄弟ティッカーでしか SPAC と分からない
        r = classify_cik_group(
            ["KPET", "KPET-UN", "KPET-WT"], "KPET Ultra Paceline Corp")
        assert r.ticker == "KPET"
        assert "spac" in r.flags

    def test_ADRのみのCIKはCIKごと除外(self):
        """国内普通株が存在しないので候補になりえない。

        フィルタ後に空になったとき全件へフォールバックすると、
        実測 873 CIK ぶんの無駄な Yahoo プローブが発生する。
        """
        r = classify_cik_group(["PITAF", "PSTEY"], "Poste Italiane S.p.A./ADR")
        assert r.excluded is True
        assert r.exclude_reason == "adr_only"

    def test_複数クラス別上場はCIKごと除外(self):
        """実測 231 件の実体は社債・優先株・ワラントで、普通株の複数クラスではない。

        `CRBD`（社債）が `CRBG`（普通株）より辞書順で先に来るため、
        接頭辞ルールに任せると**社債側を誤選択する**。曖昧なら落とす。
        """
        r = classify_cik_group(["CRBD", "CRBG"], "Corebridge Financial, Inc.")
        assert r.excluded is True
        assert r.exclude_reason == "multi_class"

    @pytest.mark.parametrize("tickers,name", [
        (["FG", "FGN", "FGSN"], "F&G Annuities & Life, Inc."),      # シニアノート
        (["PDCC", "PDPA"], "Pearl Diver Credit Co Inc."),           # 優先株シリーズA
        (["MLCI", "MLCIL"], "Mount Logan Capital Inc."),            # ベビーボンド
        (["SPMA", "SPMC", "SPME"], "Sound Point Meridian Capital"),  # ノート各シリーズ
        (["LLYVA", "LLYVB", "LLYVK"], "Liberty Live Holdings, Inc."),
    ])
    def test_複数クラス系はすべて除外(self, tickers, name):
        assert classify_cik_group(tickers, name).excluded is True

    def test_既知の優先株パターン_BACRP(self):
        # ダッシュ無しの優先株（`BACRP`）。`preferred_ticker()` の
        # 「`-` を含むか」だけの判定では取りこぼしていた
        r = classify_cik_group(["BAC", "BACRP"], "BANK OF AMERICA CORP")
        assert r.excluded is True


class TestFullScaleLeakage:
    """E1（本番相当 4,009 件）で pending に混入した3件を潰す。

    実測でしか出てこない形。ユニットテストの想像では拾えなかった。
    """

    def test_ダッシュ優先株は普通株にしない(self):
        """`CFTR-PA` は優先株シリーズA。**兄弟が居なくても**普通株ではない。

        その CIK に普通株が上場していないだけなので、CIK ごと落とすのが正しい。
        """
        r = classify_cik_group(["CFTR-PA"], "Cantor Fitzgerald Income Trust, Inc.")
        assert r.excluded is True

    @pytest.mark.parametrize("ticker", ["ABC-PA", "ABC-PB", "BAC-PK", "PSA-PF"])
    def test_各種ダッシュ優先株(self, ticker):
        assert classify_cik_group([ticker], "Some Corp").excluded is True

    def test_社名のAmericanDepositaryでADRを弾く(self):
        """`PHOS` は4文字なのでティッカー形からは ADR と分からない。

        `First Phosphate Corp. American Depositary Shares` と社名に書いてある。
        """
        assert "adr" in name_flags(
            "First Phosphate Corp. American Depositary Shares")

    def test_社名のFundをファンドとみなす(self):
        """`RVII`(Robinhood Ventures Fund II) はクローズドエンド型ファンド。

        `ETF` は含まないので既存の regex では拾えなかった。
        """
        assert "fund" in name_flags("Robinhood Ventures Fund II")

    @pytest.mark.parametrize("name", [
        "Office Properties Income Trust",
        "Terra Property Trust, Inc.",
        "Cantor Fitzgerald Income Trust, Inc.",
        "Blackstone Digital Infrastructure Trust Inc.",
    ])
    def test_Trustは除外しない(self, name):
        """**REIT を巻き込まない。** 実測 4 件はいずれも不動産・インフラの REIT。

        暗号資産信託は `MSBT`(NYSEArca) のように取引所判定で落ちるので、
        社名の `Trust` で弾く必要がない。
        """
        assert name_flags(name) == set()

    @pytest.mark.parametrize("name", [
        "Jersey Mike's Subs Inc.",
        "Reformation Inc.",
        "QVC Group Inc.",
        "ADI Global Distribution Inc",
        "Apnimed, Inc.",
        "Scribe Therapeutics Inc.",
        "Standard Nuclear, Inc.",
    ])
    def test_実在のIPOにフラグを付けない(self, name):
        """E1 で pending に入った実物。誤除外ゼロを回帰で守る。"""
        assert name_flags(name) == set()


# ---------------------------------------------------------------------------
# is_non_common_line — 普通株でない銘柄行かどうか
# ---------------------------------------------------------------------------

class TestIsNonCommonLine:

    @pytest.mark.parametrize("base,sibling", [
        ("JAB", "JABRU"), ("JAB", "JABRW"), ("JAB", "JABRR"),
        ("CAQ", "CAQUU"), ("SCAG", "SCAGW"), ("EURK", "EURKR"),
        ("KPET", "KPET-UN"), ("KPET", "KPET-WT"), ("WENC", "WENC-RI"),
    ])
    def test_ユニット_ワラント_ライツを検出(self, base, sibling):
        assert is_non_common_line(base, sibling) is True

    @pytest.mark.parametrize("base,sibling", [
        ("CRBD", "CRBG"),      # 社債 vs 普通株
        ("LLYVA", "LLYVB"),    # 議決権クラス
        ("FG", "FGN"),         # シニアノート
    ])
    def test_ユニット_ワラント以外はFalse(self, base, sibling):
        assert is_non_common_line(base, sibling) is False


class TestHasUnitSibling:
    """ユニットの有無が現役 SPAC と de-SPAC 済みを分ける唯一の構造的な手がかり。"""

    @pytest.mark.parametrize("base,sibs", [
        ("EURK", ["EURKR", "EURKU"]),
        ("CXII", ["CXIIU", "CXIIW"]),
        ("JAB", ["JABRR", "JABRU", "JABRW"]),
        ("KPET", ["KPET-UN", "KPET-WT"]),
        ("MTNE", ["MTNE-UN", "MTNE-WT"]),
        ("ALF", ["ALFUU", "ALFUW"]),
    ])
    def test_ユニットありを検出(self, base, sibs):
        assert has_unit_sibling(base, sibs) is True

    @pytest.mark.parametrize("base,sibs", [
        ("SCAG", ["SCAGW"]),
        ("INV", ["INVLW"]),
        ("KWM", ["KWMWW"]),     # WW をユニットと誤認しないこと
        ("GCL", ["GCLWW"]),
        ("YDES", ["YDESW"]),
    ])
    def test_ワラントのみはFalse(self, base, sibs):
        assert has_unit_sibling(base, sibs) is False


# ---------------------------------------------------------------------------
# is_allowed_exchange — 取引所ホワイトリスト
# ---------------------------------------------------------------------------

class TestIsAllowedExchange:

    @pytest.mark.parametrize("ex", [
        "NasdaqGS", "NasdaqGM", "NasdaqCM", "NYSE", "NYSE American",
    ])
    def test_主要取引所を許可(self, ex):
        assert is_allowed_exchange(ex) is True

    def test_NYSEArcaは前方一致で混入してはいけない(self):
        """`'NYSEArca'.startswith('NYSE')` は True。

        前方一致で実装すると **ETF・信託の取引所である NYSEArca が通る**。
        検証中に `MSBT`（Morgan Stanley Bitcoin Trust）が実際に通過した。
        """
        assert is_allowed_exchange("NYSEArca") is False

    @pytest.mark.parametrize("ex", [
        "OTC Markets OTCPK", "OTC Markets OTCQB", "OTC Markets OTCQX",
        "OTC Markets OTCID", "NYSEArca", "", None,
    ])
    def test_OTC系と未知は拒否(self, ex):
        assert is_allowed_exchange(ex) is False


# ---------------------------------------------------------------------------
# name_flags — 社名からのフラグ付け
# ---------------------------------------------------------------------------

class TestNameFlags:

    @pytest.mark.parametrize("name", [
        "Alpex Acquisition Corp",
        "Irenic Acquisition Corp.",
        "JATT II Acquisition Corp.",
        "MOZAYYX Acquisition Corp.",
        "West Enclave Merger Corp.",
        "General Catalyst Global Resilience Merger Corp",
    ])
    def test_SPACを社名から検出(self, name):
        assert "spac" in name_flags(name)

    @pytest.mark.parametrize("name", [
        "21Shares Hyperliquid ETF",
    ])
    def test_ETFを社名から検出(self, name):
        assert "fund" in name_flags(name)

    @pytest.mark.parametrize("name", [
        "ERock, Inc.",
        "Rare Earths Americas, Inc.",
        "Standard Nuclear, Inc.",
        "FedEx Freight Holding Company, Inc.",
        "Parabilis Medicines, Inc.",
        "Forbright, Inc.",
        "CH4 Natural Solutions Corp",
    ])
    def test_実業会社にはフラグを付けない(self, name):
        """スピンオフ（`FDXF`）を落とすと本末転倒。誤検知ゼロが目標。"""
        assert name_flags(name) == set()

    def test_Terra_Property_Trustは除外しない(self):
        # REIT の "Trust" と暗号資産信託の "Trust" を混同しない
        assert "fund" not in name_flags("Terra Property Trust, Inc.")


# ---------------------------------------------------------------------------
# confirm_with_quote — Yahoo の応答で確定させる
# ---------------------------------------------------------------------------

class TestConfirmWithQuote:

    BASE = {
        "instrumentType": "EQUITY",
        "fullExchangeName": "NasdaqGM",
        "first_trade_date": "2026-06-10",
    }

    def test_基準日以降の主要取引所の株式は通過(self):
        ok, reason = confirm_with_quote(self.BASE, since="2026-04-01")
        assert ok is True and reason is None

    def test_基準日より前の上場は落とす(self):
        q = {**self.BASE, "first_trade_date": "2025-06-27"}
        ok, reason = confirm_with_quote(q, since="2026-04-01")
        assert ok is False and reason == "before_since"

    def test_ワラントの新しい上場日に騙されない(self):
        """`SCAGW`（ワラント）の上場日 2026-07-17 は普通株 `SCAG` より新しい。

        日付フィルタは素通りするので、**ティッカー構造での除外が必須**。
        ここでは confirm 単体では通ってしまうことを明示的に固定し、
        `detect_ipo_candidates` 側で落ちることを別テストで担保する。
        """
        q = {**self.BASE, "first_trade_date": "2026-07-17"}
        ok, _ = confirm_with_quote(q, since="2026-04-01")
        assert ok is True

    def test_NYSEArcaの信託を落とす(self):
        q = {**self.BASE, "fullExchangeName": "NYSEArca",
             "first_trade_date": "2026-04-06"}
        ok, reason = confirm_with_quote(q, since="2026-04-01")
        assert ok is False and reason == "exchange"

    def test_ETFを落とす(self):
        q = {**self.BASE, "instrumentType": "ETF"}
        ok, reason = confirm_with_quote(q, since="2026-04-01")
        assert ok is False and reason == "instrument_type"

    def test_上場日が取れない場合は落とす(self):
        q = {**self.BASE, "first_trade_date": None}
        ok, reason = confirm_with_quote(q, since="2026-04-01")
        assert ok is False and reason == "no_first_trade_date"


# ---------------------------------------------------------------------------
# detect_ipo_candidates — 除外集合を引いて候補を組み立てる（通信なし）
# ---------------------------------------------------------------------------

class TestDetectIpoCandidates:

    CT = {
        "EROC": 2110029,          # 実業。候補になるべき
        "FDXF": 2000001,          # FedEx スピンオフ。候補になるべき
        "SCAG": 2000366,          # 既知（universe に居る想定）
        "SCAGW": 2000366,         # ワラント
        "JAB": 2128739, "JABRR": 2128739, "JABRU": 2128739, "JABRW": 2128739,
        "PITAF": 2149111, "PSTEY": 2149111,   # ADR のみ
        "CRBD": 1889539, "CRBG": 1889539,     # 複数クラス
        "AAPL": 320193,           # cik 既知
    }
    TITLES = {
        2110029: "ERock, Inc.",
        2000001: "FedEx Freight Holding Company, Inc.",
        2000366: "Scage Future",
        2128739: "JAB Acquisition Corp I",
        2149111: "Poste Italiane S.p.A./ADR",
        1889539: "Corebridge Financial, Inc.",
        320193: "Apple Inc.",
    }

    def _run(self, **kw):
        params = dict(
            company_tickers=self.CT,
            titles=self.TITLES,
            known_tickers={"AAPL", "SCAG"},
            known_ciks={320193},
            old_tickers=set(),
            fund_symbols=set(),
        )
        params.update(kw)
        return {c.ticker: c for c in detect_ipo_candidates(**params)}

    def test_cik既知は除外される(self):
        # 既存の改称検知（sec_corporate_actions）の担当。二重検知しない
        assert "AAPL" not in self._run()

    def test_実業会社が候補に上がる(self):
        out = self._run()
        assert "EROC" in out and out["EROC"].flags == set()

    def test_スピンオフが候補に上がる(self):
        """`FDXF` が通ることが最重要。SPAC 判定を強めすぎて落とすと本末転倒。"""
        assert "FDXF" in self._run()

    def test_ワラントは候補にならない(self):
        # 上場日ではティッカー構造の問題を解決できない（§2.2）
        assert "SCAGW" not in self._run()

    def test_SPACは候補に残るがフラグが付く(self):
        out = self._run()
        assert "JAB" in out and "spac" in out["JAB"].flags
        assert "JABRU" not in out

    def test_deSPAC済みはフラグ無しで候補に上がる(self):
        out = self._run(known_tickers={"AAPL"})   # SCAG を既知から外す
        assert "SCAG" in out and out["SCAG"].flags == set()

    def test_ADRのみのCIKは候補にならない(self):
        out = self._run()
        assert "PITAF" not in out and "PSTEY" not in out

    def test_複数クラスのCIKは候補にならない(self):
        out = self._run()
        assert "CRBD" not in out and "CRBG" not in out

    def test_symbols_masterの既知ティッカーは除外(self):
        # active に関係なく除外する（自分で退役させた 36 件を再浮上させない）
        assert "EROC" not in self._run(known_tickers={"EROC"})

    def test_改称前ティッカーは除外(self):
        """`ticker_history.old_ticker` が SEC マスタに残っていると新規に見える。"""
        assert "EROC" not in self._run(old_tickers={"EROC"})

    def test_ファンドシンボルは除外(self):
        assert "EROC" not in self._run(fund_symbols={"EROC"})


# ---------------------------------------------------------------------------
# 定数の健全性
# ---------------------------------------------------------------------------

def test_許可取引所にNYSEArcaを含めない():
    assert "NYSEArca" not in ALLOWED_EXCHANGES
    assert "NYSE" in ALLOWED_EXCHANGES
