"""Authentication, transaction workflows, dashboard and financial reports."""
import csv, io, json
from datetime import date, timedelta
from decimal import Decimal
from functools import wraps
from urllib.parse import urljoin, urlsplit
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response, abort, current_app
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import Company, User, Account, JournalEntry, JournalEntryLine, CsvImportBatch, OpeningBalanceInitialization
from app.services import seed_accounts, post_entry, reports, ledger, parse_csv, csv_source_key

main_bp=Blueprint("main",__name__)

def report_operation_error(exc, generic_message):
    """Keep expected validation details while logging unexpected failures privately."""
    if isinstance(exc, ValueError):
        flash(str(exc), "danger")
    else:
        current_app.logger.exception("Application operation failed")
        flash(generic_message, "danger")

def is_safe_redirect(target):
    if not target: return False
    ref=urlsplit(request.host_url); test=urlsplit(urljoin(request.host_url,target))
    return test.scheme in ("http","https") and ref.netloc==test.netloc

def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args,**kwargs):
        if not current_user.is_admin: abort(403)
        return fn(*args,**kwargs)
    return wrapped

@main_bp.route("/")
def index():
    if current_user.is_authenticated: return redirect(url_for("main.dashboard"))
    return render_template("index.html")

@main_bp.route("/register",methods=["GET","POST"])
def register():
    if not current_app.config.get("ENABLE_PUBLIC_REGISTRATION",False): abort(404)
    if request.method=="POST":
        name=request.form.get("company","").strip(); user_name=request.form.get("name","").strip()
        email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
        confirmation=request.form.get("confirm_password","")
        if not name or not user_name: flash("Enter both a company name and administrator name.","danger")
        elif "@" not in email: flash("Enter a valid email address.","danger")
        elif not password: flash("Enter a password.","danger")
        elif len(password)<10: flash("Password must be at least 10 characters long.","danger")
        elif password!=confirmation: flash("Password and confirmation do not match.","danger")
        elif User.query.filter_by(email=email).first(): flash("That email is already registered.","danger")
        elif Company.query.filter(func.lower(Company.name)==name.casefold()).first(): flash("A company with that name is already registered.","danger")
        else:
            try:
                company=Company(name=name,currency=request.form.get("currency","INR").upper()[:3]); db.session.add(company); db.session.flush()
                user=User(company_id=company.id,email=email,name=user_name,role="administrator"); user.set_password(password); db.session.add(user); db.session.flush()
                seed_accounts(company); db.session.commit(); login_user(user); return redirect(url_for("main.dashboard"))
            except IntegrityError:
                db.session.rollback(); flash("That email or company name is already registered. No account was created.","danger")
            except Exception:
                db.session.rollback(); current_app.logger.exception("Company registration failed; transaction rolled back")
                flash("Registration could not be completed. No company or user records were saved.","danger")
    return render_template("form.html",title="Create your company",action="register")

@main_bp.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        user=User.query.filter_by(email=request.form.get("email"," ").strip().lower(),active=True).first()
        if user and user.check_password(request.form.get("password","")):
            target=request.args.get("next")
            if target and not is_safe_redirect(target): abort(400,"Invalid next URL.")
            login_user(user); return redirect(target or url_for("main.dashboard"))
        flash("Email or password is incorrect.","danger")
    return render_template("form.html",title="Sign in",action="login")

@main_bp.post("/logout")
@login_required
def logout(): logout_user(); return redirect(url_for("main.index"))

@main_bp.route("/team",methods=["GET","POST"])
@admin_required
def team():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); name=request.form.get("name","").strip(); password=request.form.get("password","")
        if User.query.filter_by(email=email).first() or not name or len(password)<10: flash("Use a new email, a name and a password of at least 10 characters.","danger")
        else:
            u=User(company_id=current_user.company_id,email=email,name=name,role="accountant"); u.set_password(password); db.session.add(u); db.session.commit(); flash("Accountant added.","success")
    return render_template("team.html",users=User.query.filter_by(company_id=current_user.company_id).all())

def account_form_values():
    code=request.form.get("code","").strip(); name=request.form.get("name","").strip()
    account_type=request.form.get("account_type","").strip().lower()
    classification=request.form.get("cash_flow_classification","").strip().lower() or None
    is_cash=request.form.get("is_cash")=="on"
    if not code or len(code)>20 or not name or len(name)>120: raise ValueError("Enter an account code (1–20 characters) and name (1–120 characters).")
    if account_type not in ("asset","liability","equity","income","expense"): raise ValueError("Choose a valid account type.")
    if classification not in (None,"operating","investing","financing"): raise ValueError("Choose a valid cash-flow classification.")
    if is_cash and account_type!="asset": raise ValueError("Only asset accounts can be marked as cash or bank.")
    if is_cash: classification=None
    elif not classification: raise ValueError("Choose a cash-flow classification for non-cash accounts.")
    return {"code":code,"name":name,"account_type":account_type,"cash_flow_classification":classification,"is_cash":is_cash}

