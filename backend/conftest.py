import sys
import os

# Ensure 'backend/' itself is on sys.path so that 'indicators', 'backtest', etc.
# are importable as top-level packages.
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
