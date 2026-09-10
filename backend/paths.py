"""データルート / DB / Parquet のパス解決を一元化する唯一の権威。

## なぜ必要か

従来、パス解決の権威が4系統に分裂していた:

1. `config.toml` の `db_path`（**絶対パス**で本番を指す）
2. 相対デフォルト `"data/stocktool.db"`
3. `STOCKTOOL_ENV=sandbox` → `"data/sandbox/stocktool.db"`
4. `os.path.join(_PROJECT_ROOT, "data", ...)`（**環境変数を一切見ない**。14ファイルに散在）

ワークツリーでは 2〜4 がワークツリー内の存在しないパスに解決され、
`create_all` / `sqlite3.connect` が**エラーにならず空DBを新規作成する**。
「サイレント成功 → 間違ったデータで結論」という、このプロジェクトが
繰り返し踏んできた事故の型そのものだった。

## 方針

- **ワークツリーは明示的なプロビジョニングを要求する**。`config.toml` は
  本番の絶対パスを持つため、ワークツリーでは**意図的に無視する**
  （尊重すると「ワークツリーから本番を書ける」経路が残るため）。
- 存在しないデータには `DataNotProvisionedError` で**即死**させる。
  復旧コマンドを例外メッセージに埋め込む。
- 本番への書き込みは FS では防げない（reparse point も hardlink も
  自前の ACL を持たない）ため、`ensure_writable()` でコード側で担保する。

設計の全体像: `doc/completed/worktree_data_provisioning_plan.md` §3.1
"""
import os

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - 古い環境向けフォールバック
    import tomli as _toml


class DataNotProvisionedError(RuntimeError):
    """必要なデータ（DB / Parquet / data ルート）が用意されていない。

    **黙って空DBを作らせないための例外。** メッセージには必ず復旧手順を含める。
    """


class ProductionWriteError(RuntimeError):
    """ワークツリーから本番データへ書き込もうとした。"""


#: `get_db_path()` が解決できる論理名 → ファイル名
DB_FILENAMES = {
    "stocktool": "stocktool.db",
    "user_data": "user_data.db",
    "universe": "universe.db",
    "optimization_trials": "optimization_trials.db",
}

PARQUET_MASTER_DIRNAME = "parquet_master"

_ENV_DATA_ROOT = "STOCKTOOL_DATA_ROOT"
_ENV_PROD_DATA_ROOT = "STOCKTOOL_PROD_DATA_ROOT"
_ENV_NAME = "STOCKTOOL_ENV"
_ENV_ALLOW_CREATE = "STOCKTOOL_ALLOW_DB_CREATE"

_PROVISION_CMD = (
    "python tools/provision_worktree_data.py . --mode read   # 読み取りのみ\n"
    "  python tools/provision_worktree_data.py . --mode write  # sandbox も作る"
)


# --- リポジトリの位置と種別 -----------------------------------------------

def get_repo_root() -> str:
    """このモジュールが属するリポジトリ（本体またはワークツリー）のルート。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_worktree(repo_root: str | None = None) -> bool:
    """git ワークツリーかどうか。

    本体チェックアウトでは `.git` はディレクトリ、ワークツリーでは
    `gitdir: ...` を格納した**ファイル**になる（実測で確認済み）。
    """
    root = repo_root or get_repo_root()
    return os.path.isfile(os.path.join(root, ".git"))


# --- TOML の読み取り ------------------------------------------------------

def _read_toml(path: str) -> dict:
    """TOML を読む。存在しない・壊れている場合は空 dict を返す。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as f:
            return _toml.load(f)
    except Exception:
        # 設定ファイルの破損でパス解決全体を巻き込まない。
        # 実際に必要な値が取れなければ後段が DataNotProvisionedError を出す。
        return {}


def _config_local(repo_root: str) -> dict:
    return _read_toml(os.path.join(repo_root, "config.local.toml"))


def _default_main_data_root(repo_root: str) -> str:
    """本体チェックアウトにおける「本番 data」の位置。

    `STOCKTOOL_ENV` や `STOCKTOOL_DATA_ROOT` の影響を受けない点が重要。
    本体で sandbox に切り替えていても、本番の所在は変わらないため。
    """
    conf = _read_toml(os.path.join(repo_root, "config.toml"))
    db_path = (conf.get("system") or {}).get("db_path")
    if db_path:
        return os.path.abspath(os.path.dirname(db_path))
    return os.path.join(repo_root, "data")


