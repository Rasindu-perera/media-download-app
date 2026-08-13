import tempfile
import os
import atexit
import shutil

# Create a temporary directory in the OS default temp location
GLOBAL_TMP_DIR = tempfile.mkdtemp(prefix="media_downloader_")

def cleanup_global_tmp():
    """Remove the global temp directory on exit"""
    try:
        if os.path.exists(GLOBAL_TMP_DIR):
            shutil.rmtree(GLOBAL_TMP_DIR)
            print(f"Cleaned up global temp directory: {GLOBAL_TMP_DIR}")
    except Exception as e:
        print(f"Error cleaning up global temp directory: {e}")

atexit.register(cleanup_global_tmp)