def account_is_used(account_id):
    return db.session.execute(select(JournalEntryLine.id).join(JournalEntry).where(JournalEntryLine.account_id==account_id,JournalEntry.status=="posted").limit(1)).first() is not None

@main_bp.route("/accounts",methods=["GET","POST"])
@admin_required
def accounts():
    if request.method=="POST":
        try:
            values=account_form_values()
            if Account.query.filter_by(company_id=current_user.company_id,code=values["code"]).first(): raise ValueError("That account code already exists in this company.")
            db.session.add(Account(company_id=current_user.company_id,**values)); db.session.commit(); flash("Account created.","success"); return redirect(url_for("main.accounts"))
        except Exception as exc: db.session.rollback(); report_operation_error(exc,"The account could not be saved. Please try again.")
    return render_template("accounts.html",accounts=Account.query.filter_by(company_id=current_user.company_id).order_by(Account.code).all())

@main_bp.route("/accounts/<int:account_id>/edit",methods=["GET","POST"])
@admin_required
def edit_account(account_id):
    account=Account.query.filter_by(id=account_id,company_id=current_user.company_id).first_or_404()
    used=account_is_used(account.id)
    if request.method=="POST":
        try:
            values=account_form_values()
            duplicate=Account.query.filter(Account.company_id==current_user.company_id,Account.code==values["code"],Account.id!=account.id).first()
            if duplicate: raise ValueError("That account code already exists in this company.")
            if used and any(values[k]!=getattr(account,k) for k in ("account_type","cash_flow_classification","is_cash")):
                raise ValueError("Type, cash-flow classification, and cash/bank status cannot change after posted entries use this account.")
            for key,value in values.items(): setattr(account,key,value)
            db.session.commit(); flash("Account updated.","success"); return redirect(url_for("main.accounts"))
        except Exception as exc: db.session.rollback(); report_operation_error(exc,"The account could not be updated. Please try again.")
    return render_template("account_form.html",account=account,used=used)

@main_bp.post("/accounts/<int:account_id>/delete")
@admin_required
def delete_account(account_id):
    account=Account.query.filter_by(id=account_id,company_id=current_user.company_id).first_or_404()
    if account_is_used(account.id):
        flash("This account is used by a posted journal entry and cannot be deleted.","danger")
    elif JournalEntryLine.query.filter_by(account_id=account.id).first():
        flash("This account is referenced by a draft journal entry and cannot be deleted.","danger")
    else:
        db.session.delete(account); db.session.commit(); flash("Account deleted.","success")
    return redirect(url_for("main.accounts"))

@main_bp.get("/dashboard")
@login_required
def dashboard():
    today=date.today(); start=date(today.year,today.month,1); data=reports(current_user.company_id,start,today)
    recent=JournalEntry.query.filter_by(company_id=current_user.company_id,status="posted").order_by(JournalEntry.date.desc(),JournalEntry.id.desc()).limit(8).all()
    month_starts=[]; year=today.year; month=today.month
    for _ in range(12):
        month_starts.append(date(year,month,1)); month-=1
        if month==0: month=12; year-=1
    month_starts.reverse(); monthly={m.strftime("%Y-%m"):{"income":Decimal("0.00"),"expenses":Decimal("0.00")} for m in month_starts}
    for line,entry,account in ledger(current_user.company_id,start=month_starts[0],end=today):
        bucket=monthly.get(entry.date.strftime("%Y-%m"))
        if bucket is None: continue
        if account.account_type=="income": bucket["income"]+=line.credit-line.debit
        elif account.account_type=="expense": bucket["expenses"]+=line.debit-line.credit
    month_labels=[m.strftime("%b") for m in month_starts]
    monthly_income=[int(monthly[m.strftime("%Y-%m")]["income"]*100) for m in month_starts]
    monthly_expenses=[int(monthly[m.strftime("%Y-%m")]["expenses"]*100) for m in month_starts]
    return render_template("dashboard.html",data=data,start=start,end=today,recent=recent,
        month_labels=month_labels,monthly_income=monthly_income,monthly_expenses=monthly_expenses)

