"""backend/paths.py のテスト。

パス解決の唯一の権威としての振る舞いを固定する。
背景と設計: doc/completed/worktree_data_provisioning_plan.md §3.1
"""
import os

import pytest

import paths


# --- テスト用ヘルパ -------------------------------------------------------

STOCKTOOL_ENV_VARS = (
    "STOCKTOOL_DATA_ROOT",
    "STOCKTOOL_PROD_DATA_ROOT",
    "STOCKTOOL_ENV",
    "STOCKTOOL_ALLOW_DB_CREATE",
    "STOCKTOOL_DB_PATH",
    "STOCKTOOL_USER_DB_PATH",
    "STOCKTOOL_UNIVERSE_DB_PATH",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """このモジュールのテストは環境変数を汚さない状態から始める。"""
    for name in STOCKTOOL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def make_repo(tmp_path, *, worktree: bool, config_toml: str | None = None,
              config_local_toml: str | None = None):
    """本体チェックアウト / ワークツリーを模したディレクトリを作る。

    本体は `.git` がディレクトリ、ワークツリーは `.git` がファイル
    （`gitdir: ...` を格納）という git の実際の構造に合わせる。
    """
    root = tmp_path / ("wt" if worktree else "main")
    root.mkdir()
    if worktree:
        (root / ".git").write_text("gitdir: D:/somewhere/.git/worktrees/x\n",
                                   encoding="utf-8")
    else:
        (root / ".git").mkdir()
    if config_toml is not None:
        (root / "config.toml").write_text(config_toml, encoding="utf-8")
    if config_local_toml is not None:
        (root / "config.local.toml").write_text(config_local_toml, encoding="utf-8")
    return str(root)


def same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# --- リポジトリ種別の判定 -------------------------------------------------

class TestRepoDetection:
    def test_main_checkout_has_git_directory(self, tmp_path):
        root = make_repo(tmp_path, worktree=False)
        assert paths.is_worktree(root) is False

    def test_worktree_has_git_file(self, tmp_path):
        root = make_repo(tmp_path, worktree=True)
        assert paths.is_worktree(root) is True

    def test_repo_root_of_this_module_contains_backend(self):
        """実リポジトリでの自己解決が壊れていないこと。"""
        root = paths.get_repo_root()
        assert os.path.isdir(os.path.join(root, "backend"))


# --- get_data_root の解決順 -----------------------------------------------

class TestDataRootResolution:
    def test_env_data_root_wins(self, tmp_path, monkeypatch):
        root = make_repo(tmp_path, worktree=True)
        explicit = tmp_path / "explicit"
        explicit.mkdir()
        monkeypatch.setenv("STOCKTOOL_DATA_ROOT", str(explicit))
        assert same(paths.get_data_root(root), str(explicit))

    def test_env_data_root_beats_stocktool_env(self, tmp_path, monkeypatch):
        root = make_repo(tmp_path, worktree=False)
        explicit = tmp_path / "explicit"
        explicit.mkdir()
        monkeypatch.setenv("STOCKTOOL_DATA_ROOT", str(explicit))
        monkeypatch.setenv("STOCKTOOL_ENV", "sandbox")
        assert same(paths.get_data_root(root), str(explicit))

    def test_stocktool_env_sandbox(self, tmp_path, monkeypatch):
        root = make_repo(tmp_path, worktree=False)
        monkeypatch.setenv("STOCKTOOL_ENV", "sandbox")
        assert same(paths.get_data_root(root), os.path.join(root, "data", "sandbox"))

    def test_stocktool_env_test(self, tmp_path, monkeypatch):
        root = make_repo(tmp_path, worktree=False)
        monkeypatch.setenv("STOCKTOOL_ENV", "test")
        assert same(paths.get_data_root(root), os.path.join(root, "data", "test"))

    def test_config_local_data_root(self, tmp_path):
        target = tmp_path / "provisioned"
        target.mkdir()
        root = make_repo(
            tmp_path, worktree=True,
            config_local_toml=f'[data]\nroot = "{target.as_posix()}"\n')
        assert same(paths.get_data_root(root), str(target))

    def test_main_checkout_falls_back_to_config_toml_db_path_parent(self, tmp_path):
        prod = tmp_path / "prod_data"
        prod.mkdir()
        root = make_repo(
            tmp_path, worktree=False,
            config_toml=f'[system]\ndb_path = "{(prod / "stocktool.db").as_posix()}"\n')
        assert same(paths.get_data_root(root), str(prod))

    def test_main_checkout_falls_back_to_repo_data(self, tmp_path):
        root = make_repo(tmp_path, worktree=False)
        assert same(paths.get_data_root(root), os.path.join(root, "data"))


class TestWorktreeRequiresProvisioning:
    """ワークツリーは明示的なプロビジョニングを要求する（本計画の核）。"""

    def test_worktree_without_provisioning_raises(self, tmp_path):
        root = make_repo(tmp_path, worktree=True)
        with pytest.raises(paths.DataNotProvisionedError):
            paths.get_data_root(root)

    def test_worktree_ignores_config_toml(self, tmp_path):
        """config.toml は本番の絶対パスを持つ。ワークツリーでは尊重しない。

        尊重すると「ワークツリーから本番を書ける」経路が残るため。
        """
        prod = tmp_path / "prod_data"
        prod.mkdir()
        root = make_repo(
            tmp_path, worktree=True,
            config_toml=f'[system]\ndb_path = "{(prod / "stocktool.db").as_posix()}"\n')
        with pytest.raises(paths.DataNotProvisionedError):
            paths.get_data_root(root)

    def test_error_message_names_the_provisioning_command(self, tmp_path):
        root = make_repo(tmp_path, worktree=True)
        with pytest.raises(paths.DataNotProvisionedError) as exc:
            paths.get_data_root(root)
        assert "provision_worktree_data" in str(exc.value)


class TestAllowCreateRelaxesWorktreeRules:
    """`STOCKTOOL_ALLOW_DB_CREATE`（pytest / 新規作成タスク）では緩める。

    テストは使い捨て DB を明示パスで新規作成するのが正常な動作であり、
    「ワークツリーは未プロビジョニングなら即死」という規則をそのまま
    適用するとテスト自体が collection 時に落ちる。
    """

    def test_unprovisioned_worktree_falls_back_to_repo_data(self, tmp_path, monkeypatch):
        root = make_repo(tmp_path, worktree=True)
        monkeypatch.setenv("STOCKTOOL_ALLOW_DB_CREATE", "1")
        assert same(paths.get_data_root(root), os.path.join(root, "data"))

    def test_fallback_never_uses_config_toml_production_path(self, tmp_path, monkeypatch):
        """緩めても本番（config.toml の絶対パス）には落ちないこと。"""
        prod = tmp_path / "prod_data"
        prod.mkdir()
        root = make_repo(
            tmp_path, worktree=True,
            config_toml=f'[system]\ndb_path = "{(prod / "stocktool.db").as_posix()}"\n')
        monkeypatch.setenv("STOCKTOOL_ALLOW_DB_CREATE", "1")
        resolved = paths.get_data_root(root)
        assert same(resolved, os.path.join(root, "data"))
        assert not same(resolved, str(prod))

    def test_caller_argument_is_honoured_under_allow_create(self, tmp_path, monkeypatch):
        root = make_repo(tmp_path, worktree=True)
        monkeypatch.setenv("STOCKTOOL_ALLOW_DB_CREATE", "1")
        caller = tmp_path / "tmpdb" / "stocktool.db"
        assert same(
            paths.resolve_db_path_for_init("stocktool", str(caller), root),
            str(caller))

    def test_production_caller_path_is_never_honoured_in_a_worktree(
            self, tmp_path, monkeypatch):
        """緩めても、本番を指す引数だけは絶対に通さない。

        pytest 下で `api.server` を import すると
        `init_db(config["system"]["db_path"])` が走り、その値は
        **config.toml の本番絶対パス**。ワークツリーではこれを黙って
        本番へ向けず、ワークツリー側へ振り替える。
        """
        prod = tmp_path / "prod"
        prod.mkdir()
        wt_data = tmp_path / "wt_data"
        wt_data.mkdir()
        root = make_repo(tmp_path, worktree=True)
        monkeypatch.setenv("STOCKTOOL_ALLOW_DB_CREATE", "1")
        monkeypatch.setenv("STOCKTOOL_PROD_DATA_ROOT", str(prod))
        monkeypatch.setenv("STOCKTOOL_DATA_ROOT", str(wt_data))
        resolved = paths.resolve_db_path_for_init(
            "stocktool", str(prod / "stocktool.db"), root)
        assert same(resolved, str(wt_data / "stocktool.db"))

    def test_main_checkout_may_still_open_production(self, tmp_path):
        """本体からの本番オープンは正常（現行動作を壊さない）。"""
        prod = tmp_path / "prod_data"
        prod.mkdir()
        root = make_repo(
            tmp_path, worktree=False,
            config_toml=f'[system]\ndb_path = "{(prod / "stocktool.db").as_posix()}"\n')
        target = str(prod / "stocktool.db")
        assert same(paths.resolve_db_path_for_init("stocktool", target, root), target)


# --- get_prod_data_root ---------------------------------------------------

class TestProdDataRoot:
    def test_env_wins(self, tmp_path, monkeypatch):
        root = make_repo(tmp_path, worktree=True)
        prod = tmp_path / "prod"
        prod.mkdir()
        monkeypatch.setenv("STOCKTOOL_PROD_DATA_ROOT", str(prod))
        assert same(paths.get_prod_data_root(root), str(prod))

    def test_from_config_local(self, tmp_path):
        prod = tmp_path / "prod"
        prod.mkdir()
        root = make_repo(
            tmp_path, worktree=True,
            config_local_toml=f'[data]\nprod_root = "{prod.as_posix()}"\n')
        assert same(paths.get_prod_data_root(root), str(prod))

    def test_main_checkout_uses_config_toml_even_under_sandbox_env(self, tmp_path, monkeypatch):
        """本体で STOCKTOOL_ENV=sandbox のとき、prod は sandbox ではなく本番を指す。"""
        prod = tmp_path / "prod_data"
        prod.mkdir()
        root = make_repo(
            tmp_path, worktree=False,
            config_toml=f'[system]\ndb_path = "{(prod / "stocktool.db").as_posix()}"\n')
        monkeypatch.setenv("STOCKTOOL_ENV", "sandbox")
        assert same(paths.get_data_root(root), os.path.join(root, "data", "sandbox"))
        assert same(paths.get_prod_data_root(root), str(prod))

    def test_worktree_without_provisioning_returns_none(self, tmp_path):
        root = make_repo(tmp_path, worktree=True)
        assert paths.get_prod_data_root(root) is None


# --- 個別パス -------------------------------------------------------------

class TestConcretePaths:
    def test_db_paths(self, tmp_path, monkeypatch):
        root = make_repo(tmp_path, worktree=True)
        data = tmp_path / "d"
        data.mkdir()
        monkeypatch.setenv("STOCKTOOL_DATA_ROOT", str(data))
        assert same(paths.get_db_path("stocktool", root), str(data / "stocktool.db"))
        assert same(paths.get_db_path("user_data", root), str(data / "user_data.db"))
        assert same(paths.get_db_path("universe", root), str(data / "universe.db"))
        assert same(paths.get_db_path("optimization_trials", root),
                    str(data / "optimization_trials.db"))

    def test_unknown_db_name_raises(self, tmp_path, monkeypatch):
        root = make_repo(tmp_path, worktree=True)
        data = tmp_path / "d"
        data.mkdir()
        monkeypatch.setenv("STOCKTOOL_DATA_ROOT", str(data))
        with pytest.raises(KeyError):
            paths.get_db_path("nope", root)

    def test_parquet_master_dir(self, tmp_path, monkeypatch):
        root = make_repo(tmp_path, worktree=True)
        data = tmp_path / "d"
        data.mkdir()
        monkeypatch.setenv("STOCKTOOL_DATA_ROOT", str(data))
        assert same(paths.get_parquet_master_dir(root), str(data / "parquet_master"))


# --- fail-fast ------------------------------------------------------------

class TestRequireExisting:
    def test_existing_path_passes_through(self, tmp_path):
        f = tmp_path / "a.db"
        f.write_text("x", encoding="utf-8")
        assert paths.require_existing(str(f)) == str(f)

    def test_missing_path_raises_with_recovery_hint(self, tmp_path):
        missing = tmp_path / "missing.db"
        with pytest.raises(paths.DataNotProvisionedError) as exc:
            paths.require_existing(str(missing))
        msg = str(exc.value)
        assert "missing.db" in msg
        assert "provision_worktree_data" in msg
        assert "STOCKTOOL_ALLOW_DB_CREATE" in msg

    def test_allow_create_env_bypasses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STOCKTOOL_ALLOW_DB_CREATE", "1")
        missing = tmp_path / "missing.db"
        assert paths.require_existing(str(missing)) == str(missing)


class TestRequirePopulated:
    """「存在するが中身が空」を通さない。

    ワークツリーに残る `stocktool.db` は 139,264 バイトの**スキーマだけの空DB**
    （pytest が作った残骸）で、存在チェックだけでは素通りしてしまう。
    実際に `ipo-candidates` ワークツリーで観測された症状そのもの。
    """

    def _make_db(self, path, *, table="symbols", rows=0):
        import sqlite3
        conn = sqlite3.connect(str(path))
        if table:
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
            for _ in range(rows):
                conn.execute(f"INSERT INTO {table} DEFAULT VALUES")
        conn.commit()
        conn.close()

    def test_populated_db_passes(self, tmp_path):
        db = tmp_path / "stocktool.db"
        self._make_db(db, rows=3)
        assert paths.require_populated(str(db), "stocktool") == str(db)

    def test_schema_only_empty_db_raises(self, tmp_path):
        db = tmp_path / "stocktool.db"
        self._make_db(db, rows=0)
        with pytest.raises(paths.DataNotProvisionedError) as exc:
            paths.require_populated(str(db), "stocktool")
        assert "provision_worktree_data" in str(exc.value)

    def test_db_without_sentinel_table_raises(self, tmp_path):
        db = tmp_path / "stocktool.db"
        self._make_db(db, table=None)
        with pytest.raises(paths.DataNotProvisionedError):
            paths.require_populated(str(db), "stocktool")

    def test_zero_byte_db_raises(self, tmp_path):
        db = tmp_path / "stocktool.db"
        db.write_bytes(b"")
        with pytest.raises(paths.DataNotProvisionedError):
            paths.require_populated(str(db), "stocktool")

    def test_user_data_is_exempt_because_empty_is_legitimate(self, tmp_path):
        """ウォッチリスト・ポートフォリオが空なのは正常な状態。"""
        db = tmp_path / "user_data.db"
        self._make_db(db, table="watchlist", rows=0)
        assert paths.require_populated(str(db), "user_data") == str(db)

    def test_allow_create_bypasses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STOCKTOOL_ALLOW_DB_CREATE", "1")
        db = tmp_path / "stocktool.db"
        self._make_db(db, rows=0)
        assert paths.require_populated(str(db), "stocktool") == str(db)


# --- 本番への書き込みガード ------------------------------------------------

class TestProductionWriteGuard:
    def test_worktree_cannot_write_into_prod_root(self, tmp_path, monkeypatch):
        prod = tmp_path / "prod"
        prod.mkdir()
        sandbox = tmp_path / "sb"
        sandbox.mkdir()
        root = make_repo(tmp_path, worktree=True)
        monkeypatch.setenv("STOCKTOOL_PROD_DATA_ROOT", str(prod))
        monkeypatch.setenv("STOCKTOOL_DATA_ROOT", str(sandbox))
        with pytest.raises(paths.ProductionWriteError):
            paths.ensure_writable(str(prod / "stocktool.db"), repo_root=root)

    def test_worktree_can_write_into_its_sandbox(self, tmp_path, monkeypatch):
        prod = tmp_path / "prod"
        prod.mkdir()
        sandbox = tmp_path / "sb"
        sandbox.mkdir()
        root = make_repo(tmp_path, worktree=True)
        monkeypatch.setenv("STOCKTOOL_PROD_DATA_ROOT", str(prod))
        monkeypatch.setenv("STOCKTOOL_DATA_ROOT", str(sandbox))
        target = str(sandbox / "stocktool.db")
        assert paths.ensure_writable(target, repo_root=root) == target

    def test_main_checkout_may_write_production(self, tmp_path):
        """本体からの本番書き込みは正常な運用（daily update・昇格）。"""
        prod = tmp_path / "prod_data"
        prod.mkdir()
        root = make_repo(
            tmp_path, worktree=False,
            config_toml=f'[system]\ndb_path = "{(prod / "stocktool.db").as_posix()}"\n')
        target = str(prod / "stocktool.db")
        assert paths.ensure_writable(target, repo_root=root) == target


# --- is_production --------------------------------------------------------

class TestIsProduction:
    def test_main_checkout_default_is_production(self, tmp_path):
        root = make_repo(tmp_path, worktree=False)
        assert paths.is_production(root) is True

    def test_sandbox_env_is_not_production(self, tmp_path, monkeypatch):
        root = make_repo(tmp_path, worktree=False)
        monkeypatch.setenv("STOCKTOOL_ENV", "sandbox")
        assert paths.is_production(root) is False

    def test_worktree_sandbox_is_not_production(self, tmp_path, monkeypatch):
        prod = tmp_path / "prod"
        prod.mkdir()
        sandbox = tmp_path / "sb"
        sandbox.mkdir()
        root = make_repo(tmp_path, worktree=True)
        monkeypatch.setenv("STOCKTOOL_PROD_DATA_ROOT", str(prod))
        monkeypatch.setenv("STOCKTOOL_DATA_ROOT", str(sandbox))
        assert paths.is_production(root) is False


# --- init_db 向けの解決 ----------------------------------------------------

class TestResolveDbPathForInit:
    """`init_db(db_path)` が受け取った引数をどう扱うか。

    本体では**呼び出し側の引数を尊重**して現行動作を保つ。
    ワークツリーでは**paths.py が権威**になる（呼び出し側は config.toml 由来の
    本番絶対パスを渡してくるため、尊重すると本番を書ける経路が残る）。
    """

    def test_main_checkout_honours_caller_argument(self, tmp_path):
        root = make_repo(tmp_path, worktree=False)
        caller = tmp_path / "caller" / "stocktool.db"
        assert same(
            paths.resolve_db_path_for_init("stocktool", str(caller), root),
            str(caller))

    def test_worktree_ignores_caller_argument(self, tmp_path, monkeypatch):
        data = tmp_path / "wt_data"
        data.mkdir()
        root = make_repo(
            tmp_path, worktree=True,
            config_local_toml=f'[data]\nroot = "{data.as_posix()}"\n')
        caller = tmp_path / "prod" / "stocktool.db"
        assert same(
            paths.resolve_db_path_for_init("stocktool", str(caller), root),
            str(data / "stocktool.db"))

    def test_unprovisioned_worktree_raises_instead_of_using_caller_path(self, tmp_path):
        root = make_repo(tmp_path, worktree=True)
        caller = tmp_path / "prod" / "stocktool.db"
        with pytest.raises(paths.DataNotProvisionedError):
            paths.resolve_db_path_for_init("stocktool", str(caller), root)

    def test_stocktool_env_overrides_caller_in_main_checkout(self, tmp_path, monkeypatch):
        root = make_repo(tmp_path, worktree=False)
        monkeypatch.setenv("STOCKTOOL_ENV", "sandbox")
        caller = tmp_path / "prod" / "stocktool.db"
        assert same(
            paths.resolve_db_path_for_init("stocktool", str(caller), root),
            os.path.join(root, "data", "sandbox", "stocktool.db"))

    @pytest.mark.parametrize("name,env", [
        ("stocktool", "STOCKTOOL_DB_PATH"),
        ("user_data", "STOCKTOOL_USER_DB_PATH"),
        ("universe", "STOCKTOOL_UNIVERSE_DB_PATH"),
    ])
    def test_legacy_per_db_env_still_wins(self, tmp_path, monkeypatch, name, env):
        """既存のレガシー環境変数は最優先で維持する（後方互換）。"""
        root = make_repo(tmp_path, worktree=False)
        target = tmp_path / "legacy.db"
        monkeypatch.setenv(env, str(target))
        monkeypatch.setenv("STOCKTOOL_ENV", "sandbox")
        assert same(paths.resolve_db_path_for_init(name, "ignored.db", root),
                    str(target))

    def test_data_root_env_overrides_caller(self, tmp_path, monkeypatch):
        root = make_repo(tmp_path, worktree=False)
        data = tmp_path / "d"
        data.mkdir()
        monkeypatch.setenv("STOCKTOOL_DATA_ROOT", str(data))
        assert same(
            paths.resolve_db_path_for_init("stocktool", "ignored.db", root),
            str(data / "stocktool.db"))


# --- config.local.toml 相乗り ---------------------------------------------

class TestConfigLocalCoexistence:
    def test_sec_and_tls_sections_are_untouched(self, tmp_path):
        """[data] を足しても既存セクションの読み手を壊さない（§4.1）。"""
        data = tmp_path / "d"
        data.mkdir()
        root = make_repo(
            tmp_path, worktree=True,
            config_local_toml=(
                '[sec]\ncontact = "stocktool someone@example.com"\n\n'
                '[tls]\nca_bundle = "C:/certs/bundle.pem"\n\n'
                f'[data]\nroot = "{data.as_posix()}"\n'))
        assert same(paths.get_data_root(root), str(data))

        from data_collection.sec_client import resolve_contact
        assert resolve_contact(root) == "stocktool someone@example.com"
