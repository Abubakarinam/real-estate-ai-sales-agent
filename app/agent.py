"""The sales agent: a small LangGraph loop around Claude.

    customer message -> [call_claude] --tool_use--> [run_tools] --> back to [call_claude]
                                   \\--done--> reply to customer

The whole Claude message history (including thinking and tool blocks) is stored per
conversation and only ever appended to, so prompt caching keeps working and each
turn costs little.
"""
import json
import re
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from . import scheduling as calendar, config, db, leads, prompts
from .tools import TOOLS, run_tool

MAX_TOOL_ROUNDS = 8
ARABIC = re.compile(r"[؀-ۿ]")
FALLBACK_TEXT = {
    "English": "Sorry, I couldn't answer that one. I've passed your message to one of our consultants, who will reply here shortly.",
    "Arabic": "عذراً، لم أتمكن من الإجابة على ذلك. لقد حوّلت رسالتك إلى أحد مستشارينا وسيرد عليك هنا قريباً.",
}


class AgentState(TypedDict):
    conv_id: str
    messages: list[dict]
    rounds: int
    stop_reason: str
    reply: str
    usage: dict


_client = None


def get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


def set_client(client) -> None:
    """Tests inject a fake client here."""
    global _client
    _client = client


def _block_to_param(block: Any) -> dict:
    if isinstance(block, dict):
        return block
    return block.model_dump(mode="json", exclude_none=True, by_alias=True)


def call_claude(state: AgentState) -> dict:
    today = calendar.local_now().strftime("%A %d %B %Y")
    request = dict(
        model=config.CLAUDE_MODEL,
        max_tokens=8000,
        # Stable prompt first (cached); the date line changes once a day.
        system=[
            {"type": "text", "text": prompts.SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": prompts.date_line(today)},
        ],
        tools=TOOLS,
        messages=state["messages"],
        output_config={"effort": config.CLAUDE_EFFORT},
        cache_control={"type": "ephemeral"},
    )
    client = get_client()
    if config.CLAUDE_FALLBACKS:
        response = client.beta.messages.create(
            **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
        )
    else:
        response = client.messages.create(**request)

    content = [_block_to_param(b) for b in response.content]
    messages = state["messages"] + [{"role": "assistant", "content": content}]
    text = "\n\n".join(b["text"].strip() for b in content if b.get("type") == "text" and b.get("text", "").strip())

    u = state["usage"]
    usage = getattr(response, "usage", None)
    if usage is not None:
        u = {
            "input": u["input"] + (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0),
            "output": u["output"] + (getattr(usage, "output_tokens", 0) or 0),
            "cache_read": u["cache_read"] + (getattr(usage, "cache_read_input_tokens", 0) or 0),
        }
    return {
        "messages": messages,
        "stop_reason": response.stop_reason,
        "reply": text or state["reply"],
        "rounds": state["rounds"] + 1,
        "usage": u,
    }


def run_tools(state: AgentState) -> dict:
    last = state["messages"][-1]["content"]
    results = []
    for block in last:
        if block.get("type") != "tool_use":
            continue
        text, is_error = run_tool(block["name"], block.get("input") or {}, state["conv_id"])
        results.append({"type": "tool_result", "tool_use_id": block["id"], "content": text, "is_error": is_error})
    # all results go back in ONE user message
    return {"messages": state["messages"] + [{"role": "user", "content": results}]}


def route(state: AgentState) -> str:
    if state["rounds"] >= MAX_TOOL_ROUNDS:
        return END
    if state["stop_reason"] == "tool_use":
        return "run_tools"
    if state["stop_reason"] == "pause_turn":
        return "call_claude"
    return END


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("call_claude", call_claude)
    g.add_node("run_tools", run_tools)
    g.set_entry_point("call_claude")
    g.add_conditional_edges("call_claude", route, {"run_tools": "run_tools", "call_claude": "call_claude", END: END})
    g.add_edge("run_tools", "call_claude")
    return g.compile()


GRAPH = build_graph()


# ---------------------------------------------------------------- conversations

def new_conversation(channel: str = "web") -> str:
    conv_id = uuid.uuid4().hex[:12]
    with db.tx() as c:
        c.execute(
            "INSERT INTO conversations(id, channel, created_at, updated_at) VALUES (?,?,?,?)",
            (conv_id, channel, db.now(), db.now()),
        )
    leads.ensure(conv_id)
    return conv_id


def add_message(conv_id: str, role: str, text: str) -> dict:
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO messages(conversation_id, role, text, created_at) VALUES (?,?,?,?)",
            (conv_id, role, text, db.now()),
        )
        c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (db.now(), conv_id))
    return db.one("SELECT * FROM messages WHERE id=?", (cur.lastrowid,))


