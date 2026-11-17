"""Web server: customer chat API + staff dashboard API."""
import csv
import hashlib
import hmac
import io
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, scheduling as calendar, config, db, knowledge, leads, listings, scoring

WEB = config.ROOT / "web"


@asynccontextmanager
async def lifespan(_app):
    db.get()
    knowledge.ensure_loaded()
    yield


app = FastAPI(title=f"{config.AGENCY_NAME} AI Sales Agent", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB), name="static")

GREETING = (
    f"Hi, welcome to {config.AGENCY_NAME}! I can help you buy, rent or invest in {config.AGENCY_CITY}, "
    "answer questions about fees, payment plans and the Golden Visa, and book a viewing.\n\n"
    "مرحباً بك! يمكنك التحدث معي بالعربية أيضاً.\n\nWhat are you looking for?"
)


# ---------------------------------------------------------------- pages

@app.get("/", include_in_schema=False)
def home():
    return FileResponse(WEB / "index.html")


@app.get("/chat", include_in_schema=False)
def chat_page():
    return FileResponse(WEB / "chat.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return FileResponse(WEB / "dashboard.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/public/info")
def public_info():
    return {"agency": config.AGENCY_NAME, "city": config.AGENCY_CITY,
            "featured": [l for l in listings.all_listings() if l["purpose"] == "sale"][:6]}


# ---------------------------------------------------------------- customer chat

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


_rate: dict[str, list[float]] = {}


def _rate_limit(key: str, per_minute: int = 12) -> None:
    now = time.time()
    hits = [t for t in _rate.get(key, []) if now - t < 60]
    if len(hits) >= per_minute:
        raise HTTPException(429, "Too many messages, please wait a moment.")
    hits.append(now)
    _rate[key] = hits


@app.post("/api/chat/start")
def chat_start(request: Request):
    _rate_limit("start:" + (request.client.host if request.client else "?"), 10)
    conv_id = agent.new_conversation("web")
    msg = agent.add_message(conv_id, "ai", GREETING)
    return {"conversation_id": conv_id, "messages": [msg]}


@app.post("/api/chat/{conv_id}")
def chat_send(conv_id: str, body: ChatIn):
    if not db.one("SELECT id FROM conversations WHERE id=?", (conv_id,)):
        raise HTTPException(404, "Conversation not found")
    _rate_limit("chat:" + conv_id)
    result = agent.handle_customer_message(conv_id, body.message)
    return result


@app.get("/api/chat/{conv_id}/messages")
def chat_messages(conv_id: str, after: int = 0):
    conv = db.one("SELECT status FROM conversations WHERE id=?", (conv_id,))
    if not conv:
        raise HTTPException(404, "Conversation not found")
    rows = db.query("SELECT id, role, text, created_at FROM messages WHERE conversation_id=? AND id>? ORDER BY id",
                    (conv_id, after))
    return {"status": conv["status"], "messages": rows}


# ---------------------------------------------------------------- staff auth

def _sign(payload: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def staff(request: Request) -> str:
    token = request.cookies.get("hk_session", "")
    try:
        expires, sig = token.split(".", 1)
        if int(expires) > time.time() and hmac.compare_digest(sig, _sign(expires)):
            return "admin"
    except ValueError:
        pass
    raise HTTPException(401, "Please log in")


class LoginIn(BaseModel):
    password: str


@app.post("/api/login")
def login(body: LoginIn, response: Response, request: Request):
    _rate_limit("login:" + (request.client.host if request.client else "?"), 8)
    if not hmac.compare_digest(body.password, config.ADMIN_PASSWORD):
        raise HTTPException(401, "Wrong password")
    expires = str(int(time.time()) + 7 * 24 * 3600)
    response.set_cookie("hk_session", f"{expires}.{_sign(expires)}", httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("hk_session")
    return {"ok": True}


@app.get("/api/me")
def me(user: str = Depends(staff)):
    return {"user": user, "agency": config.AGENCY_NAME, "default_password": config.ADMIN_PASSWORD == "demo1234"}


# ---------------------------------------------------------------- dashboard data

def _lead_row(l: dict) -> dict:
    out = leads.public(l)
    conv = db.one("SELECT status, channel, updated_at FROM conversations WHERE id=?", (l["conversation_id"],))
    out["conversation_status"] = conv["status"] if conv else None
    out["channel"] = conv["channel"] if conv else None
    out["messages"] = db.one("SELECT COUNT(*) AS n FROM messages WHERE conversation_id=? AND role='customer'",
                             (l["conversation_id"],))["n"]
    return out


def _real_leads_sql() -> str:
    # a lead only counts once the customer has said something
    return "EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=leads.conversation_id AND m.role='customer')"


@app.get("/api/stats")
def stats(user: str = Depends(staff)):
    real = _real_leads_sql()
    by_temp = {r["temperature"]: r["n"] for r in db.query(
        f"SELECT temperature, COUNT(*) AS n FROM leads WHERE {real} GROUP BY temperature")}
    by_stage = {r["stage"]: r["n"] for r in db.query(f"SELECT stage, COUNT(*) AS n FROM leads WHERE {real} GROUP BY stage")}
    by_lang = {r["language"] or "Unknown": r["n"] for r in db.query(
        f"SELECT language, COUNT(*) AS n FROM leads WHERE {real} GROUP BY language")}
    conv = db.one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o, "
        "COALESCE(SUM(cache_read_tokens),0) AS c FROM conversations WHERE EXISTS "
        "(SELECT 1 FROM messages m WHERE m.conversation_id=conversations.id AND m.role='customer')")
    upcoming = db.query(
        "SELECT v.*, l.name, l.temperature FROM viewings v JOIN leads l ON l.id=v.lead_id "
        "WHERE v.status='booked' AND v.start_at>=? ORDER BY v.start_at LIMIT 5",
        (calendar.local_now().strftime("%Y-%m-%dT%H:%M"),))
    for v in upcoming:
        v["label"] = calendar.pretty(v["start_at"])
        v["listing"] = (listings.get(v["listing_id"]) or {}).get("title")
    hot = [_lead_row(l) for l in db.query(
        f"SELECT * FROM leads WHERE {real} ORDER BY score DESC, updated_at DESC LIMIT 5")]
    waiting = db.one("SELECT COUNT(*) AS n FROM conversations WHERE status='human'")["n"]
    ai_msgs = db.one("SELECT COUNT(*) AS n FROM messages WHERE role='ai'")["n"]
    return {
        "conversations": conv["n"], "leads": sum(by_temp.values()), "by_temperature": by_temp,
        "by_stage": by_stage, "by_language": by_lang, "waiting_for_human": waiting,
        "viewings_booked": db.one("SELECT COUNT(*) AS n FROM viewings WHERE status!='cancelled'")["n"],
        "ai_replies": ai_msgs, "upcoming_viewings": upcoming, "top_leads": hot,
        "tokens": {"input": conv["i"], "output": conv["o"], "cache_read": conv["c"]},
        "model": config.CLAUDE_MODEL,
    }


@app.get("/api/leads")
def list_leads(temperature: str = "", stage: str = "", q: str = "", user: str = Depends(staff)):
    sql, args = f"SELECT * FROM leads WHERE {_real_leads_sql()}", []
    if temperature:
        sql += " AND temperature=?"; args.append(temperature)
    if stage:
        sql += " AND stage=?"; args.append(stage)
    if q:
        sql += " AND (name LIKE ? OR phone LIKE ? OR email LIKE ? OR areas LIKE ? OR summary LIKE ?)"
        args += [f"%{q}%"] * 5
    sql += " ORDER BY updated_at DESC"
    return [_lead_row(l) for l in db.query(sql, tuple(args))]


@app.get("/api/leads.csv")
def export_leads(user: str = Depends(staff)):
    rows = db.query(f"SELECT * FROM leads WHERE {_real_leads_sql()} ORDER BY score DESC")
    buf = io.StringIO()
    cols = ["id", "name", "phone", "email", "language", "intent", "areas", "property_type", "bedrooms", "budget_min",
            "budget_max", "timeline", "financing", "score", "temperature", "stage", "summary", "next_step", "updated_at"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return Response("﻿" + buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=leads.csv"})


@app.get("/api/leads/{lead_id}")
def lead_detail(lead_id: int, user: str = Depends(staff)):
    l = leads.get(lead_id)
    if not l:
        raise HTTPException(404, "Lead not found")
    conv = db.one("SELECT id, channel, language, status, handoff_reason, input_tokens, output_tokens, "
                  "cache_read_tokens, created_at FROM conversations WHERE id=?", (l["conversation_id"],))
    msgs = db.query("SELECT id, role, text, created_at FROM messages WHERE conversation_id=? ORDER BY id",
                    (l["conversation_id"],))
    views = db.query("SELECT * FROM viewings WHERE lead_id=? ORDER BY start_at", (lead_id,))
    for v in views:
        v["label"] = calendar.pretty(v["start_at"])
        v["listing"] = (listings.get(v["listing_id"]) or {}).get("title")
    interested = [listings.get(x.strip()) for x in (l.get("interested_listings") or "").split(",") if x.strip()]
    return {"lead": _lead_row(l), "conversation": conv, "messages": msgs, "viewings": views,
            "listings": [x for x in interested if x]}


class LeadPatch(BaseModel):
    stage: str | None = None
    notes: str | None = None


@app.patch("/api/leads/{lead_id}")
def lead_patch(lead_id: int, body: LeadPatch, user: str = Depends(staff)):
    l = leads.get(lead_id)
    if not l:
        raise HTTPException(404, "Lead not found")
    if body.stage and body.stage not in ("new", "qualified", "viewing booked", "handed off", "won", "lost"):
        raise HTTPException(400, "Unknown stage")
    changes = {"notes": body.notes} if body.notes is not None else {}
    fresh = leads.update(l["conversation_id"], changes, stage=body.stage)
    db.log_event("lead.edited", l["conversation_id"], body.model_dump(exclude_none=True))
    return _lead_row(fresh)


@app.get("/api/conversations")
def list_conversations(status: str = "", user: str = Depends(staff)):
    sql = ("SELECT c.id, c.channel, c.language, c.status, c.handoff_reason, c.updated_at, l.id AS lead_id, l.name, "
           "l.temperature, l.score, (SELECT text FROM messages m WHERE m.conversation_id=c.id ORDER BY id DESC LIMIT 1) "
           "AS last_text, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id AND m.role='customer') AS n "
           "FROM conversations c LEFT JOIN leads l ON l.conversation_id=c.id WHERE n>0")
    args = ()
    if status:
        sql += " AND c.status=?"
        args = (status,)
    return db.query(sql + " ORDER BY c.updated_at DESC", args)


class StaffReply(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@app.post("/api/conversations/{conv_id}/reply")
def staff_reply(conv_id: str, body: StaffReply, user: str = Depends(staff)):
    conv = db.one("SELECT * FROM conversations WHERE id=?", (conv_id,))
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if conv["status"] == "ai":
        # a consultant replying takes the chat over from the AI
        with db.tx() as c:
            c.execute("UPDATE conversations SET status='human', handoff_reason='Taken over by staff', handoff_at=? "
                      "WHERE id=?", (db.now(), conv_id))
    msg = agent.add_message(conv_id, "staff", body.text.strip())
    db.log_event("staff.reply", conv_id)
    return msg


class StatusIn(BaseModel):
    status: str


@app.post("/api/conversations/{conv_id}/status")
def set_status(conv_id: str, body: StatusIn, user: str = Depends(staff)):
    if body.status not in ("ai", "human", "closed"):
        raise HTTPException(400, "Unknown status")
    if not db.one("SELECT id FROM conversations WHERE id=?", (conv_id,)):
        raise HTTPException(404, "Conversation not found")
    if body.status == "ai":
        agent.return_to_ai(conv_id)
    else:
        with db.tx() as c:
            c.execute("UPDATE conversations SET status=?, handoff_at=COALESCE(handoff_at, ?), updated_at=? WHERE id=?",
                      (body.status, db.now(), db.now(), conv_id))
        db.log_event(f"status.{body.status}", conv_id)
    return {"ok": True}


@app.get("/api/viewings")
def list_viewings(user: str = Depends(staff)):
    rows = db.query("SELECT v.*, l.name, l.phone, l.temperature, l.score FROM viewings v JOIN leads l ON l.id=v.lead_id "
                    "ORDER BY v.start_at")
    for v in rows:
        v["label"] = calendar.pretty(v["start_at"])
        lst = listings.get(v["listing_id"]) or {}
        v["listing"] = lst.get("title")
        v["area"] = lst.get("area")
        v["google_calendar"] = calendar.google_calendar_link(
            f"Viewing: {lst.get('title')} ({v['name'] or 'client'})", v["start_at"],
            f"Client: {v['name']} {v['phone']}. Booked by the AI sales agent.", lst.get("area", ""))
    return rows


class ViewingPatch(BaseModel):
    status: str


@app.patch("/api/viewings/{vid}")
def viewing_patch(vid: int, body: ViewingPatch, user: str = Depends(staff)):
    if body.status not in ("booked", "cancelled", "done"):
        raise HTTPException(400, "Unknown status")
    v = db.one("SELECT * FROM viewings WHERE id=?", (vid,))
    if not v:
        raise HTTPException(404, "Viewing not found")
    with db.tx() as c:
        c.execute("UPDATE viewings SET status=? WHERE id=?", (body.status, vid))
    leads.rescore(v["lead_id"])
    db.log_event(f"viewing.{body.status}", v["conversation_id"], {"viewing": vid})
    return {"ok": True}


@app.get("/api/viewings/{vid}.ics")
def viewing_ics(vid: int, user: str = Depends(staff)):
    v = db.one("SELECT v.*, l.name, l.phone FROM viewings v JOIN leads l ON l.id=v.lead_id WHERE v.id=?", (vid,))
    if not v:
        raise HTTPException(404, "Viewing not found")
    lst = listings.get(v["listing_id"]) or {}
    body = calendar.ics(f"Viewing: {lst.get('title')}", v["start_at"], f"Client: {v['name']} {v['phone']}",
                        lst.get("area", ""), f"viewing-{vid}@real-estate-ai-sales-agent")
    return Response(body, media_type="text/calendar", headers={"Content-Disposition": f"attachment; filename=viewing-{vid}.ics"})


@app.get("/api/listings")
def all_listings(user: str = Depends(staff)):
    return listings.all_listings()


@app.get("/api/knowledge")
def knowledge_list(user: str = Depends(staff)):
    return knowledge.sources()


@app.get("/api/knowledge/search")
def knowledge_search(q: str, user: str = Depends(staff)):
    return knowledge.search(q, top_k=4)


@app.post("/api/knowledge/upload")
async def knowledge_upload(file: UploadFile = File(...), user: str = Depends(staff)):
    name = re.sub(r"[^A-Za-z0-9._ -]", "", Path(file.filename or "").name).strip() or "document.txt"
    if Path(name).suffix.lower() not in knowledge.SUPPORTED:
        raise HTTPException(400, "Upload a PDF, Markdown or text file")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "File is larger than 10 MB")
    path = config.KNOWLEDGE_DIR / name
    path.write_bytes(data)
    try:
        n = knowledge.ingest_file(path)
    except Exception as exc:  # noqa: BLE001
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not read this file: {exc}")
    db.log_event("knowledge.uploaded", None, {"file": name, "chunks": n})
    return {"source": name, "chunks": n}


@app.delete("/api/knowledge/{source}")
def knowledge_delete(source: str, user: str = Depends(staff)):
    knowledge.delete_source(source)
    path = config.KNOWLEDGE_DIR / Path(source).name
    if path.exists():
        path.unlink()
    db.log_event("knowledge.deleted", None, {"file": source})
    return {"ok": True}


@app.get("/api/settings")
def settings(user: str = Depends(staff)):
    return {
        "agency": config.AGENCY_NAME, "model": config.CLAUDE_MODEL, "effort": config.CLAUDE_EFFORT,
        "webhook": bool(config.LEAD_WEBHOOK_URL), "timezone": config.TIMEZONE,
        "scoring": {"timeline": scoring.TIMELINE_POINTS, "financing": scoring.FINANCING_POINTS,
                    "other": {"budget shared": 15, "knows area or property type": 10, "gave contact details": 15,
                              "viewing booked": 15},
                    "hot_from": scoring.HOT, "warm_from": scoring.WARM},
        "api_key_set": bool(__import__("os").getenv("ANTHROPIC_API_KEY")),
    }


@app.get("/api/events")
def events(user: str = Depends(staff)):
    rows = db.query("SELECT * FROM events ORDER BY id DESC LIMIT 100")
    for r in rows:
        try:
            r["detail"] = json.loads(r["detail"]) if r["detail"] else None
        except ValueError:
            pass
    return rows