@main_bp.route("/transactions",methods=["GET","POST"])
@login_required
def transactions():
    accounts=Account.query.filter_by(company_id=current_user.company_id,active=True).order_by(Account.code).all()
    cash=[a for a in accounts if a.is_cash]; income=[a for a in accounts if a.account_type=="income"]; expense=[a for a in accounts if a.account_type=="expense"]
    asset_accounts=[a for a in accounts if a.account_type=="asset" and not a.is_cash and a.cash_flow_classification=="investing"]
    if request.method=="POST":
        try:
            kind=request.form.get("type"); amount=Decimal(request.form.get("amount","")); d=date.fromisoformat(request.form.get("date","")); desc=request.form.get("description","").strip()
            cash_id=int(request.form.get("cash_account")); category_id=int(request.form.get("category_account")); category=db.session.get(Account,category_id)
            category_options={"income":income,"expense":expense,"asset_purchase":asset_accounts}
            if kind not in category_options or amount<=0 or amount.as_tuple().exponent < -2 or category not in category_options.get(kind,[]): raise ValueError("Choose a valid type and account and a positive amount with no more than two decimal places.")
            if not any(a.id==cash_id for a in cash): raise ValueError("Choose a cash or bank account from this company.")
            lines=[(cash_id,amount,0),(category_id,0,amount)] if kind=="income" else [(category_id,amount,0),(cash_id,0,amount)]
            post_entry(current_user.company_id,current_user.id,d,desc,lines); db.session.commit(); flash("Transaction posted.","success"); return redirect(url_for("main.transactions"))
        except Exception as exc: db.session.rollback(); report_operation_error(exc,"The transaction could not be posted. Please review the values and try again.")
    entries=JournalEntry.query.filter_by(company_id=current_user.company_id).order_by(JournalEntry.date.desc(),JournalEntry.id.desc()).limit(30).all()
    return render_template("transactions.html",cash=cash,income=income,expense=expense,asset_accounts=asset_accounts,entries=entries)

@main_bp.post("/transactions/<int:entry_id>/reverse")
@login_required
def reverse_entry(entry_id):
    entry=JournalEntry.query.filter_by(id=entry_id,company_id=current_user.company_id,status="posted").first_or_404()
    if entry.kind=="reversal": flash("A reversal entry cannot itself be reversed.","danger"); return redirect(url_for("main.transactions"))
    exists=JournalEntry.query.filter_by(company_id=current_user.company_id,reversal_of_id=entry.id).first()
    if exists: flash("This entry was already reversed.","warning"); return redirect(url_for("main.transactions"))
    post_entry(current_user.company_id,current_user.id,date.today(),f"Reversal of entry #{entry.id}",[(l.account_id,l.credit,l.debit) for l in entry.lines],kind="reversal",reversal_of_id=entry.id)
    if entry.kind=="opening":
        marker=OpeningBalanceInitialization.query.filter_by(company_id=current_user.company_id,journal_entry_id=entry.id).first()
        if marker: db.session.delete(marker)
    db.session.commit(); flash("Reversal posted. The original entry remains in the ledger.","success"); return redirect(url_for("main.transactions"))