# --- data ルートの解決 ----------------------------------------------------

def get_data_root(repo_root: str | None = None) -> str:
    """書き込み先の data ルートを解決する。

    優先順位:
        1. 環境変数 ``STOCKTOOL_DATA_ROOT``
        2. ``STOCKTOOL_ENV=sandbox|test`` → ``<repo>/data/sandbox|test``
        3. ``config.local.toml`` の ``[data] root``（プロビジョニングが書く）
        4. **本体のみ** ``config.toml`` の ``[system] db_path`` の親
        5. **本体のみ** ``<repo>/data``
        6. ワークツリーでここまで来たら ``DataNotProvisionedError``

    Raises:
        DataNotProvisionedError: ワークツリーが未プロビジョニングの場合。
    """
    root = repo_root or get_repo_root()

    env_root = (os.environ.get(_ENV_DATA_ROOT) or "").strip()
    if env_root:
        return os.path.abspath(env_root)

    env_name = (os.environ.get(_ENV_NAME) or "").strip()
    if env_name in ("sandbox", "test"):
        return os.path.join(root, "data", env_name)

    local_root = ((_config_local(root).get("data") or {}).get("root") or "").strip()
    if local_root:
        return os.path.abspath(local_root)

    if not is_worktree(root):
        return _default_main_data_root(root)

    if allow_db_create():
        # pytest や「DB を新規作成すること自体が目的」のタスク。
        # ワークツリー自身の data/ に落とす。**config.toml（本番の絶対パス）には
        # 絶対に落とさない** — テストが本番を掴む事故を作らないため。
        return os.path.join(root, "data")

    raise DataNotProvisionedError(
        f"ワークツリー {root} の data ルートが未設定です。\n"
        f"  ワークツリーでは config.toml（本番の絶対パス）を意図的に無視するため、\n"
        f"  先にプロビジョニングが必要です:\n"
        f"    {_PROVISION_CMD}\n"
        f"  一時的に明示する場合は環境変数 {_ENV_DATA_ROOT} を絶対パスで設定してください。"
    )


def get_prod_data_root(repo_root: str | None = None) -> str | None:
    """本番 data ルート（**読み取り専用参照**を想定）。

    未設定のワークツリーでは ``None`` を返す（例外は投げない。
    「本番がどこか分からない」ことは、それ自体では異常ではないため）。
    """
    root = repo_root or get_repo_root()

    env_prod = (os.environ.get(_ENV_PROD_DATA_ROOT) or "").strip()
    if env_prod:
        return os.path.abspath(env_prod)

    local_prod = ((_config_local(root).get("data") or {}).get("prod_root") or "").strip()
    if local_prod:
        return os.path.abspath(local_prod)

    if not is_worktree(root):
        return _default_main_data_root(root)

    return None


def require_prod_data_root(repo_root: str | None = None) -> str:
    """本番 data ルートを返す。未設定なら復旧手順つきで即死する。

    `get_prod_data_root()` が `None` を許すのは「本番がどこか分からなくても
    それ自体は異常ではない」場面（`is_production()` の判定・`ensure_writable()`
    のガード等）向け。**本番を読むこと自体が目的の監査ツール**
    （`scripts/scan_price_anomalies.py` / `scripts/scan_split_consistency.py`）
    にとっては `None` は続行不能なエラーで、ここを経由せず `config.toml` を
    直接読んでいたために、未プロビジョニングのワークツリーでも気づかず
    「たまたま存在する」本番の絶対パスへフォールバックしていた
    （2026-09-10 発見・2026-09-11 対応）。
    """
    root = repo_root or get_repo_root()
    prod_root = get_prod_data_root(root)
    if prod_root:
        return prod_root
    raise DataNotProvisionedError(
        "本番 data の場所が分かりません。\n"
        "  このツールは本番データを読むこと自体が目的なので、\n"
        "  ワークツリーでは明示的なプロビジョニングが必要です:\n"
        f"    {_PROVISION_CMD}\n"
        "  もしくは本体チェックアウトから実行してください。"
    )


def is_production(repo_root: str | None = None) -> bool:
    """いま書き込み先として見ている data ルートが本番かどうか。"""
    root = repo_root or get_repo_root()
    try:
        data_root = get_data_root(root)
    except DataNotProvisionedError:
        return False
    prod_root = get_prod_data_root(root)
    if not prod_root:
        return False
    return _same_path(data_root, prod_root)


