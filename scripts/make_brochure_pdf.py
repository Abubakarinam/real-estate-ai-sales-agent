"""Builds the demo PDF brochure in knowledge/ (shows that the agent reads PDFs).

Run: python scripts/make_brochure_pdf.py   (needs: pip install reportlab)
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).resolve().parent.parent / "knowledge" / "07-creek-vista-brochure.pdf"

styles = getSampleStyleSheet()
story = [
    Paragraph("Creek Vista Residences - Dubai Creek Harbour", styles["Title"]),
    Paragraph("Off-plan launch brochure (demo data, fictional project) - Harbor &amp; Key Realty", styles["Italic"]),
    Spacer(1, 12),
    Paragraph("Project overview", styles["Heading2"]),
    Paragraph(
        "Creek Vista Residences is a 32-floor tower in Dubai Creek Harbour with views of the creek, "
        "the Ras Al Khor wildlife sanctuary and the Downtown skyline. Expected handover is Q4 2028. "
        "Amenities: infinity pool, gym, kids' play area, co-working lounge, and covered parking.",
        styles["BodyText"],
    ),
    Spacer(1, 8),
    Paragraph("Unit types and starting prices", styles["Heading2"]),
]
units = [
    ["Unit", "Size (sq ft)", "Starting price (AED)"],
    ["1 bedroom", "740 - 810", "1,600,000"],
    ["2 bedrooms", "1,180 - 1,260", "2,450,000"],
    ["3 bedrooms", "1,650 - 1,720", "3,550,000"],
]
t = Table(units, hAlign="LEFT")
t.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f2a44")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ("PADDING", (0, 0), (-1, -1), 6),
]))
story += [t, Spacer(1, 8), Paragraph("Payment plan (60/40)", styles["Heading2"])]
plan = [
    ["Milestone", "Share of price"],
    ["On booking", "10%"],
    ["Within 60 days of booking", "10%"],
    ["During construction (6 instalments of 5%)", "30%"],
    ["At 70% construction completion", "10%"],
    ["On handover (Q4 2028)", "40%"],
]
p = Table(plan, hAlign="LEFT")
p.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f2a44")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ("PADDING", (0, 0), (-1, -1), 6),
]))
story += [p, Spacer(1, 8), Paragraph("Good to know", styles["Heading2"])]
for line in [
    "The 4% Dubai Land Department fee is paid on booking. No agency commission is charged to the buyer.",
    "Service charges are estimated at AED 16 per sq ft per year.",
    "Units of AED 2 million or more can qualify the buyer for the 10-year Golden Visa (conditions apply for off-plan).",
    "Launch offer: the developer pays the DEWA connection fees for bookings made before 31 December 2026.",
    "Contact Layla Nasser, Off-plan Specialist, for floor plans and availability.",
]:
    story.append(Paragraph("- " + line, styles["BodyText"]))

SimpleDocTemplate(str(OUT), pagesize=A4, title="Creek Vista Residences brochure").build(story)
print("Wrote", OUT)
