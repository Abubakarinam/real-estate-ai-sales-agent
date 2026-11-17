"""Save or load the recorded demo data (real conversations made with
scripts/demo_conversations.py), so anyone can see a full dashboard without an API key.

    python scripts/demo_data.py export   -> demo/demo-data.json
    python scripts/demo_data.py load     -> replaces the local database with the demo data
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db, knowledge  # noqa: E402

FILE = config.ROOT / "demo" / "demo-data.json"
TABLES = ["conversations", "messages", "leads", "viewings", "events"]


def export(path: Path = FILE) -> Path:
    data = {"recorded_at": db.now(), "local_now": datetime.now().strftime("%Y-%m-%dT%H:%M")}
    for t in TABLES:
        data[t] = db.query(f"SELECT * FROM {t} ORDER BY rowid")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _shift(value: str | None, delta: timedelta, fmt: str) -> str | None:
    if not value:
        return value
    try:
        return (datetime.strptime(value, fmt) + delta).strftime(fmt)
    except ValueError:
        return value


def load(path: Path = FILE) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    recorded = datetime.strptime(data["recorded_at"], "%Y-%m-%dT%H:%M:%SZ")
    # move timestamps forward so the demo looks like it happened just now; viewings move by
    # whole weeks, so they stay in the future and on the same weekday (no Sunday viewings)
    gap = datetime.now(timezone.utc).replace(tzinfo=None) - recorded
    delta = timedelta(days=gap.days)
    weeks = timedelta(weeks=-(-gap.days // 7))
    with db.tx() as c:
        for t in TABLES:
            c.execute(f"DELETE FROM {t}")
        for t in TABLES:
            for row in data[t]:
                for k in ("created_at", "updated_at", "handoff_at"):
                    if k in row:
                        row[k] = _shift(row[k], delta, "%Y-%m-%dT%H:%M:%SZ")
                if t == "viewings":
                    row["start_at"] = _shift(row["start_at"], weeks, "%Y-%m-%dT%H:%M")
                cols = ", ".join(row)
                c.execute(f"INSERT INTO {t} ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    knowledge.ensure_loaded()
    print(f"Loaded {len(data['conversations'])} recorded conversations into {config.DB_PATH}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "export":
        print("Wrote", export())
    elif cmd == "load":
        load()
    else:
        print(__doc__)
