"""Persistent models for company-scoped, simplified double-entry accounting."""
from datetime import datetime, date
from decimal import Decimal
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash
from app.extensions import db
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session


class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="INR")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    users = db.relationship("User", backref="company", cascade="all, delete-orphan")
    accounts = db.relationship("Account", backref="company", cascade="all, delete-orphan")
    entries = db.relationship("JournalEntry", backref="company", cascade="all, delete-orphan")


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    email = db.Column(db.String(254), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="accountant")
    active = db.Column(db.Boolean, nullable=False, default=True)
    __table_args__ = (db.UniqueConstraint("email", name="uq_user_email"),)
    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)
    @property
    def is_admin(self): return self.role == "administrator"


class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    code = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    account_type = db.Column(db.String(20), nullable=False)  # asset/liability/equity/income/expense
    cash_flow_classification = db.Column(db.String(20))  # operating/investing/financing
    is_cash = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    __table_args__ = (db.UniqueConstraint("company_id", "code"),)


class JournalEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    description = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(12), nullable=False, default="draft")
    kind = db.Column(db.String(20), nullable=False, default="normal")  # normal/opening/transfer/reversal
    source_key = db.Column(db.String(64), index=True)
    reversal_of_id = db.Column(db.Integer, db.ForeignKey("journal_entry.id"), unique=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    lines = db.relationship("JournalEntryLine", backref="entry", cascade="all, delete-orphan", order_by="JournalEntryLine.id")
    created_by = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("company_id", "source_key", name="uq_journal_source"),)


class JournalEntryLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    journal_entry_id = db.Column(db.Integer, db.ForeignKey("journal_entry.id"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    debit = db.Column(db.Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    credit = db.Column(db.Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    account = db.relationship("Account")


class CsvImportBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), nullable=False, unique=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    rows_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class OpeningBalanceInitialization(db.Model):
    """Tracks one active opening-balance journal per company and opening date."""
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True)
    opening_date = db.Column(db.Date, nullable=False)
    journal_entry_id = db.Column(db.Integer, db.ForeignKey("journal_entry.id"), nullable=False, unique=True)
    __table_args__ = (db.UniqueConstraint("company_id", "opening_date", name="uq_opening_company_date"),)


@event.listens_for(Session, "before_flush")
def protect_posted_entries(session, flush_context, instances):
    """Disallow changing posted records; corrections must use a reversal entry."""
    candidates=[o for o in set(session.new).union(session.dirty) if isinstance(o,JournalEntry) and o.status=="posted"]
    for entry in candidates:
        if entry.kind not in ("normal","opening","transfer","reversal"):
            raise ValueError("Unknown posted journal entry type.")
        if len(entry.lines)<2 or sum((line.debit for line in entry.lines),Decimal("0.00"))!=sum((line.credit for line in entry.lines),Decimal("0.00")):
            raise ValueError("A posted journal entry must have at least two balanced lines.")
        for line in entry.lines:
            if line.debit<0 or line.credit<0 or (line.debit>0 and line.credit>0) or (line.debit==0 and line.credit==0) or line.debit.as_tuple().exponent < -2 or line.credit.as_tuple().exponent < -2:
                raise ValueError("Posted journal lines must have one positive amount with no more than two decimals.")
            if line.account.company_id!=entry.company_id:
                raise ValueError("Every journal account must belong to the journal company.")
        if entry.kind=="opening" and any(line.account.account_type in ("income","expense") for line in entry.lines):
            raise ValueError("Opening balances may only use balance sheet accounts.")
        if entry.kind=="transfer" and any(not line.account.is_cash for line in entry.lines):
            raise ValueError("Cash transfers may only use cash or bank accounts.")
        pnl_lines=[line for line in entry.lines if line.account.account_type in ("income","expense")]
        if entry.kind=="normal" and pnl_lines:
            if not any(line.account.is_cash for line in entry.lines):
                raise ValueError("Cash-basis income and expenses must post to a cash or bank account.")
            for line in pnl_lines:
                expected="credit" if line.account.account_type=="income" else "debit"
                if getattr(line,expected)<=0 or getattr(line,"debit" if expected=="credit" else "credit")!=0:
                    raise ValueError(f"{line.account.account_type.title()} accounts must use their normal posting side.")
        if entry.created_by.company_id!=entry.company_id:
            raise ValueError("The posting user must belong to the journal company.")
        if entry.kind=="reversal":
            original=session.get(JournalEntry,entry.reversal_of_id) if entry.reversal_of_id else None
            if not original or original.status!="posted" or original.company_id!=entry.company_id:
                raise ValueError("A reversal must reference a posted entry from the same company.")
            original_totals={}
            for line in original.lines:
                account_id=line.account_id or line.account.id
                debit,credit=original_totals.get(account_id,(Decimal("0.00"),Decimal("0.00")))
                original_totals[account_id]=(debit+line.debit,credit+line.credit)
            reversal_totals={}
            for line in entry.lines:
                account_id=line.account_id or line.account.id
                debit,credit=reversal_totals.get(account_id,(Decimal("0.00"),Decimal("0.00")))
                reversal_totals[account_id]=(debit+line.debit,credit+line.credit)
            if reversal_totals!={aid:(credit,debit) for aid,(debit,credit) in original_totals.items()}:
                raise ValueError("A reversal must exactly reverse the original journal lines.")
    for obj in session.dirty:
        if isinstance(obj, JournalEntry) and inspect(obj).persistent and obj.status == "posted":
            state=inspect(obj)
            status_history=state.attrs.status.history
            legitimate_post=bool(status_history.deleted and status_history.deleted[0]=="draft" and status_history.added and status_history.added[0]=="posted")
            changed=("date", "description", "kind", "company_id") if legitimate_post else ("date", "description", "status", "kind", "company_id")
            if any(state.attrs[key].history.has_changes() for key in changed):
                raise ValueError("Posted journal entries are immutable; create a reversal entry.")
        if isinstance(obj, JournalEntryLine) and inspect(obj).persistent and obj.entry and obj.entry.status == "posted":
            if any(inspect(obj).attrs[key].history.has_changes() for key in ("account_id", "debit", "credit", "journal_entry_id")):
                raise ValueError("Lines in posted journal entries are immutable; create a reversal entry.")
    for obj in session.deleted:
        if isinstance(obj, JournalEntry) and obj.status == "posted":
            raise ValueError("Posted journal entries cannot be deleted; create a reversal entry.")
        if isinstance(obj, JournalEntryLine) and obj.entry and obj.entry.status == "posted":
            raise ValueError("Lines in posted journal entries cannot be deleted; create a reversal entry.")
    for obj in session.new:
        if isinstance(obj,JournalEntryLine) and obj.entry and inspect(obj.entry).persistent and obj.entry.status=="posted":
            history=inspect(obj.entry).attrs.status.history
            legitimate_post=bool(history.deleted and history.deleted[0]=="draft" and history.added and history.added[0]=="posted")
            if not legitimate_post:
                raise ValueError("New lines cannot be added to posted journal entries; create a reversal entry.")
