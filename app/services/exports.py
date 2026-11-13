"""PDF and Excel renderers sharing the calculated report payload."""
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

def _rows(kind,d):
    out=[]
    if kind in ("income","all"):
        rows=[["Income Statement", "Amount"]]+[[a.name,v] for a,v in d["income"]]+[[a.name,-v] for a,v in d["expenses"]]+[["Net profit / (loss)",d["net"]]]
        out.append(("Income Statement",rows))
    if kind in ("balance","all"):
        rows=[["Balance Sheet", "Amount"]]+[["Assets · "+a.name,v] for a,v in d["assets"]]+[["Total assets",d["asset_total"]]]+[["Liabilities · "+a.name,v] for a,v in d["liabilities"]]+[["Equity · "+a.name,v] for a,v in d["equity"]]+[["Prior retained earnings",d["opening_earnings"]],["Current period earnings",d["net"]],["Total liabilities and equity",d["liability_total"]+d["equity_total"]]]
        out.append(("Balance Sheet",rows))
    if kind in ("cashflow","all"):
        rows=[["Cash Flow Statement", "Amount"]]+[[x.title()+" activities",d["cash_flows"].get(x,0)] for x in ("operating","investing","financing")]+[["Opening cash and bank",d["cash_start"]],["Net change",d["cash_change"]],["Closing cash and bank",d["cash_end"]]]
        out.append(("Cash Flow",rows))
    return out

def make_pdf(kind,data,company,start,end):
    buf=BytesIO(); doc=SimpleDocTemplate(buf,pagesize=letter,pageCompression=0); styles=getSampleStyleSheet(); story=[Paragraph(company,styles["Title"]),Paragraph(f"Financial statements · {start} to {end}",styles["Normal"]),Spacer(1,18)]
    for title,rows in _rows(kind,data):
        rendered=[[str(cell) for cell in row] for row in rows]
        story.extend([Paragraph(title,styles["Heading2"]),Table(rendered,colWidths=[370,110],style=TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#173b61")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),.35,colors.lightgrey),("ALIGN",(1,1),(1,-1),"RIGHT"),("BOTTOMPADDING",(0,0),(-1,-1),6)])),Spacer(1,16)])
    doc.build(story); return buf.getvalue()

def make_xlsx(kind,data,company,start,end):
    wb=Workbook(); wb.remove(wb.active)
    for title,rows in _rows(kind,data):
        ws=wb.create_sheet(title); ws.append([company]); ws.append([f"{start} to {end}"]); ws.append([])
        for row in rows: ws.append(row)
        ws.freeze_panes="A4"; ws.column_dimensions["A"].width=38; ws.column_dimensions["B"].width=18
        for cell in ws[4]: cell.font=Font(bold=True,color="FFFFFF"); cell.fill=PatternFill("solid",fgColor="173B61")
        for row in ws.iter_rows(min_row=5,min_col=2,max_col=2): row[0].number_format='#,##0.00;[Red](#,##0.00)'
    buf=BytesIO(); wb.save(buf); return buf.getvalue()
