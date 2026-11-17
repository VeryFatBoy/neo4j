"""Change station status in the .cl file from the command line, while the MCP server is NOT running
(Relate allows one writer at a time).

  python disrupt.py show
  python disrupt.py suspend "Green Park"
  python disrupt.py delay "Goodge Street" 5
  python disrupt.py clear "Green Park"
  python disrupt.py clear-all
"""

import os
import relate
import sys
import walker

def main(argv):
    if not argv or argv[0] not in ("show", "suspend", "delay", "clear", "clear-all"):
        print(__doc__)
        return 1
    db = relate.connect(os.environ.get("WALK_DB", "walk.cl"))
    try:
        cmd = argv[0]
        if cmd == "clear-all":
            walker.clear_all(db)
        elif cmd != "show":
            if len(argv) < 2:
                print("Give a station name.")
                return 1
            if cmd == "delay":
                walker.set_status(db, argv[1], "delayed", int(argv[2]) if len(argv) > 2 else 5)
            else:
                walker.set_status(db, argv[1], "suspended" if cmd == "suspend" else "clear")
        status = walker.station_status(db)
        if not status:
            print("All stations running normally.")
        for name, (off, late) in sorted(status.items()):
            print(f"{name}: {'suspended' if off else f'delayed {late} min'}")
    except ValueError as exc:
        print(exc)
        return 1
    finally:
        db.close()
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
