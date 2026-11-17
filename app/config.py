"""Settings, read from environment variables (or a .env file next to run.py)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

AGENCY_NAME = os.getenv("AGENCY_NAME", "Harbor & Key Realty")
AGENCY_CITY = os.getenv("AGENCY_CITY", "Dubai")
TIMEZONE = os.getenv("AGENCY_TIMEZONE", "Asia/Dubai")

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
# low effort keeps chat replies fast and cheap; raise to "medium" for harder sales conversations
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "low")
# server-side refusal fallback (on by default on the Claude API); set to 0 on platforms without it
CLAUDE_FALLBACKS = os.getenv("CLAUDE_FALLBACKS", "1") == "1"

DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "agent.db"))
KNOWLEDGE_DIR = Path(os.getenv("KNOWLEDGE_DIR", ROOT / "knowledge"))
LISTINGS_FILE = Path(os.getenv("LISTINGS_FILE", ROOT / "data" / "listings.json"))

# Dashboard login. Change it before you put the app online.
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "demo1234")
SECRET_KEY = os.getenv("SECRET_KEY", ADMIN_PASSWORD + "-session-key")

# Optional: every new or updated lead, booking and handoff is POSTed here as JSON
# (an n8n, Make or Zapier webhook that writes to Google Sheets, HubSpot, etc.)
LEAD_WEBHOOK_URL = os.getenv("LEAD_WEBHOOK_URL", "").strip()

# Viewing calendar
VIEWING_DAYS = [0, 1, 2, 3, 4, 5]  # Monday..Saturday
VIEWING_HOURS = list(range(10, 18))  # slots start 10:00 .. 17:00, 1 hour each
MAX_TURNS_PER_CONVERSATION = int(os.getenv("MAX_TURNS_PER_CONVERSATION", "60"))
