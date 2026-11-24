#!/usr/bin/env python3
"""AQ Clipz Lead Engine.

  python3 run.py                       start the dashboard (http://127.0.0.1:8000)
  python3 run.py snapshot-export FILE  write every table to a JSON file (safe to commit)
  python3 run.py snapshot-import FILE  restore a JSON snapshot into an empty database
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from leadengine.db import Store  # noqa: E402
from leadengine.server import serve  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "serve":
        serve()
        return 0
    if argv[0] == "snapshot-export" and len(argv) == 2:
        Path(argv[1]).write_text(json.dumps(Store().export_snapshot(), indent=1, default=str))
        print(f"Wrote {argv[1]}")
        return 0
    if argv[0] == "snapshot-import" and len(argv) == 2:
        Store().import_snapshot(json.loads(Path(argv[1]).read_text()))
        print("Imported")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
