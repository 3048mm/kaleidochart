"""tools/provision_worktree_data.py のテスト。

実データを触らずに、事故につながる細部（BOM・絶対パス・冪等性）を固定する。
背景: doc/completed/worktree_data_provisioning_plan.md §3.4 / §7
"""
import importlib.util
import json
import os
import sqlite3

import pytest

import paths

_SCRIPT = os.path.join(paths.get_repo_root(), "tools", "provision_worktree_data.py")


def _load():
    spec = importlib.util.spec_from_file_location("_provision", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prov = _load()


# --- config.local.toml の生成 ---------------------------------------------

class TestStripDataSection:
    def test_keeps_other_sections(self):
        text = ('[sec]\ncontact = "a@example.com"\n\n'
                '[data]\nroot = "X"\nprod_root = "Y"\n\n'
                '[tls]\nca_bundle = "Z"\n')
        out = prov.strip_data_section(text)
        assert "[sec]" in out
        assert "[tls]" in out
        assert 'ca_bundle = "Z"' in out
        assert "[data]" not in out
        assert 'root = "X"' not in out

    def test_removes_generated_marker_so_reruns_do_not_accumulate(self):
        text = f'[sec]\ncontact = "a"\n\n{prov.GENERATED_MARKER}\n[data]\nroot = "X"\n'
        out = prov.strip_data_section(text)
        assert prov.GENERATED_MARKER not in out

    def test_empty_input(self):
        assert prov.strip_data_section("") == ""


class TestWriteConfigLocal:
    def _main(self, tmp_path, content=None):
        main = tmp_path / "main"
        main.mkdir()
        if content is not None:
            (main / "config.local.toml").write_text(content, encoding="utf-8")
        return str(main)

    def test_written_without_bom_and_with_lf(self, tmp_path):
        main = self._main(tmp_path, '[sec]\ncontact = "a@example.com"\n')
        wt = tmp_path / "wt"
        wt.mkdir()
        dst = prov.write_config_local(str(wt), main,
                                      str(wt / "data"), os.path.join(main, "data"))
        raw = open(dst, "rb").read()
        # BOM は get_latest_master_files() を黙って None にする（§7-2）
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in raw

    def test_preserves_sec_and_tls(self, tmp_path):
        main = self._main(
            tmp_path,
            '[sec]\ncontact = "someone@example.com"\n\n[tls]\nca_bundle = "C:/x.pem"\n')
        wt = tmp_path / "wt"
        wt.mkdir()
        dst = prov.write_config_local(str(wt), main,
                                      str(wt / "data"), os.path.join(main, "data"))
        import tomllib
        conf = tomllib.load(open(dst, "rb"))
        assert conf["sec"]["contact"] == "someone@example.com"
        assert conf["tls"]["ca_bundle"] == "C:/x.pem"
        assert conf["data"]["root"] == (wt / "data").as_posix()

    def test_is_idempotent(self, tmp_path):
        main = self._main(tmp_path, '[sec]\ncontact = "a@example.com"\n')
        wt = tmp_path / "wt"
        wt.mkdir()
        args = (str(wt), main, str(wt / "data"), os.path.join(main, "data"))
        first = open(prov.write_config_local(*args), encoding="utf-8").read()
        second = open(prov.write_config_local(*args), encoding="utf-8").read()
        assert first == second

    def test_works_when_main_has_no_config_local(self, tmp_path):
        main = self._main(tmp_path)
        wt = tmp_path / "wt"
        wt.mkdir()
        dst = prov.write_config_local(str(wt), main,
                                      str(wt / "data"), os.path.join(main, "data"))
        import tomllib
        conf = tomllib.load(open(dst, "rb"))
        assert conf["data"]["prod_root"] == os.path.join(main, "data").replace(os.sep, "/")


# --- Parquet のハードリンク ------------------------------------------------

class TestLinkParquetMaster:
    def _make_master(self, tmp_path):
        src = tmp_path / "prod" / "parquet_master"
        src.mkdir(parents=True)
        pointer = {}
        for key in ("prices", "indicators"):
            f = src / f"{key}_20260829_144251.parquet"
            f.write_bytes(b"PARQUET-" + key.encode())
            pointer[key] = str(f)
        (src / "latest_master.json").write_text(
            json.dumps(pointer, indent=2), encoding="utf-8", newline="\n")
        (src / "data_version_20260829_144251.json").write_text(
            "{}", encoding="utf-8", newline="\n")
        return str(src), pointer

    def test_files_are_hardlinked_not_copied(self, tmp_path):
        src, pointer = self._make_master(tmp_path)
        dst = str(tmp_path / "wt" / "sandbox" / "parquet_master")
        assert prov.link_parquet_master(src, dst) == 2
        for original in pointer.values():
            linked = os.path.join(dst, os.path.basename(original))
            assert os.path.samefile(linked, original)
            assert os.stat(linked).st_nlink == 2

    def test_pointer_is_rewritten_to_sandbox(self, tmp_path):
        """本番の絶対パスが残ると、読めてしまうので誤りに気づけない（§7-3）。"""
        src, _ = self._make_master(tmp_path)
        dst = str(tmp_path / "wt" / "sandbox" / "parquet_master")
        prov.link_parquet_master(src, dst)
        written = json.load(open(os.path.join(dst, "latest_master.json"),
                                 encoding="utf-8"))
        for value in written.values():
            assert os.path.dirname(os.path.abspath(value)) == os.path.abspath(dst)
            assert os.path.abspath(src) != os.path.dirname(os.path.abspath(value))

    def test_pointer_written_without_bom(self, tmp_path):
        src, _ = self._make_master(tmp_path)
        dst = str(tmp_path / "wt" / "sandbox" / "parquet_master")
        prov.link_parquet_master(src, dst)
        raw = open(os.path.join(dst, "latest_master.json"), "rb").read()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in raw

    def test_reads_pointer_even_if_source_has_bom(self, tmp_path):
        src, pointer = self._make_master(tmp_path)
        with open(os.path.join(src, "latest_master.json"), "wb") as f:
            f.write(b"\xef\xbb\xbf" + json.dumps(pointer).encode("utf-8"))
        dst = str(tmp_path / "wt" / "sandbox" / "parquet_master")
        assert prov.link_parquet_master(src, dst) == 2

    def test_is_idempotent(self, tmp_path):
        src, _ = self._make_master(tmp_path)
        dst = str(tmp_path / "wt" / "sandbox" / "parquet_master")
        assert prov.link_parquet_master(src, dst) == 2
        assert prov.link_parquet_master(src, dst) == 0

    def test_data_version_files_are_copied(self, tmp_path):
        src, _ = self._make_master(tmp_path)
        dst = str(tmp_path / "wt" / "sandbox" / "parquet_master")
        prov.link_parquet_master(src, dst)
        assert os.path.exists(os.path.join(dst, "data_version_20260829_144251.json"))

    def test_missing_pointer_target_aborts(self, tmp_path):
        src, pointer = self._make_master(tmp_path)
        os.remove(pointer["prices"])
        dst = str(tmp_path / "wt" / "sandbox" / "parquet_master")
        with pytest.raises(SystemExit):
            prov.link_parquet_master(src, dst)

    def test_missing_master_pointer_aborts(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(SystemExit):
            prov.link_parquet_master(str(empty), str(tmp_path / "dst"))


# --- SQLite の複製 --------------------------------------------------------

class TestCopySqlite:
    def _make_db(self, path):
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('hello')")
        conn.commit()
        conn.close()

    def test_copy_is_a_real_independent_file(self, tmp_path):
        src = str(tmp_path / "src.db")
        dst = str(tmp_path / "dst.db")
        self._make_db(src)
        prov.copy_sqlite(src, dst)
        assert not os.path.samefile(src, dst)  # ハードリンクではない
        conn = sqlite3.connect(dst)
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "hello"
        conn.close()

    def test_copy_survives_wal_mode(self, tmp_path):
        """WAL の内容を取りこぼさないこと（ファイルコピーだと落とし得る）。"""
        src = str(tmp_path / "src.db")
        self._make_db(src)
        conn = sqlite3.connect(src)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO t (v) VALUES ('in-wal')")
        conn.commit()
        dst = str(tmp_path / "dst.db")
        prov.copy_sqlite(src, dst)
        conn.close()
        out = sqlite3.connect(dst)
        values = {r[0] for r in out.execute("SELECT v FROM t")}
        out.close()
        assert values == {"hello", "in-wal"}

    def test_populated_destination_is_skipped(self, tmp_path):
        """中身のある既存コピーは上書きしない（冪等）。"""
        src = str(tmp_path / "src.db")
        dst = str(tmp_path / "dst.db")
        self._make_db(src)
        conn = sqlite3.connect(dst)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('already-here')")
        conn.commit()
        conn.close()
        prov.copy_sqlite(src, dst)
        out = sqlite3.connect(dst)
        assert out.execute("SELECT v FROM t").fetchone()[0] == "already-here"
        out.close()

    def test_schema_only_destination_is_replaced(self, tmp_path):
        """スキーマだけの空DB（pytest 残骸）は「既存」扱いせず作り直す。

        実際に踏んだ: `data/sandbox/stocktool.db` が 139,264 バイトの空DBとして
        残っており、プロビジョニングが「既存」とスキップした結果、
        塞ぎたかった空DBがそのまま残った。
        """
        src = str(tmp_path / "src.db")
        dst = str(tmp_path / "dst.db")
        self._make_db(src)
        conn = sqlite3.connect(dst)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")  # 0 行
        conn.commit()
        conn.close()
        prov.copy_sqlite(src, dst)
        out = sqlite3.connect(dst)
        assert out.execute("SELECT v FROM t").fetchone()[0] == "hello"
        out.close()

    def test_zero_byte_destination_is_replaced(self, tmp_path):
        src = str(tmp_path / "src.db")
        dst = str(tmp_path / "dst.db")
        self._make_db(src)
        open(dst, "wb").close()
        prov.copy_sqlite(src, dst)
        out = sqlite3.connect(dst)
        assert out.execute("SELECT v FROM t").fetchone()[0] == "hello"
        out.close()

    def test_corrupt_destination_is_replaced(self, tmp_path):
        src = str(tmp_path / "src.db")
        dst = str(tmp_path / "dst.db")
        self._make_db(src)
        with open(dst, "wb") as f:
            f.write(b"NOT A SQLITE FILE")
        prov.copy_sqlite(src, dst)
        out = sqlite3.connect(dst)
        assert out.execute("SELECT v FROM t").fetchone()[0] == "hello"
        out.close()


class TestIsEmptySqlite:
    def test_populated(self, tmp_path):
        p = str(tmp_path / "a.db")
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO t DEFAULT VALUES")
        conn.commit()
        conn.close()
        assert prov.is_empty_sqlite(p) is False

    def test_schema_only(self, tmp_path):
        p = str(tmp_path / "a.db")
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        assert prov.is_empty_sqlite(p) is True

    def test_no_tables(self, tmp_path):
        p = str(tmp_path / "a.db")
        sqlite3.connect(p).close()
        assert prov.is_empty_sqlite(p) is True

    def test_not_a_database(self, tmp_path):
        p = str(tmp_path / "a.db")
        with open(p, "wb") as f:
            f.write(b"garbage")
        assert prov.is_empty_sqlite(p) is True
