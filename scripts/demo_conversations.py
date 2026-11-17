"""Runs a few realistic buyer and tenant chats through the REAL agent (needs
ANTHROPIC_API_KEY), so the dashboard fills with real conversations, scores and
bookings. Then saves everything to demo/demo-data.json.

Run:  python scripts/demo_conversations.py          (about 7 chats, roughly USD 0.50-1.50)
      python scripts/demo_conversations.py --only 2 (just one persona)
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import agent, db, knowledge  # noqa: E402
from scripts.demo_data import export  # noqa: E402

PERSONAS = [
    ("Family villa buyer (English)", [
        "Hi, we're a family of five moving from London. We're looking for a 4-bedroom villa to buy, ideally near good schools.",
        "Budget is around 7 million dirhams. Dubai Hills sounds good.",
        "We'd pay cash, and we want to move within a month if we find the right place.",
        "HK-106 looks great. What would the total buying costs be on top of the price?",
        "Can we see it this week?",
        "The second time you suggested works. I'm Ahmed Siddiqui, +971 50 214 7788",
    ]),
    ("Investor (Arabic)", [
        "السلام عليكم، أبحث عن شقة استوديو للاستثمار في دبي",
        "ميزانيتي حوالي 700 ألف درهم، وأفضل منطقة قرية جميرا الدائرية",
        "ما هو العائد الإيجاري المتوقع؟ وهل يمكنني الحصول على الإقامة الذهبية بهذا المبلغ؟",
        "سأحتاج إلى تمويل عقاري، لم أتقدم بعد. أخطط للشراء خلال 3 إلى 6 أشهر",
        "اسمي فاطمة الزهراني، ورقمي 0551234567",
    ]),
    ("Tenant (English)", [
        "Hello, I need a 1-bed apartment to rent, close to the metro. Budget about 100k a year.",
        "JLT or Marina are both fine. I start a new job next month so I need to move in by early December.",
        "HK-201 sounds good. How many cheques can I pay in, and what other costs are there?",
        "Can I view it tomorrow or the day after?",
        "The first slot is perfect. James Carter, 058 330 1942",
    ]),
    ("Off-plan buyer (English)", [
        "Hi! Tell me about Creek Vista Residences. What is the payment plan?",
        "I'm interested in the 2-bed. Is there any agency commission on it?",
        "My mortgage is already pre-approved. I'd like to book within the next 2 months.",
        "Please send the floor plans to priya.nair@example.com. I'm Priya.",
    ]),
    ("Wants to negotiate (English)", [
        "Is the Downtown 2-bed with the Burj Khalifa view still available?",
        "I'd like to offer 3 million for it. Can you accept that?",
    ]),
    ("Just browsing (English)", [
        "just looking really, what kind of prices are there in Dubai Marina?",
        "ok thanks, not buying anytime soon, maybe next year",
    ]),
    ("Townhouse buyer (Arabic)", [
        "مرحباً، أريد شراء تاون هاوس من 3 غرف في المرابع العربية",
        "الميزانية 3 ملايين درهم، والدفع نقداً، وأريد الانتقال في أقرب وقت",
        "أريد معاينة العقار يوم السبت إن أمكن",
        "الموعد الأول مناسب. اسمي خالد المنصوري ورقمي 0509876543",
    ]),
]


def run(persona_idx: int | None = None) -> None:
    db.get()
    knowledge.ensure_loaded()
    for i, (label, turns) in enumerate(PERSONAS, 1):
        if persona_idx and i != persona_idx:
            continue
        conv = agent.new_conversation("web")
        agent.add_message(conv, "ai", "Hi, welcome to Harbor & Key Realty! How can I help you today?")
        print(f"\n=== {i}. {label} ({conv}) ===")
        for text in turns:
            print(f"\nCUSTOMER: {text}")
            t0 = time.time()
            result = agent.handle_customer_message(conv, text)
            print(f"AI ({time.time() - t0:.1f}s): {result['reply']}")
            if result.get("status") != "ai":
                print(f"[chat is now with a human: {result.get('status')}]")
                break
        lead = db.one("SELECT score, temperature, stage FROM leads WHERE conversation_id=?", (conv,))
        print(f"--> lead score {lead['score']} ({lead['temperature']}), stage: {lead['stage']}")
    path = export()
    print(f"\nSaved the recorded demo to {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, help="run just persona number N")
    run(ap.parse_args().only)
