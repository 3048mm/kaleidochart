import os
import sys
import logging
import subprocess
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
        sys.exit(process.returncode)
    else:
        logger.info("Database update completed successfully.")

if __name__ == "__main__":
    logger.info("=== Daily Update Boot Sequence ===")
    
    # Lock management has been moved to update_pipeline.py to ensure 
    # singleton execution across both manual and automated runs.
    try:
        run_update()
    except Exception as e:
        logger.error(f"Daily update job failed: {e}")
        sys.exit(1)
