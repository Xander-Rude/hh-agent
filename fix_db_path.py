from __future__ import annotations

from datetime import datetime
from pathlib import Path
import py_compile
import shutil

DB_FILE = Path(r"C:\hh-agent\app\db.py")
OLD = 'DB_PATH = Path("data/hh_agent.db")'
NEW = '''ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "hh_agent.db"'''

if not DB_FILE.exists():
    raise SystemExit(f"ERROR: file not found: {DB_FILE}")

text = DB_FILE.read_text(encoding="utf-8-sig")

if NEW in text:
    print("Already fixed.")
    print(r"DB path: C:\hh-agent\data\hh_agent.db")
    raise SystemExit(0)

if OLD not in text:
    raise SystemExit(
        "ERROR: expected DB_PATH line not found. File was NOT changed.\n"
        f"Open {DB_FILE} and inspect DB_PATH manually."
    )

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = DB_FILE.with_name(f"db.py.bak_{stamp}")
shutil.copy2(DB_FILE, backup)

patched = text.replace(OLD, NEW, 1)
DB_FILE.write_text(patched, encoding="utf-8")

try:
    py_compile.compile(str(DB_FILE), doraise=True)
except Exception:
    shutil.copy2(backup, DB_FILE)
    raise

print("OK: db.py fixed and syntax checked.")
print(f"Backup: {backup}")
print(r"All processes will now use: C:\hh-agent\data\hh_agent.db")
