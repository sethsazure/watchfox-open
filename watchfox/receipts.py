"""PDF receipts, drawn on request from Helcim's record of a payment. Nothing is saved."""
from typing import Any, Dict

from fpdf import FPDF


def _t(v: Any) -> str:
    """The built-in PDF fonts only draw Latin-1; anything else becomes '?' instead of failing."""
    return str(v if v is not None else "").encode("latin-1", "replace").decode("latin-1")


def make(row: Dict[str, Any], raw: Dict[str, Any], org_name: str, email: str, plan_label: str = "") -> bytes:
    refund = row["kind"] == "refund"
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, 20)
    pdf.add_page()
    pdf.set_margins(20, 20, 20)
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 12, "WatchFox", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(110)
    pdf.cell(0, 6, "Refund receipt" if refund else "Payment receipt", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_text_color(0)
    pdf.set_font("Helvetica", "B", 26)
    pdf.cell(0, 14, _t(("-" if refund else "") + f"{row['currency']} ${row['amount']:,.2f}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    lines = [("Date", row["date"]), ("Receipt number", row["id"]), ("Billed to", org_name), ("Account email", email),
             ("Description", ("Refund" if refund else "WatchFox subscription") + (f" - {plan_label.title()} plan" if plan_label and not refund else "")),
             ("Paid with", " ".join(x for x in (row["brand"], f"ending {row['card']}" if row["card"] else "") if x)),
             ("Approval code", raw.get("approvalCode")), ("Status", "Approved")]
    for k, v in lines:
        if not v:
            continue
        pdf.set_font("Helvetica", "", 10); pdf.set_text_color(110)
        pdf.cell(45, 8, _t(k))
        pdf.set_font("Helvetica", "", 11); pdf.set_text_color(0)
        pdf.cell(0, 8, _t(v), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(225); pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 9); pdf.set_text_color(110)
    pdf.multi_cell(0, 5, "Card payments are processed by Helcim. WatchFox never sees or stores your card number. Amounts are in US dollars. "
                         "Questions about this receipt: hello@watchfox.run")
    return bytes(pdf.output())