# --- 個別パス -------------------------------------------------------------

def get_db_path(name: str, repo_root: str | None = None) -> str:
    """論理名から DB ファイルのパスを解決する。

    Args:
        name: ``DB_FILENAMES`` のキー（stocktool / user_data / universe /
            optimization_trials）。

    Raises:
        KeyError: 未知の論理名。**黙って既定値にフォールバックしない。**
    """
    if name not in DB_FILENAMES:
        raise KeyError(
            f"未知の DB 名 {name!r}。既知: {sorted(DB_FILENAMES)}")
    return os.path.join(get_data_root(repo_root), DB_FILENAMES[name])


def announce_non_production(label: str, db_path: str) -> None:
    """本番以外の DB に接続していることを目立つバナーで知らせる。

    従来 `init_db()` 系が個別に出していたバナーを共通化したもの。
    「本番のつもりで sandbox を見ていた／その逆」を防ぐための可視化なので、
    静かにしない。
    """
    try:
        if is_production():
            return
    except DataNotProvisionedError:
        pass
    print("\n" + "!" * 60)
    print(f"!!! [INFO] {label} ENVIRONMENT: NON-PRODUCTION !!!")
    print(f"!!! Target DB: {db_path} ")
    print("!" * 60 + "\n")


#: 後方互換のためのレガシー環境変数（DB 個別指定）。最優先で尊重する。
_LEGACY_DB_ENV = {
    "stocktool": "STOCKTOOL_DB_PATH",
    "user_data": "STOCKTOOL_USER_DB_PATH",
    "universe": "STOCKTOOL_UNIVERSE_DB_PATH",
}


def resolve_db_path_for_init(name: str, caller_path: str | None = None,
                             repo_root: str | None = None) -> str:
    """``init_db(db_path)`` 系が実際に開くべきパスを決める。

    優先順位:
        1. レガシー環境変数（``STOCKTOOL_DB_PATH`` 等）— 後方互換
        2. ``STOCKTOOL_DATA_ROOT`` / ``STOCKTOOL_ENV=sandbox|test``
        3. **ワークツリーなら** ``paths`` の解決結果（呼び出し側の引数は無視）
        4. **本体なら** 呼び出し側の引数（現行動作を保つ）

    3 がこの関数の要点。本体の呼び出し側（``server.py`` 等）は
    ``config.toml`` の**本番絶対パス**を渡してくるため、ワークツリーで
    それを尊重すると本番を書ける経路が残ってしまう。ワークツリーでは
    プロビジョニングの結果を正とし、未プロビジョニングなら
    ``DataNotProvisionedError`` で即死させる。
    """
    root = repo_root or get_repo_root()

    legacy_env = _LEGACY_DB_ENV.get(name)
    if legacy_env:
        legacy = (os.environ.get(legacy_env) or "").strip()
        if legacy:
            return os.path.abspath(legacy)

    env_name = (os.environ.get(_ENV_NAME) or "").strip()
    forced = (
        bool((os.environ.get(_ENV_DATA_ROOT) or "").strip())
        or env_name in ("sandbox", "test")
        # ワークツリーでは paths が権威。ただし pytest 等で新規作成が
        # 明示的に許可されている場合は、呼び出し側の明示パス（tmp_path 等）を尊重する。
        or (is_worktree(root) and not allow_db_create())
    )
    if forced or not caller_path:
        return get_db_path(name, root)

    resolved = os.path.abspath(caller_path)
    if is_worktree(root):
        # 引数を尊重する場合でも、**本番を指す値だけは絶対に通さない**。
        # pytest 下の `api.server` の import は
        # `init_db(config["system"]["db_path"])`（= 本番の絶対パス）を呼ぶため、
        # ここを塞がないとワークツリーのテストが本番 DB を開いてしまう。
        prod_root = get_prod_data_root(root)
        if prod_root and _is_under(resolved, prod_root):
            return get_db_path(name, root)
    return resolved


def get_parquet_master_dir(repo_root: str | None = None) -> str:
    """Parquet マスターのディレクトリ（data ルート直下）。"""
    return os.path.join(get_data_root(repo_root), PARQUET_MASTER_DIRNAME)


def get_prod_parquet_master_dir(repo_root: str | None = None) -> str | None:
    """本番 Parquet マスター（読み取り専用参照）。未設定なら ``None``。"""
    prod = get_prod_data_root(repo_root)
    return os.path.join(prod, PARQUET_MASTER_DIRNAME) if prod else None