@main_bp.route("/opening",methods=["GET","POST"])
@admin_required
def opening():
    accounts=Account.query.filter(Account.company_id==current_user.company_id,Account.active.is_(True),Account.account_type.in_(("asset","liability","equity"))).order_by(Account.code).all()
    if request.method=="POST":
        try:
            d=date.fromisoformat(request.form.get("date",""))
            existing=JournalEntry.query.filter_by(company_id=current_user.company_id,date=d,kind="opening",status="posted").all()
            reversed_ids={e.reversal_of_id for e in JournalEntry.query.filter_by(company_id=current_user.company_id,kind="reversal",status="posted").all() if e.reversal_of_id}
            if any(e.id not in reversed_ids for e in existing): raise ValueError("Opening balances already exist for this date. Reverse the existing opening entry before correcting it.")
            stale=OpeningBalanceInitialization.query.filter_by(company_id=current_user.company_id,opening_date=d).first()
            if stale: db.session.delete(stale)
            account_by_id={a.id:a for a in accounts}; lines=[]
            for key,value in request.form.items():
                if not key.startswith("balance_") or not value.strip(): continue
                try: account_id=int(key.removeprefix("balance_")); amount=Decimal(value)
                except (ValueError,ArithmeticError): raise ValueError(f"Invalid opening amount for {key}.")
                account=account_by_id.get(account_id)
                if not account: raise ValueError("Every opening account must belong to this company and be a balance-sheet account.")
                if not amount.is_finite() or amount.as_tuple().exponent < -2: raise ValueError("Opening amounts must be finite and have at most two decimals.")
                if amount==0: continue
                normal_debit=account.account_type=="asset"
                debit_side=normal_debit if amount>0 else not normal_debit
                lines.append((account.id,abs(amount),0) if debit_side else (account.id,0,abs(amount)))
            total_debits=sum((Decimal(str(line[1])) for line in lines),Decimal("0.00"))
            total_credits=sum((Decimal(str(line[2])) for line in lines),Decimal("0.00"))
            if not lines: raise ValueError("Enter at least one nonzero opening balance.")
            if total_debits!=total_credits: raise ValueError(f"Opening balances must balance: debits {total_debits:.2f}, credits {total_credits:.2f}.")
            entry=post_entry(current_user.company_id,current_user.id,d,f"Opening balances as of {d}",lines,kind="opening")
            db.session.add(OpeningBalanceInitialization(company_id=current_user.company_id,opening_date=d,journal_entry_id=entry.id))
            db.session.commit(); flash("Opening balances posted; excluded from current-period income and cash flows.","success"); return redirect(url_for("main.transactions"))
        except Exception as exc: db.session.rollback(); report_operation_error(exc,"Opening balances could not be posted. Please review the values and try again.")
    existing_dates={m.opening_date for m in OpeningBalanceInitialization.query.filter_by(company_id=current_user.company_id).all()}
    return render_template("opening.html",accounts=accounts,today=date.today(),existing_dates=existing_dates)

@main_bp.route("/transfer",methods=["GET","POST"])
@login_required
def transfer():
    cash=Account.query.filter_by(company_id=current_user.company_id,is_cash=True,active=True).all()
    if request.method=="POST":
        try:
            a=int(request.form.get("from_account")); b=int(request.form.get("to_account")); amount=Decimal(request.form.get("amount","")); d=date.fromisoformat(request.form.get("date",""))
            if a==b or not any(x.id==a for x in cash) or not any(x.id==b for x in cash) or amount<=0: raise ValueError("Select two different cash/bank accounts and a positive amount.")
            post_entry(current_user.company_id,current_user.id,d,"Cash/bank transfer",[(b,amount,0),(a,0,amount)],kind="transfer"); db.session.commit(); flash("Transfer posted and excluded from cash flow activities.","success"); return redirect(url_for("main.transactions"))
        except Exception as exc: db.session.rollback(); report_operation_error(exc,"The transfer could not be posted. Please review the values and try again.")
    return render_template("form.html",title="Cash/bank transfer",action="transfer",accounts=cash)

