"""Generate 3 realistic supplier quote PDFs with different layouts.

Each models a real-world variation the extraction pipeline must survive:
  A) Nordtech BV  - clean modern layout, dot-decimal prices, tidy table
  B) Sudhaus GmbH - German layout, comma-decimal prices, "Angebot" heading,
                    VAT 19%, dates DD.MM.YYYY, gridless item table, DE address labels
  C) Atlas Fittings - noisy footer (bank details), discount column, wrapped
                      descriptions, NO delivery-date label: exercises the
                      needed-by <- validity fallback (with warning)
  D) Meridian Components - 2 pages, item table continues on page 2 with a
                           repeated header; multi-line Bill to / Ship to blocks
  E) Vantorre Marine  - mixed pricing: rows 1 & 3 carry real unit prices
                        (confidence 0.90, green), the custom row is priced per
                        lot ("on request") -> its unit price is inferred at
                        confidence 0.55 (red). The 3% discount sits between
                        line sum and Net Total, so the sum-vs-subtotal bump to
                        0.92 (which would repaint the red row green) does not
                        fire; the mismatch warning does.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                Spacer, PageBreak)
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
import os

OUT = os.environ.get("SAMPLES_DIR", "/data/samples")
os.makedirs(OUT, exist_ok=True)

base = getSampleStyleSheet()
cell = ParagraphStyle("cell", parent=base["Normal"], fontSize=9, leading=11)


def make_header_footer(title, addr):
    draw_small = ParagraphStyle("sml", parent=base["Normal"], fontSize=8, textColor=colors.grey)

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawString(20 * mm, 280 * mm, title)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(20 * mm, 276 * mm, addr)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(20 * mm, 14 * mm, "Page 1 - Supplier quotation document")
        canvas.restoreState()

    return hdr, draw_small, draw


def quote_a():
    doc = SimpleDocTemplate(f"{OUT}/quote_nordtech.pdf", pagesize=A4, title="Quote")
    small = ParagraphStyle("sml", parent=base["Normal"], fontSize=8, textColor=colors.grey)

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawString(20 * mm, 280 * mm, "NORTECH BV")
        canvas.setFont("Helvetica", 8)
        canvas.drawString(20 * mm, 276 * mm, "Techniekweg 12, 1041 AB Amsterdam, NL - VAT: NL802347123B01")
        canvas.setFont("Helvetica", 7)
        canvas.drawString(20 * mm, 14 * mm, "Page 1 - Supplier quotation document")
        canvas.restoreState()

    rows = [
        ["POS", "ITEM / DESCRIPTION", "SKU", "QTY", "UNIT PRICE", "TOTAL"],
        ["1", "Hydraulic press machine HPM-200", "SKU-88231", "2", "14,250.00", "28,500.00"],
        ["2", "Cooling unit CX-40", "SKU-44102", "3", "2,180.00", "6,540.00"],
        ["3", "Spare parts kit standard", "SKU-10098", "5", "340.00", "1,700.00"],
    ]
    t = Table(rows, colWidths=[12 * mm, 60 * mm, 28 * mm, 16 * mm, 30 * mm, 30 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFEFEF")),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
    ]))
    story = [
        Paragraph("QUOTATION", base["Title"]),
        Spacer(1, 4 * mm),
        Paragraph("Vendor: <b>NORTECH BV</b> (Vendor ID: NL-4012)", base["Normal"]),
        Paragraph("Quote Reference: <b>NTQ-2025-0417</b>", base["Normal"]),
        Paragraph("Quote Date: <b>17.10.2025</b>", base["Normal"]),
        Paragraph("Valid Until: <b>30.11.2025</b>", base["Normal"]),
        Paragraph("Requested Delivery Date: <b>15.12.2025</b>", base["Normal"]),
        Spacer(1, 3 * mm),
        Paragraph("Bill to: Lio Procurement BV, Keizersgracht 281, 1016 ED Amsterdam, NL", base["Normal"]),
        Paragraph("Ship to: Lio Warehouse Berlin, Mühlenstraße 12, 10243 Berlin, DE", base["Normal"]),
        Spacer(1, 6 * mm),
        t,
        Spacer(1, 4 * mm),
        Paragraph("Subtotal: <b>36,740.00 EUR</b>", base["Normal"]),
        Paragraph("VAT (21%): <b>7,715.40 EUR</b>", base["Normal"]),
        Paragraph("<b>GRAND TOTAL: 44,455.40 EUR</b>", base["Normal"]),
        Spacer(1, 4 * mm),
        Paragraph("Payment Terms: Net 30 | Delivery: Ex Works", small),
    ]
    doc.build(story, onFirstPage=draw)


def quote_b():
    doc = SimpleDocTemplate(f"{OUT}/quote_sudhaus.pdf", pagesize=A4, title="Angebot")

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawString(20 * mm, 280 * mm, "SUDHAUS MASCHINENBAU GMBH")
        canvas.setFont("Helvetica", 8)
        canvas.drawString(20 * mm, 276 * mm, "Industriestrasse 88, 45143 Essen, DE - VAT: DE812345678")
        canvas.setFont("Helvetica", 7)
        canvas.drawString(20 * mm, 14 * mm, "Page 1 - Supplier quotation document")
        canvas.restoreState()

    cell = ParagraphStyle("c", fontName="Helvetica", fontSize=9, leading=11)
    rows = [
        ["Pos.", "Bezeichnung", "Art.-Nr.", "Menge", "Einzelpreis", "Gesamt"],
        ["1", Paragraph("Spindelpresse SP-15<br/>Includes safety guard, CE cert and manual override.", cell), "SU-22114", "1", "9.750,00", "9.750,00"],
        ["2", Paragraph("Hydraulikaggregat HA-9<br/>Pressure range 0-700 bar.", cell), "SU-31107", "4", "1.480,50", "5.922,00"],
    ]
    t = Table(rows, colWidths=[12 * mm, 62 * mm, 24 * mm, 14 * mm, 30 * mm, 30 * mm])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.black),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story = [
        Paragraph("ANGEBOT / QUOTATION", base["Title"]),
        Spacer(1, 4 * mm),
        Paragraph("Lieferant: <b>SUDHAUS MASCHINENBAU GMBH</b> (Lieferanten-Nr.: DE-1147)", base["Normal"]),
        Paragraph("Angebots-Nr.: <b>SU-AN-2025-8842</b>", base["Normal"]),
        Paragraph("Datum: <b>14.10.2025</b>", base["Normal"]),
        Paragraph("Gueltig bis: <b>14.11.2025</b>", base["Normal"]),
        Paragraph("Liefertermin: <b>20.01.2026</b>", base["Normal"]),
        Spacer(1, 3 * mm),
        Paragraph("Rechnungsadresse: Lio Procurement BV, Keizersgracht 281, 1016 ED Amsterdam, NL", base["Normal"]),
        Paragraph("Lieferadresse: Lio Warehouse Berlin, Mühlenstraße 12, 10243 Berlin, DE", base["Normal"]),
        Spacer(1, 6 * mm),
        t,
        Spacer(1, 4 * mm),
        Paragraph("Nettosumme: <b>15.672,00 EUR</b>", base["Normal"]),
        Paragraph("MwSt. 19%: <b>2.977,68 EUR</b>", base["Normal"]),
        Paragraph("<b>Gesamtbetrag: 18.649,68 EUR</b>", base["Normal"]),
        Spacer(1, 4 * mm),
    ]
    doc.build(story, onFirstPage=draw)


def quote_c():
    doc = SimpleDocTemplate(f"{OUT}/quote_atlas.pdf", pagesize=A4, title="Atlas quotation")

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(20 * mm, 280 * mm, "ATLAS FITTINGS LTD")
        canvas.setFont("Helvetica", 8)
        canvas.drawString(20 * mm, 276 * mm, "Unit 7 Riverside Park, Manchester M17 1AB, UK - VAT: GB347281123")
        canvas.setFont("Helvetica", 7)
        canvas.drawString(20 * mm, 14 * mm, "Page 1 - Supplier quotation document")
        canvas.restoreState()

    cell = ParagraphStyle("c", fontName="Helvetica", fontSize=9, leading=11)
    rows = [
        ["Line", "Description", "Part No", "Qty", "Unit (GBP)", "Disc.", "Line Total"],
        ["01", Paragraph("Stainless steel pipe flange DN150<br/>class 300, raised face, EN 1092-1", cell), "ATF-1150", "12", "185.00", "5%", "2,109.00"],
        ["02", Paragraph("Ball valve, 2-piece, 1/2 inch<br/>PN40, stainless 316L body", cell), "ATF-2287", "24", "42.50", "0%", "1,020.00"],
        ["03", Paragraph("Butterfly valve DN100 wafer type<br/>with gearbox actuator", cell), "ATF-3091", "6", "310.00", "10%", "1,674.00"],
    ]
    t = Table(rows, colWidths=[11 * mm, 56 * mm, 22 * mm, 12 * mm, 22 * mm, 12 * mm, 24 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6F1")),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
    ]))
    story = [
        Paragraph("ATLAS FITTINGS LTD - OFFICIAL QUOTATION", base["Title"]),
        Spacer(1, 4 * mm),
        Paragraph("Quotation No: <b>ATL/Q/2598/2025</b>", base["Normal"]),
        Paragraph("Vendor: <b>ATLAS FITTINGS LTD</b> (Vendor ID: GB-9033)", base["Normal"]),
        Paragraph("Issue Date: <b>2025-10-16</b>", base["Normal"]),
        Paragraph("Offer Expiry: <b>2025-12-16</b>", base["Normal"]),
        Paragraph("Lead Time: <b>4 weeks</b> | Delivery Terms: <b>CIF Rotterdam</b>", base['Normal']),
        Spacer(1, 3 * mm),
        Paragraph("Bill to: Lio Procurement BV, Keizersgracht 281, 1016 ED Amsterdam, NL", base["Normal"]),
        Paragraph("Ship to: Lio Warehouse Berlin, Mühlenstraße 12, 10243 Berlin, DE", base["Normal"]),
        Spacer(1, 6 * mm),
        t,
        Spacer(1, 4 * mm),
        Paragraph("Net Total: <b>GBP 4,803.00</b>", base["Normal"]),
        Paragraph("VAT (20%): <b>GBP 960.60</b>", base["Normal"]),
        Paragraph("<b>TOTAL PAYABLE: GBP 5,763.60</b>", base["Normal"]),
        Spacer(1, 6 * mm),
        Paragraph("BANK: HSBC UK | IBAN: GB74HBUK40271234567890 | SWIFT: HBUKGB4B", small := ParagraphStyle("s", fontName="Helvetica", fontSize=8, textColor=colors.grey)),
        Paragraph("This quotation is subject to our standard terms and conditions of sale.", small),
    ]
    doc.build(story, onFirstPage=draw)


def quote_d():
    """2-page quote: items continue on page 2 with a REPEATED column-header
    row (classic long-quote continuation); multi-line Bill to / Ship to blocks;
    both delivery AND validity dates present, so neither is conflated."""
    doc = SimpleDocTemplate(f"{OUT}/quote_meridian.pdf", pagesize=A4, title="Angebot")
    cell = ParagraphStyle("c", fontName="Helvetica", fontSize=9, leading=11)

    def draw_first(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawString(20 * mm, 280 * mm, "MERIDIAN COMPONENTS GMBH")
        canvas.setFont("Helvetica", 8)
        canvas.drawString(20 * mm, 276 * mm,
                          "Werkstrasse 5, 85055 Ingolstadt, DE - VAT: DE987654321")
        canvas.setFont("Helvetica", 7)
        canvas.drawString(20 * mm, 14 * mm, "Page 1 - Supplier quotation document")
        canvas.restoreState()

    def draw_later(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(20 * mm, 14 * mm, "Page 2 - continued from page 1")
        canvas.restoreState()

    hdr = ["Pos.", "Bezeichnung", "Art.-Nr.", "Menge", "Einzelpreis", "Gesamt"]
    p1 = [
        ["1", Paragraph("Schaltschrank 400V-C (control cabinet)<br/>incl. wiring diagram set and CE documentation", cell), "MC-1001", "2", "980,00", "1.960,00"],
        ["2", Paragraph("Kabelsatz H07RN-F 5x2.5mm²", cell), "MC-1100", "10", "45,50", "455,00"],
        ["3", Paragraph("Montagerahmen XL (mounting frame)", cell), "MC-1200", "1", "3.450,00", "3.450,00"],
        ["4", Paragraph("Inbetriebnahme vor Ort (on-site commissioning), day rate incl. travel", cell), "MC-1300", "1", "640,00", "640,00"],
    ]
    p2 = [
        ["5", Paragraph("Ersatzteilpaket (spare parts package)", cell), "MC-1400", "1", "210,00", "210,00"],
        ["6", Paragraph("Dokumentationspaket DE/EN, printed + digital", cell), "MC-1401", "2", "95,60", "191,20"],
    ]
    tstyle = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFEFEF")),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    t1 = Table([hdr] + p1, colWidths=[12 * mm, 66 * mm, 22 * mm, 14 * mm, 26 * mm, 26 * mm])
    t1.setStyle(TableStyle(tstyle))
    t2 = Table([hdr] + p2, colWidths=t1._colWidths)

    story = [
        Paragraph("ANGEBOT / QUOTATION", base["Title"]),
        Spacer(1, 4 * mm),
        Paragraph("Lieferant: <b>MERIDIAN COMPONENTS GMBH</b> (Lieferanten-Nr.: DE-2288)", base["Normal"]),
        Paragraph("Angebots-Nr.: <b>MC-AN-2026-0210</b>", base["Normal"]),
        Paragraph("Datum: <b>19.10.2026</b>", base["Normal"]),
        Paragraph("Gueltig bis: <b>19.11.2026</b>", base["Normal"]),
        Paragraph("Liefertermin: <b>19.12.2026</b>", base["Normal"]),
        Spacer(1, 3 * mm),
        Paragraph("Bill to:", base["Normal"]),
        Paragraph("Lio Procurement BV", base["Normal"]),
        Paragraph("Keizersgracht 281, 1016 ED Amsterdam, NL", base["Normal"]),
        Paragraph("Ship to:", base["Normal"]),
        Paragraph("Lio Warehouse Berlin", base["Normal"]),
        Paragraph("Muehlenstrasse 12, 10243 Berlin, DE", base["Normal"]),
        Spacer(1, 6 * mm),
        t1,
        PageBreak(),
        t2,
        Spacer(1, 4 * mm),
        Paragraph("Nettosumme: <b>6.906,20 EUR</b>", base["Normal"]),
        Paragraph("MwSt. 19%: <b>1.312,18 EUR</b>", base["Normal"]),
        Paragraph("<b>Gesamtbetrag: 8.218,38 EUR</b>", base["Normal"]),
        Spacer(1, 4 * mm),
        Paragraph("Zahlungsbedingungen: Net 30 Tage | Incoterms: DAP Ingolstadt", base["Normal"]),
    ]
    doc.build(story, onFirstPage=draw_first, onLaterPages=draw_later)


def quote_e():
    """Mixed-confidence quote: rows 1 and 3 carry real list prices (qty+unit+
    line_total -> 0.90, green); the custom row is 'priced per lot' (no unit
    cell parses) -> its unit price is inferred as line_total / qty at
    confidence 0.55 (red, with a warning). The 3% trade discount is applied
    AFTER the line sum ("Net Total"), so the line-sum == subtotal bump to 0.92
    deliberately does NOT fire (it would repaint the red row green), and the
    'line-total sum does not match subtotal' warning appears instead."""
    doc = SimpleDocTemplate(f"{OUT}/quote_vantorre.pdf", pagesize=A4,
                            title="Offer")
    small = ParagraphStyle("s", fontName="Helvetica", fontSize=8, textColor=colors.grey)

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawString(20 * mm, 280 * mm, "VANTORRE MARINE SUPPLIES NV")
        canvas.setFont("Helvetica", 8)
        canvas.drawString(20 * mm, 276 * mm,
                          "Havenweg 44, 2030 Antwerpen, BE - VAT: BE0456789123")
        canvas.setFont("Helvetica", 7)
        canvas.drawString(20 * mm, 14 * mm, "Page 1 - Supplier quotation document")
        canvas.restoreState()

    rows = [
        ["Pos.", "ITEM / DESCRIPTION", "SKU", "QTY", "UNIT PRICE", "TOTAL"],
        ["1", Paragraph("Anchor chain 22 mm galvanized, delivered in 40 m lengths<br/>"
                        "incl. proof-load certificates", cell),
         "VT-3301", "40", "100.00", "4,000.00"],
        ["2", Paragraph("Mooring rope Polyamp 48 mm, 200 m coil, spliced to order", cell),
         "VT-2287", "2", "on request", "1,960.00"],
        ["3", Paragraph("Deck winch gypsy wheel set incl. mounting kit<br/>"
                        "spare bolt set and hydraulic hose package", cell),
         "VT-3091", "1", "2,280.00", "2,280.00"],
    ]
    t = Table(rows, colWidths=[12 * mm, 62 * mm, 24 * mm, 14 * mm, 26 * mm, 28 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFEFEF")),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story = [
        Paragraph("QUOTATION / UNIT PRICES ON REQUEST", base["Title"]),
        Spacer(1, 4 * mm),
        Paragraph("Vendor: <b>VANTORRE MARINE SUPPLIES NV</b> (Vendor ID: BE-4471)", base["Normal"]),
        Paragraph("Quote Reference: <b>VT-2026-0143</b>", base["Normal"]),
        Paragraph("Quote Date: <b>19.10.2026</b>", base["Normal"]),
        Paragraph("Valid Until: <b>30.11.2026</b>", base["Normal"]),
        Paragraph("Requested Delivery Date: <b>15.01.2027</b>", base["Normal"]),
        Spacer(1, 3 * mm),
        Paragraph("Bill to: Lio Procurement BV, Keizersgracht 281, 1016 ED Amsterdam, NL", base["Normal"]),
        Paragraph("Ship to: Lio Warehouse Berlin, Mühlenstraße 12, 10243 Berlin, DE", base["Normal"]),
        Spacer(1, 6 * mm),
        t,
        Spacer(1, 3 * mm),
        Paragraph("List items priced normally; custom items (spliced to order) are quoted per lot, unit price on request.", small),
        Spacer(1, 4 * mm),
        Paragraph("List value: 8,240.00 EUR, less trade discount 3% (-247.20 EUR)", base["Normal"]),
        Paragraph("Net Total: <b>7,992.80 EUR</b>", base["Normal"]),
        Paragraph("VAT (19%): <b>1,518.63 EUR</b>", base["Normal"]),
        Paragraph("<b>TOTAL PAYABLE: 9,511.43 EUR</b>", base["Normal"]),
        Spacer(1, 4 * mm),
        Paragraph("Payment Terms: Net 30 | Delivery: FCA Antwerpen", small),
    ]
    doc.build(story, onFirstPage=draw)


if __name__ == "__main__":
    quote_a()
    quote_b()
    quote_c()
    quote_d()
    quote_e()
    print("Generated:", os.listdir(OUT))