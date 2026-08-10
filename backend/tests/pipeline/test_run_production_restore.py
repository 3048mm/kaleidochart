"""本番キャッシュ復元のロック処理テスト（scripts/run_production_restore.py）

## なぜ直すか

`run_production_restore.py` は `stocktool.db` を**ファイルごと削除**して Parquet から
作り直す。削除に失敗すると「active lock を検知した」として**即座に `DROP ALL TABLES`**
へフォールバックする設計だった。問題が3つある。

1. **ロックの主体を特定しないまま破壊的操作へ進む。** 誰が掴んでいるのか分からないので
   運用者は何を止めればよいか分からない。
2. **1回失敗しただけで諦める。** 終了直前のリクエストが握っている程度の一過性の
   ハンドルでも、遅い破壊的経路に落ちる。
3. **`DROP ALL` は読み取りロックに阻まれて長時間止まりうる。**
   2026-08-06 の実測では "Forcing DROP ALL tables" から**5分以上復帰せず**（CPU 8秒）、
   プロセスを落として uvicorn 停止後に再実行する羽目になった。

このスクリプトは `refresh_All.bat` の最終段・`deploy_after_merge.py` からも呼ばれるため、
中途半端に壊れると復旧の難易度が跳ね上がる。**掴んでいる相手を報告して止まる**のが正しい。
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

from backend.scripts.run_production_restore import (  # noqa: E402
    DELETE_RETRY_ATTEMPTS,
    delete_with_retry,
    describe_lock_holders,
)


class TestDeleteWithRetry:
    """一過性のハンドルで破壊的経路に落ちないこと。"""

    def test_deletes_when_unlocked(self, tmp_path):
        f = tmp_path / "a.db"
        f.write_text("x", encoding="utf-8")

        ok, stuck = delete_with_retry([str(f)], sleep=lambda _s: None)

        assert ok is True and stuck == []
        assert not f.exists()

    def test_missing_files_are_not_an_error(self, tmp_path):
        """`-wal` / `-shm` は存在しないことが普通。"""
        ok, stuck = delete_with_retry([str(tmp_path / "nope.db")], sleep=lambda _s: None)

        assert ok is True and stuck == []

    def test_retries_a_transient_lock(self, tmp_path):
        """**2回目で消えるなら破壊的経路に落とさない。**

        終了直前のリクエストがハンドルを握っている、程度のことは普通に起きる。
        """
        f = tmp_path / "a.db"
        f.write_text("x", encoding="utf-8")
        calls = []

        def flaky(path):
            calls.append(path)
            if len(calls) < 2:
                raise PermissionError("locked")
            os.remove(path)

        ok, stuck = delete_with_retry([str(f)], remove=flaky, sleep=lambda _s: None)

        assert ok is True and stuck == []
        assert len(calls) == 2, "リトライしていない"

    def test_gives_up_after_bounded_attempts(self, tmp_path):
        """無限に粘らない（復旧作業を止めない）。"""
        f = tmp_path / "a.db"
        f.write_text("x", encoding="utf-8")

        def always_locked(_path):
            raise PermissionError("locked")

        ok, stuck = delete_with_retry([str(f)], remove=always_locked, sleep=lambda _s: None)

        assert ok is False
        assert stuck == [str(f)]

    def test_reports_every_stuck_file(self, tmp_path):
        """どのファイルが掴まれているかを全部返す（1件で打ち切らない）。"""
        a, b = tmp_path / "a.db", tmp_path / "b.db-wal"
        for f in (a, b):
            f.write_text("x", encoding="utf-8")

        ok, stuck = delete_with_retry([str(a), str(b)],
                                      remove=lambda _p: (_ for _ in ()).throw(PermissionError()),
                                      sleep=lambda _s: None)

        assert ok is False
        assert set(stuck) == {str(a), str(b)}

    def test_waits_between_attempts(self, tmp_path):
        f = tmp_path / "a.db"
        f.write_text("x", encoding="utf-8")
        slept = []

        delete_with_retry([str(f)],
                          remove=lambda _p: (_ for _ in ()).throw(PermissionError()),
                          sleep=slept.append)

        assert len(slept) == DELETE_RETRY_ATTEMPTS - 1, "待たずに連打している"
        assert all(s > 0 for s in slept)


class TestDescribeLockHolders:
    """**何を止めればよいかを名指しする。**

    2026-08-03 の事故では uvicorn の**孤児の子プロセス**がハンドルを握っており、
    「サーバーを止めたのに解消しない」で原因特定に時間がかかった。
    厄介なのは、ファイルハンドルは掴んでいるが SQLite のロックは持っていないこと
    （`BEGIN IMMEDIATE` が通るので「DB は空いている」と誤認できてしまう）。

    > [!WARNING]
    > **`psutil.Process.open_files()` は使えない。** Windows で `access violation` を
    > 起こし、**インタプリタごと落ちる**（`except` で捕捉できない。2026-08-09 に実測）。
    > 厳密な特定は諦め、**同じプロジェクトを触っている Python プロセス**を候補に挙げる。
    """

    def test_returns_a_list_of_descriptions(self, tmp_path):
        f = tmp_path / "a.db"
        f.write_text("x", encoding="utf-8")

        got = describe_lock_holders(str(f))

        assert isinstance(got, list)

    def test_never_raises_for_a_missing_file(self, tmp_path):
        """診断が例外を投げて復旧を止めない。"""
        assert isinstance(describe_lock_holders(str(tmp_path / "nope.db")), list)

    def test_does_not_crash_the_interpreter(self, tmp_path):
        """**プロセス走査でインタプリタを落とさない。**

        `open_files()` を使った実装はここで access violation を起こして
        pytest ごと死んだ。このテストが通ること自体が回帰防止になる。
        """
        pytest.importorskip("psutil")
        f = tmp_path / "a.db"
        f.write_text("x", encoding="utf-8")

        for _ in range(3):
            describe_lock_holders(str(f))

    def test_lists_processes_working_on_the_same_project(self):
        """本番 DB を指すと、同じプロジェクトを触っている Python を候補に挙げる。"""
        pytest.importorskip("psutil")

        got = describe_lock_holders(os.path.join(project_root, "data", "stocktool.db"))

        assert isinstance(got, list)
        assert all(isinstance(g, str) and "PID" in g for g in got)

    def test_matches_processes_by_project_root_not_data_dir(self, monkeypatch):
        """**プロジェクトルートで照合する。**

        DB は `<root>/data/stocktool.db` だが、本命の uvicorn は
        `<root>/venv/Scripts/uvicorn.exe` として起動する。`data/` で照合すると
        API サーバーが候補に挙がらず、「止めるべきもの」を示せない
        （2026-08-10 に実際に空リストになった）。
        """
        pytest.importorskip("psutil")
        import psutil

        root = os.path.abspath(os.path.join("C:", os.sep, "proj"))
        db = os.path.join(root, "data", "stocktool.db")

        class FakeProc:
            info = {"pid": 4242, "name": "python.exe",
                    "cmdline": [os.path.join(root, "venv", "Scripts", "uvicorn.exe"),
                                "api.server:app"]}

        monkeypatch.setattr(psutil, "process_iter", lambda _attrs: [FakeProc()])

        got = describe_lock_holders(db)

        assert any("4242" in g for g in got), f"uvicorn を候補に挙げられていない: {got}"

    def test_ignores_processes_outside_the_project(self, monkeypatch):
        pytest.importorskip("psutil")
        import psutil

        class FakeProc:
            info = {"pid": 99, "name": "python.exe",
                    "cmdline": [r"C:\other\thing.py"]}

        monkeypatch.setattr(psutil, "process_iter", lambda _attrs: [FakeProc()])

        assert describe_lock_holders(r"C:\proj\data\stocktool.db") == []

    def test_does_not_list_itself(self, monkeypatch):
        """自分自身を「止めてください」と言わない。

        venv の `python.exe` はラッパーと実体で PID が分かれるため、
        `os.getpid()` の除外だけでは自分が候補に残る（2026-08-10 に実測）。
        """
        pytest.importorskip("psutil")
        import psutil

        root = os.path.abspath(os.path.join("C:", os.sep, "proj"))

        class SelfProc:
            info = {"pid": 4242, "name": "python.exe",
                    "cmdline": [os.path.join(root, "venv", "Scripts", "python.exe"),
                                "backend/scripts/run_production_restore.py"]}

        monkeypatch.setattr(psutil, "process_iter", lambda _attrs: [SelfProc()])

        assert describe_lock_holders(os.path.join(root, "data", "stocktool.db")) == []


class TestRestoreFlow:
    """削除の成否で分岐する制御フローそのものを押さえる。

    正常系（削除できた → Parquet から復元）に自動テストが無かったため、
    ロック処理を直したときに壊しても気づけない状態だった。
    """

    @pytest.fixture
    def prod_db(self, tmp_path):
        p = tmp_path / "stocktool.db"
        p.write_bytes(b"")
        return str(p)

    def _patch(self, monkeypatch, restored):
        """重い復元本体だけ差し替え、分岐は本物を通す。"""
        import backend.scripts.run_production_restore as mod

        monkeypatch.setattr(mod, "restore_sqlite_cache_from_parquet",
                            lambda db, path, logger: restored.append(path))
        monkeypatch.setattr(mod, "update_pipeline_meta", lambda *a, **k: None, raising=False)
        return mod

    def test_proceeds_to_restore_when_files_are_deletable(self, prod_db, monkeypatch):
        restored = []
        mod = self._patch(monkeypatch, restored)

        ok = mod.run_production_restore(prod_db)

        assert ok is True
        assert restored == [os.path.abspath(prod_db)], "復元本体が呼ばれていない"

    def test_aborts_without_restoring_when_locked(self, prod_db, monkeypatch):
        """**ロックされていたら復元に進まない。**

        以前は即 DROP ALL へ落ちていた。掴んでいる相手が分からないまま
        破壊的操作に進むと、中途半端な状態で失敗して復旧が難しくなる。
        """
        restored = []
        mod = self._patch(monkeypatch, restored)
        monkeypatch.setattr(mod, "delete_with_retry", lambda paths: (False, [prod_db]))

        ok = mod.run_production_restore(prod_db)

        assert ok is False
        assert restored == [], "ロックされているのに復元へ進んでいる"

    def test_force_falls_back_and_restores(self, prod_db, monkeypatch):
        """`--force` なら従来どおり DROP ALL 経由で続行する（逃げ道は残す）。"""
        restored = []
        mod = self._patch(monkeypatch, restored)
        monkeypatch.setattr(mod, "delete_with_retry", lambda paths: (False, [prod_db]))

        ok = mod.run_production_restore(prod_db, force=True)

        assert ok is True
        assert restored, "--force でも復元へ進んでいない"
