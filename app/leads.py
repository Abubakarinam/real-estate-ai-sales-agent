"""Leads (the CRM side): create/update, score, and send to an optional webhook."""
import json
import threading
import urllib.request

from . import config, db, scoring

FIELDS = [
    "name", "phone", "email", "language", "intent", "property_type", "areas", "bedrooms",
    "budget_min", "budget_max", "timeline", "financing", "interested_listings", "notes",
    "summary", "next_step",
]


def get_by_conversation(conv_id: str) -> dict | None:
    return db.one("SELECT * FROM leads WHERE conversation_id=?", (conv_id,))


def get(lead_id: int) -> dict | None:
    return db.one("SELECT * FROM leads WHERE id=?", (lead_id,))


def has_viewing(lead_id: int) -> bool:
    return bool(db.one("SELECT id FROM viewings WHERE lead_id=? AND status='booked'", (lead_id,)))


def ensure(conv_id: str) -> dict:
    lead = get_by_conversation(conv_id)
    if lead:
        return lead
    with db.tx() as c:
        c.execute(
            "INSERT INTO leads(conversation_id, created_at, updated_at) VALUES (?,?,?)",
            (conv_id, db.now(), db.now()),
        )
    return get_by_conversation(conv_id)


def update(conv_id: str, changes: dict, stage: str | None = None) -> dict:
    lead = ensure(conv_id)
    clean = {}
    for k, v in changes.items():
        if k not in FIELDS or v in (None, "", []):
            continue
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        if k in ("budget_min", "budget_max"):
            try:
                v = int(float(v))
            except (TypeError, ValueError):
                continue
        clean[k] = v
    merged = {**lead, **clean}
    score, temp, reasons = scoring.score_lead(merged, has_viewing(lead["id"]))
    clean.update(score=score, temperature=temp, score_reasons=json.dumps(reasons))
    if stage:
        clean["stage"] = stage
    elif lead["stage"] == "new" and temp in ("hot", "warm"):
        clean["stage"] = "qualified"
    clean["updated_at"] = db.now()
    sets = ", ".join(f"{k}=?" for k in clean)
    with db.tx() as c:
        c.execute(f"UPDATE leads SET {sets} WHERE id=?", (*clean.values(), lead["id"]))
    fresh = get(lead["id"])
    send_webhook("lead.updated", fresh)
    return fresh


def rescore(lead_id: int) -> dict:
    lead = get(lead_id)
    return update(lead["conversation_id"], {})


def missing_fields(lead: dict) -> list[str]:
    wanted = {
        "budget": lead.get("budget_max") or lead.get("budget_min"),
        "area or property type": lead.get("areas") or lead.get("property_type"),
        "timeline": lead.get("timeline"),
        "financing": lead.get("financing"),
        "name": lead.get("name"),
        "phone or email": lead.get("phone") or lead.get("email"),
    }
    return [k for k, v in wanted.items() if not v]


def public(lead: dict) -> dict:
    out = dict(lead)
    out["score_reasons"] = json.loads(lead.get("score_reasons") or "[]")
    return out


def send_webhook(event: str, payload: dict) -> None:
    """Fire-and-forget POST to n8n / Make / Zapier. Never blocks or breaks the chat."""
    if not config.LEAD_WEBHOOK_URL:
        return
    body = json.dumps({"event": event, "agency": config.AGENCY_NAME, "data": public(payload) if "score_reasons" in payload else payload},
                      ensure_ascii=False, default=str).encode()

    def _post():
        try:
            req = urllib.request.Request(config.LEAD_WEBHOOK_URL, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=8).read()
        except Exception as exc:  # noqa: BLE001
            db.log_event("webhook.failed", payload.get("conversation_id"), {"event": event, "error": str(exc)[:200]})

    threading.Thread(target=_post, daemon=True).start()
