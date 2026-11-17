"""The tools Claude can call, and the Python code that runs each one."""
import json

from . import scheduling as calendar, config, db, knowledge, leads, listings

TOOLS = [
    {
        "name": "search_knowledge",
        "description": (
            "Search the agency's own documents (buying steps and fees, mortgages, payment plans, "
            "renting rules, Golden Visa, FAQs, project brochures, office hours, team). Use it before "
            "answering any factual question. Always write the query in English, even when the "
            "customer writes in Arabic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Short English search query"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_listings",
        "description": (
            "Find available properties from the agency's live listings. Use it whenever the customer "
            "asks what is available, or once you know their budget or area. Never invent a listing "
            "or a price: only offer what this tool returns."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "purpose": {"type": "string", "enum": ["sale", "rent"]},
                "area": {"type": "string", "description": "Area name, e.g. 'Dubai Marina', 'JVC', 'Dubai Hills'"},
                "property_type": {"type": "string", "enum": ["apartment", "villa", "townhouse"]},
                "bedrooms_min": {"type": "integer", "description": "0 for studio"},
                "budget_max": {"type": "integer", "description": "AED. For rentals, the yearly rent"},
                "budget_min": {"type": "integer", "description": "AED"},
            },
            "required": [],
        },
    },
    {
        "name": "update_lead",
        "description": (
            "Save what you have learned about the customer to the CRM. Call it every time the "
            "customer shares something new (name, phone, budget, area, timeline, financing, etc.), "
            "and always refresh `summary`. Returns the lead score and the details still missing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "email": {"type": "string"},
                "language": {"type": "string", "enum": ["English", "Arabic", "Other"]},
                "intent": {"type": "string", "enum": ["buy", "rent", "invest"]},
                "property_type": {"type": "string"},
                "areas": {"type": "string", "description": "Comma-separated areas the customer likes"},
                "bedrooms": {"type": "string"},
                "budget_min": {"type": "integer", "description": "AED"},
                "budget_max": {"type": "integer", "description": "AED (yearly rent for tenants)"},
                "timeline": {"type": "string", "enum": ["asap", "1-3 months", "3-6 months", "6+ months", "just browsing"]},
                "financing": {
                    "type": "string",
                    "enum": ["cash", "mortgage pre-approved", "mortgage not yet", "not needed (renting)"],
                },
                "interested_listings": {"type": "string", "description": "Listing IDs, comma-separated"},
                "notes": {"type": "string", "description": "Anything else useful for the sales team"},
                "summary": {"type": "string", "description": "1-2 sentence summary in English for the sales team"},
                "next_step": {"type": "string", "description": "What should happen next, in English"},
            },
            "required": ["summary"],
        },
    },
    {
        "name": "get_viewing_slots",
        "description": "Get the next free viewing times for a listing (with that listing's consultant).",
        "input_schema": {
            "type": "object",
            "properties": {
                "listing_id": {"type": "string"},
                "from_date": {"type": "string", "description": "YYYY-MM-DD, optional"},
            },
            "required": ["listing_id"],
        },
    },
    {
        "name": "book_viewing",
        "description": (
            "Book a property viewing. Only call it after the customer has chosen one of the free "
            "slots from get_viewing_slots and has given their name and phone number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "listing_id": {"type": "string"},
                "slot": {"type": "string", "description": "Exactly as returned by get_viewing_slots, e.g. 2026-11-18T11:00"},
                "name": {"type": "string"},
                "phone": {"type": "string"},
            },
            "required": ["listing_id", "slot", "name", "phone"],
        },
    },
    {
        "name": "handoff_to_human",
        "description": (
            "Pass the chat to a human consultant. Use it when the customer asks for a person, wants "
            "to negotiate the price or make an offer, has a complaint, asks a legal or visa question "
            "you can't answer from the documents, or when you are unsure. After calling it, tell the "
            "customer a consultant will reply here soon."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Short reason in English"},
                "urgency": {"type": "string", "enum": ["normal", "high"]},
            },
            "required": ["reason"],
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def run_tool(name: str, args: dict, conv_id: str) -> tuple[str, bool]:
    """Returns (result text, is_error)."""
    if not isinstance(args, dict):
        return "Tool input must be a JSON object.", True
    try:
        return HANDLERS[name](args, conv_id), False
    except KeyError as exc:
        return f"Unknown tool or missing field: {exc}", True
    except Exception as exc:  # noqa: BLE001 - errors go back to Claude, not to the customer
        return f"Tool error: {exc}", True


def _search_knowledge(args, conv_id):
    hits = knowledge.search(str(args["query"]), top_k=4)
    db.log_event("tool.search_knowledge", conv_id, {"query": args["query"], "hits": [h["source"] for h in hits]})
    if not hits:
        return "No matching passage in the agency documents. Do not guess: offer a consultant instead."
    return "\n\n---\n\n".join(f"[{h['source']} > {h['heading']}]\n{h['text']}" for h in hits)


def _search_listings(args, conv_id):
    found = listings.search(
        purpose=args.get("purpose"), area=args.get("area"), property_type=args.get("property_type"),
        bedrooms_min=args.get("bedrooms_min"), budget_max=args.get("budget_max"), budget_min=args.get("budget_min"),
    )
    db.log_event("tool.search_listings", conv_id, {"filters": args, "found": [l["id"] for l in found]})
    if not found:
        return "No listing matches these filters. Suggest widening the area or budget, or offer to alert a consultant."
    return "\n".join(
        listings.short(l) + f" | highlights: {'; '.join(l['highlights'])} | consultant: {l['agent']}" for l in found
    )


def _update_lead(args, conv_id):
    lead = leads.update(conv_id, args)
    missing = leads.missing_fields(lead)
    return _json({
        "saved": True,
        "score": lead["score"],
        "temperature": lead["temperature"],
        "still_missing": missing,
        "tip": "Ask for at most one or two missing details at a time, naturally." if missing else "Lead is fully qualified.",
    })


def _get_viewing_slots(args, conv_id):
    listing = listings.get(args["listing_id"])
    if not listing:
        return f"Listing {args['listing_id']} does not exist. Use search_listings first."
    slots = calendar.free_slots(listing["agent"], args.get("from_date"))
    if not slots:
        return "No free slots in the next days. Offer a handoff to the consultant."
    return _json({
        "listing": listings.short(listing),
        "consultant": listing["agent"],
        "today": calendar.local_now().strftime("%A %Y-%m-%d"),
        "free_slots": [{"slot": s, "label": calendar.pretty(s)} for s in slots],
    })


def _book_viewing(args, conv_id):
    listing = listings.get(args["listing_id"])
    if not listing:
        return f"Listing {args['listing_id']} does not exist."
    slot = str(args["slot"]).strip()[:16]
    if not calendar.is_free(listing["agent"], slot):
        return "That slot is not free (or not a valid viewing time). Call get_viewing_slots again and offer new times."
    lead = leads.update(conv_id, {"name": args["name"], "phone": args["phone"], "interested_listings": listing["id"]})
    with db.tx() as c:
        c.execute(
            "INSERT INTO viewings(lead_id, conversation_id, listing_id, start_at, agent, created_at) VALUES (?,?,?,?,?,?)",
            (lead["id"], conv_id, listing["id"], slot, listing["agent"], db.now()),
        )
    lead = leads.update(conv_id, {"next_step": f"Viewing of {listing['id']} on {calendar.pretty(slot)} with {listing['agent']}"},
                        stage="viewing booked")
    db.log_event("viewing.booked", conv_id, {"listing": listing["id"], "slot": slot, "agent": listing["agent"]})
    leads.send_webhook("viewing.booked", {"conversation_id": conv_id, "lead": leads.public(lead),
                                          "listing_id": listing["id"], "slot": slot, "agent": listing["agent"]})
    return _json({
        "booked": True,
        "when": calendar.pretty(slot),
        "listing": listing["title"],
        "consultant": listing["agent"],
        "meeting_point": f"{listing['area']}, the consultant will share the exact location by phone",
        "lead_score": lead["score"],
    })


def _handoff(args, conv_id):
    with db.tx() as c:
        c.execute(
            "UPDATE conversations SET status='human', handoff_reason=?, handoff_at=?, updated_at=? WHERE id=?",
            (args["reason"], db.now(), db.now(), conv_id),
        )
    lead = leads.update(conv_id, {"next_step": f"Consultant to reply: {args['reason']}"}, stage="handed off")
    db.log_event("handoff", conv_id, {"reason": args["reason"], "urgency": args.get("urgency", "normal")})
    leads.send_webhook("handoff", {"conversation_id": conv_id, "reason": args["reason"],
                                   "urgency": args.get("urgency", "normal"), "lead": leads.public(lead)})
    return "Handed off. A consultant will see this chat now. Tell the customer a person will reply here shortly."


HANDLERS = {
    "search_knowledge": _search_knowledge,
    "search_listings": _search_listings,
    "update_lead": _update_lead,
    "get_viewing_slots": _get_viewing_slots,
    "book_viewing": _book_viewing,
    "handoff_to_human": _handoff,
}
