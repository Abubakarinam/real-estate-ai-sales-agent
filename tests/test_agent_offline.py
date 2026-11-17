"""Offline tests: a fake Claude client plays a scripted conversation, so the whole
loop (tools, scoring, booking, handoff, API) is tested without an API key.

Run: python -m pytest -q
"""
import os
import tempfile
from types import SimpleNamespace as NS

import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["ADMIN_PASSWORD"] = "test-pass"

from fastapi.testclient import TestClient  # noqa: E402

from app import agent, db, knowledge, scheduling, server  # noqa: E402


def text(t):
    return NS(type="text", text=t, model_dump=lambda **_: {"type": "text", "text": t})


def tool(i, name, inp):
    return NS(type="tool_use", id=i, name=name, input=inp,
              model_dump=lambda **_: {"type": "tool_use", "id": i, "name": name, "input": inp})


class FakeClient:
    """Returns scripted responses; records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))
        self.messages = NS(create=self._create)

    def _create(self, **req):
        self.requests.append(req)
        stop, blocks = self.script.pop(0)
        return NS(content=blocks, stop_reason=stop,
                  usage=NS(input_tokens=100, output_tokens=20, cache_read_input_tokens=50, cache_creation_input_tokens=0))


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:
        yield c


def test_knowledge_search_finds_fees_and_arabic():
    knowledge.ensure_loaded()
    assert knowledge.search("DLD transfer fee")[0]["source"] == "02-buying-process-and-fees.md"
    assert knowledge.search("Creek Vista payment plan")
    assert knowledge.search("الإقامة الذهبية")[0]["source"] == "06-faq-arabic.md"


def test_full_sales_conversation(client):
    slot = scheduling.free_slots("Omar Haddad")[0]
    fake = FakeClient([
        ("tool_use", [tool("t1", "search_listings", {"purpose": "sale", "area": "JVC", "budget_max": 1300000}),
                      tool("t2", "update_lead", {"intent": "buy", "areas": "JVC", "budget_max": 1300000,
                                                  "summary": "Wants to buy in JVC up to 1.3M"})]),
        ("end_turn", [text("I have HK-102, a 2-bed in JVC for AED 1,250,000. When are you planning to buy?")]),
        ("tool_use", [tool("t3", "update_lead", {"timeline": "asap", "financing": "cash",
                                                  "summary": "Cash buyer, JVC, ready now"}),
                      tool("t4", "get_viewing_slots", {"listing_id": "HK-102"})]),
        ("end_turn", [text(f"Great. I can show it at {slot}. Your name and phone?")]),
        ("tool_use", [tool("t5", "book_viewing", {"listing_id": "HK-102", "slot": slot, "name": "Ali Khan",
                                                   "phone": "+971500000000"})]),
        ("end_turn", [text("Booked! Omar will meet you there.")]),
    ])
    agent.set_client(fake)

    conv = client.post("/api/chat/start").json()["conversation_id"]
    r = client.post(f"/api/chat/{conv}", json={"message": "Hi, I want a 2-bed in JVC, budget 1.3M"}).json()
    assert "HK-102" in r["reply"]
    client.post(f"/api/chat/{conv}", json={"message": "Cash, I can buy now"})
    r = client.post(f"/api/chat/{conv}", json={"message": "Ali Khan, +971500000000, first slot please"}).json()
    assert r["reply"].startswith("Booked")

    # every tool_result went back in one user message right after its tool_use
    second = fake.requests[1]["messages"]
    assert second[-1]["role"] == "user" and len(second[-1]["content"]) == 2
    assert all(b["type"] == "tool_result" for b in second[-1]["content"])
    # the system prompt is cached
    assert fake.requests[0]["system"][0]["cache_control"] == {"type": "ephemeral"}

    lead = db.one("SELECT * FROM leads WHERE conversation_id=?", (conv,))
    assert lead["temperature"] == "hot" and lead["score"] >= 90
    assert lead["stage"] == "viewing booked"
    assert db.one("SELECT * FROM viewings WHERE conversation_id=?", (conv,))["start_at"] == slot
    # the slot is now taken
    assert slot not in scheduling.free_slots("Omar Haddad")


def test_handoff_and_staff_reply(client):
    fake = FakeClient([
        ("tool_use", [tool("h1", "handoff_to_human", {"reason": "Wants to negotiate price", "urgency": "high"})]),
        ("end_turn", [text("A consultant will reply here shortly.")]),
        ("end_turn", [text("Welcome back! Anything else?")]),
    ])
    agent.set_client(fake)
    conv = client.post("/api/chat/start").json()["conversation_id"]
    r = client.post(f"/api/chat/{conv}", json={"message": "Can you do 1.1M for HK-102?"}).json()
    assert r["status"] == "human"
    # while a human has the chat, the AI stays quiet
    r = client.post(f"/api/chat/{conv}", json={"message": "hello?"}).json()
    assert r["reply"] is None

    assert client.get("/api/stats").status_code == 401
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "test-pass"}).status_code == 200
    client.post(f"/api/conversations/{conv}/reply", json={"text": "Hi, Sara here. The seller may accept 1.2M."})
    msgs = client.get(f"/api/chat/{conv}/messages").json()["messages"]
    assert msgs[-1]["role"] == "staff"

    client.post(f"/api/conversations/{conv}/status", json={"status": "ai"})
    client.post(f"/api/chat/{conv}", json={"message": "Thanks Sara"})
    last_user = fake.requests[-1]["messages"][-1]
    assert "Sara here" in last_user["content"][0]["text"]  # the AI is told what the consultant said

    stats = client.get("/api/stats").json()
    assert stats["leads"] >= 2 and stats["viewings_booked"] >= 1
    assert client.get("/api/leads.csv").status_code == 200


def test_ai_error_hands_off(client):
    class Broken:
        beta = NS(messages=NS(create=lambda **_: (_ for _ in ()).throw(RuntimeError("boom"))))
        messages = beta.messages

    agent.set_client(Broken())
    conv = client.post("/api/chat/start").json()["conversation_id"]
    r = client.post(f"/api/chat/{conv}", json={"message": "hello"}).json()
    assert r["status"] == "human" and r["reply"]


def test_scoring_rules():
    from app.scoring import score_lead

    assert score_lead({"timeline": "just browsing", "budget_max": 1, "phone": "1", "financing": "cash"})[1] == "cold"
    assert score_lead({"budget_max": 1, "timeline": "1-3 months", "financing": "mortgage pre-approved"})[1] == "warm"
