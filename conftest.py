import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Keep test databases and media out of the user's normal data directory.
TEST_DATA = Path(tempfile.mkdtemp(prefix="clipforge-tests-"))
os.environ["DATA_DIR"] = str(TEST_DATA)
os.environ["APP_TIMEZONE"] = "Asia/Kolkata"
