import sys
import os

# Ensure 'backend/' itself is on sys.path so that 'indicators', 'backtest', etc.
# are importable as top-level packages.
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# テストは使い捨ての DB を新規作成するのが正常な動作なので、
# init_db() 系の fail-fast（存在しない DB を黙って作らせないガード）を解除する。
# 本番/ワークツリーでの実行では設定されないため、ガードは効いたまま。
# 設計: doc/in_progress/worktree_data_provisioning_plan.md §3.2
os.environ.setdefault("STOCKTOOL_ALLOW_DB_CREATE", "1")
