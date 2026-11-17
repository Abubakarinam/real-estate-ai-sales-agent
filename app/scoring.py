"""Lead scoring. Plain rules (not the AI's opinion), so the sales team can see
exactly why a lead is hot, warm or cold, and change the weights in one place."""

TIMELINE_POINTS = {"asap": 25, "1-3 months": 18, "3-6 months": 10, "6+ months": 4, "just browsing": 0}
FINANCING_POINTS = {
    "cash": 20,
    "mortgage pre-approved": 18,
    "not needed (renting)": 15,
    "mortgage not yet": 8,
}
HOT, WARM = 70, 40


def score_lead(lead: dict, has_viewing: bool = False) -> tuple[int, str, list[str]]:
    points, reasons = 0, []

    def add(n: int, why: str):
        nonlocal points
        if n:
            points += n
            reasons.append(f"+{n} {why}")

    if lead.get("budget_max") or lead.get("budget_min"):
        add(15, "budget shared")
    t = (lead.get("timeline") or "").lower()
    if t in TIMELINE_POINTS:
        add(TIMELINE_POINTS[t], f"timeline: {t}")
    f = (lead.get("financing") or "").lower()
    if f in FINANCING_POINTS:
        add(FINANCING_POINTS[f], f"financing: {f}")
    if lead.get("areas") or lead.get("property_type") or lead.get("interested_listings"):
        add(10, "knows the area or property type")
    if lead.get("phone") or lead.get("email"):
        add(15, "gave contact details")
    if has_viewing:
        add(15, "viewing booked")

    points = min(points, 100)
    if t == "just browsing":
        # browsing buyers stay cold however much else they share
        points = min(points, WARM - 1)
        reasons.append("capped: just browsing")
    temperature = "hot" if points >= HOT else "warm" if points >= WARM else "cold"
    return points, temperature, reasons
