import os
import sys
import logging
import yfinance as yf
import subprocess
import msvcrt
import tomllib
from datetime import datetime

# Windows encoding safety
import codecs
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

# Add project root for config.toml access
project_root = os.path.dirname(backend_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

LOCK_FILE = "update_pipeline.lock"
lock_fd = None

def acquire_lock():
    """Attempt to acquire an exclusive lock on the lock file. Returns True if successful."""
    global lock_fd
    try:
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        # LK_NBLCK means non-blocking lock. Will raise IOError if already locked by another process.
        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
        return True
    except (IOError, OSError):
        return False
        
def release_lock():
    """Release the lock."""
    global lock_fd
    if lock_fd is not None:
        try:
            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
            os.close(lock_fd)
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except Exception as e:
            logger.error(f"Error releasing lock: {e}")

def run_update():
    logger.info("Starting update_pipeline.py calculation...")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    
    # Run the main pipeline (ensure it runs in the scripts directory or with absolute path)
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_pipeline.py")
    process = subprocess.run([sys.executable, script_path], env=env)
    
    if process.returncode != 0:
        logger.error(f"update_pipeline.py exited with error code {process.returncode}")
    else:
        logger.info("Database update completed successfully.")

if __name__ == "__main__":
    logger.info("=== Daily Update Boot Sequence ===")
    
    # Check concurrent execution
    if not acquire_lock():
        logger.warning("Another instance of the update script is already running. Exiting.")
        sys.exit(0)
        
    try:
        # We now rely on update_pipeline.py's own autonomous catch-up logic
        # which is more robust than a target-less date comparison on SPY only.
        run_update()
    finally:
        # Guarantee lock release even if crashes
        release_lock()
