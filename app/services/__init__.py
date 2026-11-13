"""Accounting operations and report calculations over the posted journal ledger."""
import csv, io, hashlib, json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from sqlalchemy import select
from app.extensions import db
from app.models import Account, Company, JournalEntry, JournalEntryLine, User

ZERO = Decimal("0.00")
TYPES = ("asset", "liability", "equity", "income", "expense")

def csv_source_key(company_id,row):
    canonical=json.dumps([company_id,row["date"].isoformat(),row["type"],format(row["amount"],".2f"),row["description"],row["account_code"]],ensure_ascii=False,separators=(",",":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def seed_accounts(company):
    defaults = [("1000","Cash","asset",None,True),("1010","Bank","asset",None,True),
      ("1100","Accounts Receivable","asset","operating",False),("1500","Equipment","asset","investing",False),
      ("2000","Accounts Payable","liability","operating",False),("2100","Loans Payable","liability","financing",False),
      ("3000","Owner Equity","equity","financing",False),("4000","Sales Income","income","operating",False),
      ("4100","Other Income","income","operating",False),("5000","Operating Expenses","expense","operating",False),
      ("5100","Equipment Purchases","expense","investing",False)]
    for code,name,typ,cf,cash in defaults:
        db.session.add(Account(company_id=company.id,code=code,name=name,account_type=typ,cash_flow_classification=cf,is_cash=cash))
    db.session.flush()

def post_entry(company_id, user_id, entry_date, description, lines, kind="normal", source_key=None, reversal_of_id=None):
    if kind not in ("normal","opening","transfer","reversal"): raise ValueError("Unknown journal entry type.")
    if (kind=="reversal") != (reversal_of_id is not None): raise ValueError("Reversal entries must identify the original entry.")
    if not User.query.filter_by(id=user_id,company_id=company_id,active=True).first(): raise ValueError("Posting user does not belong to this company.")
    if len(lines)<2: raise ValueError("An entry must have at least two lines.")
    if not description or not description.strip(): raise ValueError("Entry description is required.")
    total_d=total_c=ZERO; normalized=[]
    for account_id,debit,credit in lines:
        account=db.session.execute(select(Account).where(Account.id==account_id,Account.company_id==company_id,Account.active.is_(True))).scalar_one_or_none()
        if account is None: raise ValueError("An account is unavailable for this company.")
        d,c=Decimal(str(debit)),Decimal(str(credit))
        if d<0 or c<0 or d.as_tuple().exponent < -2 or c.as_tuple().exponent < -2 or (d>0 and c>0) or (d==0 and c==0): raise ValueError("Each journal line must contain a positive amount with at most two decimals on one side only.")
        total_d+=d; total_c+=c; normalized.append((account,d,c))
    if total_d!=total_c: raise ValueError("Debits and credits must balance exactly.")
    entry=JournalEntry(company_id=company_id,created_by_id=user_id,date=entry_date,description=description.strip(),status="draft",kind=kind,source_key=source_key,reversal_of_id=reversal_of_id)
    db.session.add(entry); db.session.flush()
    for account,d,c in normalized: db.session.add(JournalEntryLine(entry=entry,account=account,debit=d,credit=c))
    entry.status="posted"
    db.session.flush(); return entry

def create_draft(company_id,user_id,entry_date,description,lines):
    """Save an editable, unposted entry; reports intentionally never read it."""
    if not User.query.filter_by(id=user_id,company_id=company_id,active=True).first(): raise ValueError("Draft creator does not belong to this company.")
    if len(lines)<2: raise ValueError("A draft must have at least two lines.")
    entry=JournalEntry(company_id=company_id,created_by_id=user_id,date=entry_date,description=description.strip(),status="draft")
    db.session.add(entry); db.session.flush()
    total_d=total_c=ZERO
    for account_id,debit,credit in lines:
        account=Account.query.filter_by(id=account_id,company_id=company_id,active=True).first()
        if not account: raise ValueError("Draft account is unavailable for this company.")
        d,c=Decimal(str(debit)),Decimal(str(credit))
        if d<0 or c<0 or d.as_tuple().exponent < -2 or c.as_tuple().exponent < -2 or (d>0 and c>0): raise ValueError("Draft lines must be nonnegative amounts with at most two decimals, on one side only.")
        total_d+=d; total_c+=c
        db.session.add(JournalEntryLine(entry=entry,account=account,debit=d,credit=c))
    return entry

def post_draft(entry):
    if entry.status!="draft": raise ValueError("Only draft entries can be posted.")
    if len(entry.lines)<2 or any((l.debit<=0 and l.credit<=0) or (l.debit>0 and l.credit>0) or l.debit.as_tuple().exponent < -2 or l.credit.as_tuple().exponent < -2 or not Account.query.filter_by(id=l.account_id,company_id=entry.company_id,active=True).first() for l in entry.lines) or sum((l.debit for l in entry.lines),ZERO)!=sum((l.credit for l in entry.lines),ZERO): raise ValueError("Draft entry is not balanced or contains invalid lines; it cannot be posted.")
    entry.status="posted"; db.session.flush(); return entry

def ledger(company_id, end=None, start=None):
    q=select(JournalEntryLine,JournalEntry,Account).join(JournalEntry).join(Account).where(JournalEntry.company_id==company_id,JournalEntry.status=="posted")
    if start: q=q.where(JournalEntry.date>=start)
    if end: q=q.where(JournalEntry.date<=end)
    return db.session.execute(q.order_by(JournalEntry.date,JournalEntry.id,JournalEntryLine.id)).all()

def reports(company_id, start, end):
    allrows=ledger(company_id,end=end); period=[r for r in allrows if start<=r[1].date<=end]
    accounts={a.id:a for a in Account.query.filter_by(company_id=company_id).all()}
    balances=defaultdict(lambda: ZERO)
    for line,entry,acc in allrows:
        balances[acc.id]+=line.debit-line.credit
    income=[]; expenses=[]
    for aid,amount in balances.items():
        a=accounts[aid]
        if a.account_type=="income": income.append((a, -amount))
        if a.account_type=="expense": expenses.append((a, amount))
    # Income statement uses only the selected reporting period.
    p_income=defaultdict(lambda:ZERO); p_exp=defaultdict(lambda:ZERO)
    for line,entry,a in period:
        if a.account_type=="income": p_income[a.id]+=line.credit-line.debit
        elif a.account_type=="expense": p_exp[a.id]+=line.debit-line.credit
    inc=[(accounts[k],v) for k,v in p_income.items()]; exp=[(accounts[k],v) for k,v in p_exp.items()]
    net=sum((v for _,v in inc),ZERO)-sum((v for _,v in exp),ZERO)
    assets=[]; liabs=[]; equity=[]
    for aid,raw in balances.items():
        a=accounts[aid]
        amount=raw if a.account_type=="asset" else -raw
        if a.account_type=="asset": assets.append((a,amount))
        elif a.account_type=="liability": liabs.append((a,amount))
        elif a.account_type=="equity": equity.append((a,amount))
    # Retained earnings excludes current period activity; add current period net income once.
    prior=defaultdict(lambda:ZERO)
    for line,e,a in allrows:
        if e.date<start:
            if a.account_type=="income": prior["net"]+=line.credit-line.debit
            elif a.account_type=="expense": prior["net"]-=line.debit-line.credit
    opening_earnings=prior["net"]
    # Account balances already include all historical income/expense only in P&L buckets, not equity.
    eq_total=sum((v for _,v in equity),ZERO)+opening_earnings+net
    entries={entry.id:entry for _,entry,_ in allrows}
    opening_reversals={e.id for e in entries.values() if e.kind=="reversal" and entries.get(e.reversal_of_id) and entries[e.reversal_of_id].kind=="opening"}
    cf=defaultdict(lambda:ZERO); by_entry=defaultdict(list)
    for line,e,a in period: by_entry[e.id].append((line,e,a))
    for rows in by_entry.values():
        entry=rows[0][1]
        if entry.kind in ("opening","transfer") or entry.id in opening_reversals: continue
        cash_delta=sum((line.debit-line.credit for line,e,a in rows if a.is_cash),ZERO)
        if not cash_delta: continue  # excludes non-cash-only entries
        # In a balanced entry the non-cash lines' credit-minus-debit equals the cash movement.
        # Allocate each portion once by the counterpart account's cash-flow class.
        for line,e,a in rows:
            if a.is_cash: continue
            classification=a.cash_flow_classification
            if classification not in ("operating","investing","financing"):
                raise ValueError(f"Missing cash flow classification for {a.code} {a.name}.")
            cf[classification]+=line.credit-line.debit
    cash= sum((balances[a.id] for a in accounts.values() if a.is_cash),ZERO)
    cf_start=ZERO
    for line,e,a in ledger(company_id,end=start.fromordinal(start.toordinal()-1)):
        if a.is_cash: cf_start+=line.debit-line.credit
    # Opening-balance journals establish the reporting starting position, not period cash receipts.
    for line,e,a in period:
        if (e.kind=="opening" or e.id in opening_reversals) and a.is_cash: cf_start+=line.debit-line.credit
    return {"income":inc,"expenses":exp,"net":net,"assets":assets,"liabilities":liabs,"equity":equity,
      "opening_earnings":opening_earnings,"equity_total":eq_total,"asset_total":sum((v for _,v in assets),ZERO),
      "liability_total":sum((v for _,v in liabs),ZERO),"cash_flows":dict(cf),"cash_start":cf_start,"cash_end":cash,
      "cash_change":sum(cf.values(),ZERO),"balanced":sum((v for _,v in assets),ZERO)==sum((v for _,v in liabs),ZERO)+eq_total}

def parse_csv(content):
    reader=csv.DictReader(io.StringIO(content)); required={"date","type","amount","description","account_code"}
    if not reader.fieldnames or not required.issubset({x.strip().lower() for x in reader.fieldnames}): raise ValueError("CSV requires date,type,amount,description,account_code columns.")
    result=[]; invalid=[]
    for n,row in enumerate(reader,2):
        if None in row:
            invalid.append({"row":n,"error":"row has more values than the header"}); continue
        r={k.strip().lower(): (v or "").strip() for k,v in row.items() if k}
        try:
            d=date.fromisoformat(r["date"]); kind=r["type"].lower(); amount=Decimal(r["amount"])
            if kind not in ("income","expense","asset_purchase"): raise ValueError("type must be income, expense or asset_purchase")
            if not amount.is_finite() or amount<=0 or amount.as_tuple().exponent < -2: raise ValueError("amount must be finite, positive and have at most two decimals")
            if not r["description"] or not r["account_code"]: raise ValueError("description and account_code are required")
            result.append({"date":d,"type":kind,"amount":amount,"description":r["description"],"account_code":r["account_code"],"row":n})
        except (ValueError,KeyError,InvalidOperation) as exc: invalid.append({"row":n,"error":str(exc)})
    if not result and not invalid: invalid.append({"row":2,"error":"CSV contains no transaction rows"})
    return result,invalid