@main_bp.route("/import",methods=["GET","POST"])
@login_required
def import_csv():
    cash_accounts=Account.query.filter_by(company_id=current_user.company_id,is_cash=True,active=True).order_by(Account.code).all()
    if request.method=="POST":
        if request.form.get("confirm"):
            batch=CsvImportBatch.query.filter_by(token=request.form.get("batch_token"),company_id=current_user.company_id,user_id=current_user.id).first()
            rows=json.loads(batch.rows_json) if batch else None
            if not rows: flash("Preview expired; upload the file again.","danger")
            else:
                try:
                    cash_id=int(request.form.get("cash_account_id",""))
                    cash=Account.query.filter_by(id=cash_id,company_id=current_user.company_id,is_cash=True,active=True).first()
                    if not cash: raise ValueError("Choose an active Cash/Bank account belonging to this company.")
                    for r in rows:
                        r["date"]=date.fromisoformat(r["date"])
                        r["amount"]=Decimal(r["amount"])
                        acct=Account.query.filter_by(company_id=current_user.company_id,code=r["account_code"],active=True).first()
                        expected={"income":"income","expense":"expense","asset_purchase":"asset"}[r["type"]]
                        eligible=acct and acct.account_type==expected
                        if r["type"]=="asset_purchase": eligible=eligible and not acct.is_cash and acct.cash_flow_classification=="investing"
                        if not eligible:
                            expected_label="an eligible asset account" if r["type"]=="asset_purchase" else f"a {expected} account"
                            raise ValueError(f"CSV row {r['row']}: account code {r['account_code']} must identify {expected_label}.")
                        key=csv_source_key(current_user.company_id,r)
                        if JournalEntry.query.filter_by(company_id=current_user.company_id,source_key=key).first(): raise ValueError(f"CSV row {r['row']} is a duplicate already imported.")
                        lines=[(cash.id,r["amount"],0),(acct.id,0,r["amount"])] if expected=="income" else [(acct.id,r["amount"],0),(cash.id,0,r["amount"])]
                        post_entry(current_user.company_id,current_user.id,r["date"],r["description"],lines,source_key=key)
                    db.session.delete(batch); db.session.commit(); flash(f"Imported {len(rows)} transactions atomically.","success")
                except Exception as exc:
                    db.session.rollback(); report_operation_error(exc,"The import could not be completed. No transactions were imported.")
                    if batch and rows:
                        return render_template("import.html",rows=rows,invalid=[],batch_token=batch.token,cash_accounts=cash_accounts)
        else:
            file=request.files.get("file")
            if not file: flash("Choose a CSV file.","danger")
            else:
                try: rows,invalid=parse_csv(file.stream.read().decode("utf-8-sig"))
                except UnicodeDecodeError: return render_template("import.html",rows=None,invalid=[{"row":1,"error":"file must be UTF-8 encoded"}],cash_accounts=cash_accounts)
                except ValueError as exc: return render_template("import.html",rows=None,invalid=[{"row":1,"error":str(exc)}],cash_accounts=cash_accounts)
                # Report duplicate rows inside the upload and existing imports.
                seen=set()
                for r in rows:
                    k=(r["date"],r["type"],r["amount"],r["description"],r["account_code"])
                    if k in seen: invalid.append({"row":r["row"],"error":"duplicate row in upload"})
                    seen.add(k)
                    key=csv_source_key(current_user.company_id,r)
                    if JournalEntry.query.filter_by(company_id=current_user.company_id,source_key=key).first(): invalid.append({"row":r["row"],"error":"already imported"})
                    expected={"income":"income","expense":"expense","asset_purchase":"asset"}[r["type"]]
                    acct=Account.query.filter_by(company_id=current_user.company_id,code=r["account_code"],active=True).first()
                    eligible=acct and acct.account_type==expected
                    if r["type"]=="asset_purchase": eligible=eligible and not acct.is_cash and acct.cash_flow_classification=="investing"
                    if not eligible:
                        expected_label="an eligible asset account" if r["type"]=="asset_purchase" else f"a {expected} account"
                        invalid.append({"row":r["row"],"error":f"account_code must identify {expected_label} in this company"})
                token=None
                if invalid:
                    CsvImportBatch.query.filter_by(user_id=current_user.id).delete(); db.session.commit()
                else:
                    token=__import__("secrets").token_urlsafe(32)
                    stored=[{**r,"date":r["date"].isoformat(),"amount":str(r["amount"])} for r in rows]
                    CsvImportBatch.query.filter_by(user_id=current_user.id).delete()
                    db.session.add(CsvImportBatch(token=token,company_id=current_user.company_id,user_id=current_user.id,rows_json=json.dumps(stored)))
                    db.session.commit()
                return render_template("import.html",rows=rows if not invalid else None,invalid=invalid,batch_token=token,cash_accounts=cash_accounts)
    return render_template("import.html",rows=None,invalid=[],cash_accounts=cash_accounts)

@main_bp.get("/csv-template")
@login_required
def csv_template():
    return Response("date,type,amount,description,account_code\n2026-01-31,income,1250.00,Consulting services,4000\n2026-01-31,asset_purchase,2400.00,Office equipment,1500\n",mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=transactions_template.csv"})

def get_report_dates():
    try: end=date.fromisoformat(request.args.get("end",date.today().isoformat())); start=date.fromisoformat(request.args.get("start",end.replace(day=1).isoformat()))
    except ValueError: abort(400,"Dates must use YYYY-MM-DD.")
    if start>end: abort(400,"Start date must be on or before end date.")
    return start,end

@main_bp.get("/reports")
@login_required
def report_page():
    start,end=get_report_dates(); data=reports(current_user.company_id,start,end)
    return render_template("reports.html",data=data,start=start,end=end)

@main_bp.get("/export/<kind>/<format>")
@login_required
def export(kind,format):
    if kind not in ("income","balance","cashflow","all") or format not in ("pdf","xlsx"): abort(404)
    start,end=get_report_dates(); data=reports(current_user.company_id,start,end)
    from app.services.exports import make_pdf,make_xlsx
    payload=make_pdf(kind,data,current_user.company.name,start,end) if format=="pdf" else make_xlsx(kind,data,current_user.company.name,start,end)
    mime="application/pdf" if format=="pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(payload,mimetype=mime,headers={"Content-Disposition":f"attachment; filename={kind}_{start}_{end}.{format}"})