# --- fail-fast ------------------------------------------------------------

def allow_db_create() -> bool:
    """環境変数で新規作成が明示的に許可されているか（テスト・新規作成タスク）。"""
    return bool((os.environ.get(_ENV_ALLOW_CREATE) or "").strip())


def require_existing(path: str, what: str = "データ") -> str:
    """存在しなければ復旧手順つきで即死する。存在すればそのまま返す。

    ``STOCKTOOL_ALLOW_DB_CREATE`` が設定されていれば素通しする
    （pytest や、DB を新規作成することが目的のタスク向け）。
    """
    if allow_db_create() or os.path.exists(path):
        return path
    raise DataNotProvisionedError(
        f"{what}が存在しません: {path}\n"
        f"  ワークツリーでは先にプロビジョニングが必要です:\n"
        f"    {_PROVISION_CMD}\n"
        f"  新規に作成することが目的のタスクでは、環境変数 "
        f"{_ENV_ALLOW_CREATE}=1 を設定するか allow_create=True を渡してください。"
    )


#: 「中身がある」ことを確かめるための番兵テーブル。
#: `user_data` は空が正常な状態（ウォッチリスト未登録）なので対象外。
_POPULATED_SENTINEL = {
    "stocktool": "symbols",
    "universe": "symbols_master",
}


def require_populated(db_path: str, name: str) -> str:
    """「ファイルはあるが中身が空」を弾く。

    `require_existing()` だけでは不十分だった。ワークツリーに残る
    `stocktool.db` は **139,264 バイトのスキーマだけの空DB**（pytest が
    `STOCKTOOL_ALLOW_DB_CREATE=1` で作った残骸）で、存在チェックを通過してしまう。
    その状態で本番のつもりの処理を走らせると、**0件の結果を正常な結果として
    受け取る**——このプロジェクトが繰り返し踏んできた事故の型そのもの。

    番兵テーブルが無い、または 0 行なら未プロビジョニングとみなす。
    """
    if allow_db_create():
        return db_path
    sentinel = _POPULATED_SENTINEL.get(name)
    if not sentinel:
        return db_path

    import sqlite3
    reason = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            found = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (sentinel,)).fetchone()
            if not found:
                reason = f"テーブル {sentinel} が存在しません"
            elif conn.execute(f"SELECT 1 FROM {sentinel} LIMIT 1").fetchone() is None:
                reason = f"テーブル {sentinel} が 0 行です"
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        reason = f"SQLite として読めません（{e}）"

    if reason is None:
        return db_path
    raise DataNotProvisionedError(
        f"{name} DB は存在しますが中身がありません: {db_path}\n"
        f"  理由: {reason}（サイズ {os.path.getsize(db_path):,} バイト）\n"
        f"  pytest が残した空DBを掴んでいる可能性があります。\n"
        f"  プロビジョニングし直してください:\n"
        f"    {_PROVISION_CMD}\n"
        f"  空のまま使うことが目的なら {_ENV_ALLOW_CREATE}=1 を設定してください。"
    )


# --- 本番への書き込みガード ------------------------------------------------

def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _is_under(path: str, parent: str) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    q = os.path.normcase(os.path.abspath(parent))
    return p == q or p.startswith(q + os.sep)


def ensure_writable(path: str, repo_root: str | None = None) -> str:
    """ワークツリーから本番 data 配下へ書き込もうとしていないか検査する。

    本体チェックアウトからの本番書き込みは正常な運用（daily update・昇格）
    なので通す。ファイルシステム側では読み取り専用にできないため、
    ここがワークツリーに対する実効的なガードになる。

    Raises:
        ProductionWriteError: ワークツリーから本番配下に書こうとした場合。
    """
    root = repo_root or get_repo_root()
    if not is_worktree(root):
        return path
    prod_root = get_prod_data_root(root)
    if prod_root and _is_under(path, prod_root):
        raise ProductionWriteError(
            f"ワークツリーから本番データへの書き込みは禁止されています。\n"
            f"  書き込み先 : {os.path.abspath(path)}\n"
            f"  本番 data  : {prod_root}\n"
            f"  本番は読み取り専用で参照してください。書き込みが必要なら\n"
            f"  sandbox を用意してください:\n"
            f"    python tools/provision_worktree_data.py . --mode write"
        )
    return path