def handle_customer_message(conv_id: str, text: str) -> dict:
    """Store the customer's message and, if the AI is in charge, produce a reply."""
    conv = db.one("SELECT * FROM conversations WHERE id=?", (conv_id,))
    if not conv:
        raise KeyError(conv_id)
    text = text.strip()[:2000]
    add_message(conv_id, "customer", text)

    language = conv["language"] or ("Arabic" if ARABIC.search(text) else "English")
    if not conv["language"]:
        with db.tx() as c:
            c.execute("UPDATE conversations SET language=? WHERE id=?", (language, conv_id))
        leads.update(conv_id, {"language": language})

    if conv["status"] != "ai":
        db.log_event("customer.waiting_for_human", conv_id)
        return {"reply": None, "status": conv["status"]}

    turns = db.one("SELECT COUNT(*) AS n FROM messages WHERE conversation_id=? AND role='customer'", (conv_id,))["n"]
    if turns > config.MAX_TURNS_PER_CONVERSATION:
        return {"reply": None, "status": conv["status"]}

    history = json.loads(conv["api_messages"])
    content = [{"type": "text", "text": text}]
    if conv["pending_note"]:
        content.insert(0, {"type": "text", "text": conv["pending_note"]})
        with db.tx() as c:
            c.execute("UPDATE conversations SET pending_note=NULL WHERE id=?", (conv_id,))
    history.append({"role": "user", "content": content})
    state: AgentState = {
        "conv_id": conv_id, "messages": history, "rounds": 0, "stop_reason": "", "reply": "",
        "usage": {"input": 0, "output": 0, "cache_read": 0},
    }
    try:
        final = GRAPH.invoke(state)
    except Exception as exc:  # noqa: BLE001
        db.log_event("agent.error", conv_id, {"error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        reply = FALLBACK_TEXT.get(language, FALLBACK_TEXT["English"])
        add_message(conv_id, "ai", reply)
        run_tool("handoff_to_human", {"reason": "AI error, please reply manually", "urgency": "high"}, conv_id)
        return {"reply": reply, "status": "human", "error": True}

    messages = final["messages"]
    reply = final["reply"]
    if final["stop_reason"] == "refusal" or not reply:
        reply = FALLBACK_TEXT.get(language, FALLBACK_TEXT["English"])
        if final["stop_reason"] == "refusal":
            # drop the refused turn so the history stays valid for later turns
            messages = history[:-1]
        run_tool("handoff_to_human", {"reason": "AI could not answer this message"}, conv_id)
    if messages[-1]["role"] == "user":
        # the loop stopped after tools (round limit) - close the turn so history stays valid
        messages = messages + [{"role": "assistant", "content": [{"type": "text", "text": reply}]}]

    u = final["usage"]
    with db.tx() as c:
        c.execute(
            "UPDATE conversations SET api_messages=?, input_tokens=input_tokens+?, output_tokens=output_tokens+?, "
            "cache_read_tokens=cache_read_tokens+?, updated_at=? WHERE id=?",
            (json.dumps(messages, ensure_ascii=False), u["input"], u["output"], u["cache_read"], db.now(), conv_id),
        )
    add_message(conv_id, "ai", reply)
    status = db.one("SELECT status FROM conversations WHERE id=?", (conv_id,))["status"]
    return {"reply": reply, "status": status}


def return_to_ai(conv_id: str) -> None:
    """Give the chat back to the AI, with a note of what the consultant and customer said meanwhile."""
    conv = db.one("SELECT * FROM conversations WHERE id=?", (conv_id,))
    since = conv["handoff_at"] or conv["created_at"]
    rows = db.query(
        "SELECT role, text FROM messages WHERE conversation_id=? AND created_at>=? AND role IN ('staff','customer') ORDER BY id",
        (conv_id, since),
    )
    note = None
    if rows:
        lines = "\n".join(f"{'Consultant' if r['role'] == 'staff' else 'Customer'}: {r['text']}" for r in rows)
        note = ("[Note for the assistant, not written by the customer: a human consultant handled this chat for a while. "
                f"Here is what was said, so you can continue from it.]\n{lines}\n[End of note. The customer's new message follows.]")
    with db.tx() as c:
        c.execute("UPDATE conversations SET status='ai', pending_note=?, updated_at=? WHERE id=?", (note, db.now(), conv_id))
    db.log_event("returned_to_ai", conv_id)
