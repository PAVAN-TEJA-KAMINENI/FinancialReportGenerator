from datetime import date
from decimal import Decimal
import io
import os
import re
import zipfile
from openpyxl import load_workbook
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app import create_app
from app.extensions import db
from app.models import Account, JournalEntry, JournalEntryLine, User, Company, OpeningBalanceInitialization
from app.services import post_entry, reports, create_draft, post_draft
from app.config import ProductionConfig

@pytest.fixture
def app():
    config_name = "postgresql_testing" if os.environ.get("FRG_TEST_POSTGRES") == "1" else "testing"
    app=create_app(config_name); app.config["WTF_CSRF_ENABLED"]=False
    with app.app_context(): db.create_all()
    yield app
    with app.app_context(): db.session.remove(); db.drop_all()

@pytest.fixture
def client(app): return app.test_client()

def register(client,email="admin@example.com",company="Northwind"):
    return client.post("/register",data={"company":company,"name":"Admin","currency":"INR","email":email,"password":"long-password-123","confirm_password":"long-password-123"},follow_redirects=True)

def test_registration_form_and_success_create_first_admin_and_chart(app):
    client=app.test_client(); page=client.get("/register")
    for field in (b'name="email"',b'name="password"',b'name="confirm_password"'):
        assert field in page.data
    response=register(client,"owner@example.com","Northwind Ltd")
    assert response.status_code==200 and b"Northwind Ltd" in response.data
    with app.app_context():
        company=Company.query.one(); user=User.query.one()
        assert user.company_id==company.id and user.role=="administrator"
        assert user.password_hash!="long-password-123" and user.check_password("long-password-123")
        assert Account.query.filter_by(company_id=company.id).count()==11

@pytest.mark.parametrize("overrides,message",[
    ({"password":"","confirm_password":""},"Enter a password"),
    ({"password":"short","confirm_password":"short"},"at least 10 characters"),
    ({"password":"long-password-123","confirm_password":"different-password"},"do not match"),
])
def test_registration_rejects_missing_short_or_mismatched_password(app,overrides,message):
    client=app.test_client(); data={"company":"Rejected Co","name":"Admin","email":"rejected@example.com","currency":"INR","password":"long-password-123","confirm_password":"long-password-123"}; data.update(overrides)
    response=client.post("/register",data=data,follow_redirects=True)
    assert message.encode() in response.data
    with app.app_context(): assert Company.query.count()==0 and User.query.count()==0

def test_registration_rejects_duplicate_email_and_company(app):
    client=app.test_client(); register(client,"owner@example.com","Northwind Ltd")
    duplicate_email=app.test_client().post("/register",data={"company":"Other Co","name":"Other","email":"owner@example.com","currency":"INR","password":"long-password-123","confirm_password":"long-password-123"},follow_redirects=True)
    assert b"email is already registered" in duplicate_email.data
    duplicate_company=app.test_client().post("/register",data={"company":"northwind ltd","name":"Other","email":"other@example.com","currency":"INR","password":"long-password-123","confirm_password":"long-password-123"},follow_redirects=True)
    assert b"company with that name is already registered" in duplicate_company.data
    with app.app_context(): assert Company.query.count()==1 and User.query.count()==1

def test_registration_rolls_back_company_and_user_if_chart_setup_fails(app,monkeypatch):
    import app.blueprints.main as main_routes
    def fail_seed(company): raise RuntimeError("simulated chart failure")
    monkeypatch.setattr(main_routes,"seed_accounts",fail_seed)
    client=app.test_client(); response=client.post("/register",data={"company":"Atomic Co","name":"Owner","email":"atomic@example.com","currency":"INR","password":"long-password-123","confirm_password":"long-password-123"},follow_redirects=True)
    assert b"No company or user records were saved" in response.data
    with app.app_context(): assert Company.query.count()==0 and User.query.count()==0

def test_postgresql_explicit_ids_require_sequence_reset(app):
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            pytest.skip("PostgreSQL sequence behavior only")
        db.session.add(Company(id=1,name="Imported company",currency="INR")); db.session.commit()
        db.session.add(Company(name="Next company",currency="INR"))
        with pytest.raises(IntegrityError): db.session.flush()
        db.session.rollback()

        db.session.execute(text("SELECT setval(pg_get_serial_sequence('company','id'), (SELECT MAX(id) FROM company), true)"))
        db.session.commit()
        next_company=Company(name="Next company",currency="INR")
        db.session.add(next_company); db.session.commit()
        assert next_company.id == 2

def test_registration_roles_and_company_isolation(app):
    c1=app.test_client(); assert register(c1).status_code==200
    c1.post("/transactions",data={"type":"income","date":"2026-01-02","amount":"12.00","cash_account":"1","category_account":"8","description":"Private sale"})
    with app.app_context():
        admin=User.query.filter_by(email="admin@example.com").one(); assert admin.role=="administrator"
        secret=Account(company_id=admin.company_id,code="4999",name="Private Account",account_type="income",cash_flow_classification="operating"); db.session.add(secret); db.session.flush(); secret_id=secret.id
        post_entry(admin.company_id,admin.id,date(2026,1,3),"Secret revenue",[(Account.query.filter_by(company_id=admin.company_id,code="1000").one().id,Decimal("77"),0),(secret.id,0,Decimal("77"))]); db.session.commit()
    c2=app.test_client(); register(c2,"other@example.com","Contoso")
    with app.app_context(): assert Company_count()==2
    assert b"Northwind" in c1.get("/dashboard").data
    assert b"Private sale" not in c2.get("/transactions").data
    assert b"Private Account" not in c2.get("/transactions").data
    assert c2.get(f"/accounts/{secret_id}/edit").status_code==404
    assert b"Private Account" not in c2.get("/reports?start=2026-01-01&end=2026-01-31").data
    foreign_book=load_workbook(io.BytesIO(c2.get("/export/all/xlsx?start=2026-01-01&end=2026-01-31").data),data_only=True)
    assert all(cell.value!="Private Account" for sheet in foreign_book for row in sheet.iter_rows() for cell in row)
    assert b"Private Account" not in c2.get("/export/all/pdf?start=2026-01-01&end=2026-01-31").data
    assert c2.get("/team").status_code==200

def Company_count():
    from app.models import Company
    return Company.query.count()

def test_posted_ledger_statements_and_immutability(app,client):
    register(client)
    with app.app_context():
        admin=User.query.one(); accounts={a.code:a for a in Account.query.all()}
        post_entry(admin.company_id,admin.id,date(2026,4,1),"Opening",[(accounts["1000"].id,Decimal("500"),0),(accounts["3000"].id,0,Decimal("500"))],kind="opening")
        post_entry(admin.company_id,admin.id,date(2026,4,10),"Sale",[(accounts["1000"].id,Decimal("1000"),0),(accounts["4000"].id,0,Decimal("1000"))])
        post_entry(admin.company_id,admin.id,date(2026,4,11),"Rent",[(accounts["5000"].id,Decimal("100"),0),(accounts["1000"].id,0,Decimal("100"))])
        db.session.commit(); d=reports(admin.company_id,date(2026,4,1),date(2026,4,30))
        assert d["net"]==Decimal("900.00") and d["asset_total"]==Decimal("1400.00")
        assert d["balanced"] and d["equity_total"]==Decimal("1400.00")
        assert d["cash_flows"]["operating"]==Decimal("900.00")
        assert d["cash_start"]+d["cash_change"]==d["cash_end"]==Decimal("1400.00")
        sale=JournalEntry.query.filter_by(description="Sale").one()
        sale_lines={line.account.code:(line.debit,line.credit) for line in sale.lines}
        assert sale_lines=={"1000":(Decimal("1000.00"),Decimal("0.00")),"4000":(Decimal("0.00"),Decimal("1000.00"))}
        rent=JournalEntry.query.filter_by(description="Rent").one()
        rent_lines={line.account.code:(line.debit,line.credit) for line in rent.lines}
        assert rent_lines=={"5000":(Decimal("100.00"),Decimal("0.00")),"1000":(Decimal("0.00"),Decimal("100.00"))}
        for entry in JournalEntry.query.filter_by(status="posted").all():
            assert sum((line.debit for line in entry.lines),Decimal("0.00"))==sum((line.credit for line in entry.lines),Decimal("0.00"))
        entry=JournalEntry.query.filter_by(description="Sale").one(); line=entry.lines[0]; line.debit=Decimal("999")
        with pytest.raises(ValueError): db.session.flush()
        db.session.rollback()

def test_manual_transaction_reports_exports_and_csv(client):
    register(client)
    post=client.post("/transactions",data={"type":"income","date":"2026-04-10","amount":"450.25","cash_account":"1","category_account":"8","description":"Consulting"},follow_redirects=True)
    assert post.status_code==200
    # Use actual seed account IDs through a CSV upload, validating and then posting atomically.
    preview=client.post("/import",data={"file":(io.BytesIO(b"date,type,amount,description,account_code\n2026-04-12,expense,25.00,Cloud service,5000\n"),"input.csv")},content_type="multipart/form-data")
    assert preview.status_code==200 and b"Cloud service" in preview.data
    token=re.search(rb'name="batch_token" value="([^"]+)"',preview.data).group(1).decode()
    result=client.post("/import",data={"confirm":"1","batch_token":token,"cash_account_id":"1"},follow_redirects=True); assert b"Imported 1" in result.data
    assert client.get("/reports?start=2026-04-01&end=2026-04-30").status_code==200
    for path,signature in (("/export/all/pdf","%PDF"),("/export/all/xlsx","PK")):
        response=client.get(path+"?start=2026-04-01&end=2026-04-30")
        assert response.status_code==200 and response.data.startswith(signature.encode())
    workbook=load_workbook(io.BytesIO(client.get("/export/all/xlsx?start=2026-04-01&end=2026-04-30").data),data_only=True)
    assert set(workbook.sheetnames)=={"Income Statement","Balance Sheet","Cash Flow"}
    income=workbook["Income Statement"]
    figures={row[0].value:row[1].value for row in income.iter_rows(min_row=5)}
    assert figures["Net profit / (loss)"]==425.25
    assert figures["Sales Income"]==450.25 and figures["Operating Expenses"]==-25
    balance=workbook["Balance Sheet"]
    balance_figures={row[0].value:row[1].value for row in balance.iter_rows(min_row=5)}
    assert balance_figures["Total assets"]==425.25
    assert balance_figures["Current period earnings"]==425.25 and balance_figures["Total liabilities and equity"]==425.25
    cash=workbook["Cash Flow"]
    cash_figures={row[0].value:row[1].value for row in cash.iter_rows(min_row=5)}
    assert cash_figures["Closing cash and bank"]==425.25
    assert cash_figures["Operating activities"]==425.25
    for kind in ("income","balance","cashflow"):
        for fmt in ("pdf","xlsx"):
            assert client.get(f"/export/{kind}/{fmt}?start=2026-04-01&end=2026-04-30").status_code==200
    pdf=client.get("/export/income/pdf?start=2026-04-01&end=2026-04-30").data
    assert b"425.25" in pdf and b"Net profit" in pdf
    template=client.get("/csv-template"); assert b"account_code" in template.data

def test_manual_asset_purchase_capitalizes_equipment_and_reconciles_cashflow(app,client):
    register(client)
    with app.app_context():
        admin=User.query.one(); cash=Account.query.filter_by(company_id=admin.company_id,code="1010").one(); equity=Account.query.filter_by(company_id=admin.company_id,code="3000").one()
        post_entry(admin.company_id,admin.id,date(2026,4,1),"Opening bank",[(cash.id,5000,0),(equity.id,0,5000)],kind="opening"); db.session.commit()
    page=client.get("/transactions")
    assert b'option value="asset_purchase">Asset Purchase</option>' in page.data and b"1500" in page.data
    response=client.post("/transactions",data={"type":"asset_purchase","date":"2026-04-10","amount":"2400.00","cash_account":"2","category_account":"4","description":"Laptop equipment"},follow_redirects=True)
    assert b"Transaction posted" in response.data
    with app.app_context():
        company=User.query.one().company_id; entry=JournalEntry.query.filter_by(description="Laptop equipment").one()
        lines={line.account.code:(line.debit,line.credit) for line in entry.lines}
        assert lines=={"1500":(Decimal("2400.00"),Decimal("0.00")),"1010":(Decimal("0.00"),Decimal("2400.00"))}
        data=reports(company,date(2026,4,1),date(2026,4,30))
        asset_balances={account.code:value for account,value in data["assets"]}
        assert data["net"]==Decimal("0.00") and asset_balances["1500"]==Decimal("2400.00")
        assert asset_balances["1010"]==Decimal("2600.00")
        assert data["cash_flows"]["investing"]==Decimal("-2400.00")
        assert data["asset_total"]==data["liability_total"]+data["equity_total"]==Decimal("5000.00")
        assert data["cash_start"]+data["cash_change"]==data["cash_end"]==Decimal("2600.00")

def test_csv_invalid_preview_does_not_post(client,app):
    register(client)
    response=client.post("/import",data={"file":(io.BytesIO(b"date,type,amount,description,account_code\n2026-01-01,expense,2.00,Oops,4000\n"),"bad.csv")},content_type="multipart/form-data")
    assert b"must identify a expense account" in response.data
    with app.app_context(): assert JournalEntry.query.count()==0

def test_csv_asset_purchase_uses_selected_bank_and_asset_journal(app,client):
    register(client)
    with app.app_context():
        admin=User.query.one(); bank=Account.query.filter_by(company_id=admin.company_id,code="1010").one(); equity=Account.query.filter_by(company_id=admin.company_id,code="3000").one()
        post_entry(admin.company_id,admin.id,date(2026,4,1),"Opening bank",[(bank.id,5000,0),(equity.id,0,5000)],kind="opening"); db.session.commit(); bank_id=bank.id; company_id=admin.company_id
    source=b"date,type,amount,description,account_code\n2026-04-10,asset_purchase,2400.00,Laptop equipment,1500\n"
    preview=client.post("/import",data={"file":(io.BytesIO(source),"asset.csv")},content_type="multipart/form-data")
    assert b"Laptop equipment" in preview.data and b"asset_purchase" in preview.data
    token=re.search(rb'name="batch_token" value="([^"]+)"',preview.data).group(1).decode()
    imported=client.post("/import",data={"confirm":"1","batch_token":token,"cash_account_id":str(bank_id)},follow_redirects=True)
    assert b"Imported 1" in imported.data
    with app.app_context():
        entry=JournalEntry.query.filter_by(company_id=company_id,description="Laptop equipment").one()
        lines={line.account.code:(line.debit,line.credit) for line in entry.lines}
        assert lines=={"1500":(Decimal("2400.00"),Decimal("0.00")),"1010":(Decimal("0.00"),Decimal("2400.00"))}
        data=reports(company_id,date(2026,4,1),date(2026,4,30)); assets={a.code:v for a,v in data["assets"]}
        assert data["net"]==Decimal("0.00") and assets["1500"]==Decimal("2400.00")
        assert data["cash_flows"]["investing"]==Decimal("-2400.00")
        assert data["balanced"] and data["cash_start"]+data["cash_change"]==data["cash_end"]==Decimal("2600.00")

@pytest.mark.parametrize("account_code",["5100","1000"])
def test_csv_asset_purchase_rejects_noneligible_account_codes(client,app,account_code):
    register(client)
    source=f"date,type,amount,description,account_code\n2026-04-10,asset_purchase,100.00,Wrong account,{account_code}\n".encode()
    response=client.post("/import",data={"file":(io.BytesIO(source),"wrong-asset.csv")},content_type="multipart/form-data")
    assert b"eligible asset account" in response.data
    with app.app_context(): assert JournalEntry.query.count()==0

@pytest.mark.parametrize("row,error",[
    ("not-a-date,income,2.00,Oops,4000","Invalid isoformat"),
    ("2026-01-01,income,NaN,Oops,4000","finite"),
    ("2026-01-01,income,Infinity,Oops,4000","finite"),
    ("2026-01-01,income,-2.00,Oops,4000","positive"),
    ("2026-01-01,income,2.001,Oops,4000","two decimals"),
    ("2026-01-01,income,2.00,Oops,missing","account_code"),
])
def test_csv_validation_reports_invalid_dates_amounts_and_accounts(client,row,error):
    register(client)
    payload=("date,type,amount,description,account_code\n"+row+"\n").encode()
    response=client.post("/import",data={"file":(io.BytesIO(payload),"invalid.csv")},content_type="multipart/form-data")
    assert response.status_code==200 and error.encode().lower() in response.data.lower()

def test_csv_duplicate_rows_and_reimports_are_rejected(client,app):
    register(client)
    content=b"date,type,amount,description,account_code\n2026-01-01,income,12.00,Same,4000\n2026-01-01,income,12.00,Same,4000\n"
    duplicated=client.post("/import",data={"file":(io.BytesIO(content),"dupe.csv")},content_type="multipart/form-data")
    assert b"duplicate row in upload" in duplicated.data
    with app.app_context(): assert JournalEntry.query.count()==0
    one=b"date,type,amount,description,account_code\n2026-01-01,income,12.00,Same,4000\n"
    preview=client.post("/import",data={"file":(io.BytesIO(one),"one.csv")},content_type="multipart/form-data")
    token=re.search(rb'name="batch_token" value="([^"]+)"',preview.data).group(1).decode()
    client.post("/import",data={"confirm":"1","batch_token":token,"cash_account_id":"1"})
    repeated=client.post("/import",data={"file":(io.BytesIO(one),"one.csv")},content_type="multipart/form-data")
    assert b"already imported" in repeated.data

def test_csv_import_rolls_back_all_rows_if_state_changes_after_preview(client,app):
    register(client)
    body=b"date,type,amount,description,account_code\n2026-04-01,income,10.00,First,4000\n2026-04-02,expense,5.00,Second,5000\n"
    preview=client.post("/import",data={"file":(io.BytesIO(body),"batch.csv")},content_type="multipart/form-data")
    token=re.search(rb'name="batch_token" value="([^"]+)"',preview.data).group(1).decode()
    with app.app_context(): Account.query.filter_by(code="5000").one().active=False; db.session.commit()
    response=client.post("/import",data={"confirm":"1","batch_token":token,"cash_account_id":"1"},follow_redirects=True)
    assert b"must identify a expense account" in response.data
    with app.app_context(): assert JournalEntry.query.count()==0

def test_drafts_are_excluded_and_unbalanced_draft_cannot_post(app,client):
    register(client)
    with app.app_context():
        admin=User.query.one(); accounts={a.code:a for a in Account.query.all()}
        draft=create_draft(admin.company_id,admin.id,date(2026,4,10),"Unfinished",[(accounts["1000"].id,100,0),(accounts["4000"].id,0,90)])
        db.session.commit()
        assert reports(admin.company_id,date(2026,4,1),date(2026,4,30))["cash_end"]==Decimal("0.00")
        with pytest.raises(ValueError): post_draft(draft)
        db.session.rollback()
        balanced=create_draft(admin.company_id,admin.id,date(2026,4,11),"Ready",[(accounts["1000"].id,20,0),(accounts["4000"].id,0,20)])
        db.session.flush(); post_draft(balanced); db.session.commit()
        assert reports(admin.company_id,date(2026,4,1),date(2026,4,30))["cash_end"]==Decimal("20.00")

def test_posting_rejects_unbalanced_and_subcent_entries(app,client):
    register(client)
    with app.app_context():
        admin=User.query.one(); cash=Account.query.filter_by(code="1000").one(); income=Account.query.filter_by(code="4000").one()
        with pytest.raises(ValueError): post_entry(admin.company_id,admin.id,date.today(),"Bad",[(cash.id,10,0),(income.id,0,9)])
        with pytest.raises(ValueError): post_entry(admin.company_id,admin.id,date.today(),"Bad precision",[(cash.id,Decimal("1.001"),0),(income.id,0,Decimal("1.001"))])
        with pytest.raises(ValueError,match="balance sheet accounts"):
            post_entry(admin.company_id,admin.id,date.today(),"Opening revenue",[(cash.id,10,0),(income.id,0,10)],kind="opening")
        db.session.rollback()
        receivable=Account.query.filter_by(code="1100").one()
        with pytest.raises(ValueError,match="cash or bank"):
            post_entry(admin.company_id,admin.id,date.today(),"Accrual revenue",[(receivable.id,10,0),(income.id,0,10)])

def test_reversal_is_posted_as_a_new_entry(client,app):
    register(client)
    client.post("/transactions",data={"type":"income","date":"2026-04-10","amount":"40.00","cash_account":"1","category_account":"8","description":"Wrong sale"})
    with app.app_context(): entry=JournalEntry.query.filter_by(description="Wrong sale").one(); entry_id=entry.id
    response=client.post(f"/transactions/{entry_id}/reverse",follow_redirects=True)
    assert response.status_code==200 and b"Reversal posted" in response.data
    with app.app_context():
        entries=JournalEntry.query.order_by(JournalEntry.id).all()
        assert entries[0].status==entries[1].status=="posted" and entries[1].kind=="reversal"
        assert entries[0].lines[0].debit==entries[1].lines[0].credit
        d=reports(entries[0].company_id,date(2026,4,1),date.today())
        assert d["net"]==Decimal("0.00") and d["cash_end"]==Decimal("0.00") and d["balanced"]

def test_later_opening_balance_is_not_a_cash_receipt(app,client):
    register(client)
    with app.app_context():
        admin=User.query.one(); accounts={a.code:a for a in Account.query.all()}
        post_entry(admin.company_id,admin.id,date(2026,5,1),"Opening",[(accounts["1000"].id,100,0),(accounts["3000"].id,0,100)],kind="opening")
        # Add the second opening entry via the valid ledger path on a later reporting day.
        post_entry(admin.company_id,admin.id,date(2026,5,10),"Opening adjustment",[(accounts["1000"].id,50,0),(accounts["3000"].id,0,50)],kind="opening")
        post_entry(admin.company_id,admin.id,date(2026,5,11),"Sale",[(accounts["1000"].id,10,0),(accounts["4000"].id,0,10)])
        db.session.commit(); d=reports(admin.company_id,date(2026,5,1),date(2026,5,31))
        assert d["cash_start"]==Decimal("150.00") and d["cash_change"]==Decimal("10.00") and d["cash_end"]==Decimal("160.00")

def test_reversing_opening_balance_does_not_create_period_cash_flow(app,client):
    register(client)
    with app.app_context():
        admin=User.query.one(); a={x.code:x for x in Account.query.all()}
        original=post_entry(admin.company_id,admin.id,date(2026,7,1),"Opening",[(a["1000"].id,600,0),(a["3000"].id,0,600)],kind="opening")
        db.session.commit(); entry_id=original.id
    result=client.post(f"/transactions/{entry_id}/reverse",follow_redirects=True)
    assert b"Reversal posted" in result.data
    with app.app_context():
        end=date.today(); data=reports(User.query.one().company_id,date(2026,7,1),end)
        assert data["cash_start"]==Decimal("0.00") and data["cash_change"]==Decimal("0.00") and data["cash_end"]==Decimal("0.00")

def test_cashflow_classifies_investing_financing_and_excludes_transfers_and_noncash(app,client):
    register(client)
    with app.app_context():
        admin=User.query.one(); a={x.code:x for x in Account.query.all()}; company=admin.company_id
        post_entry(company,admin.id,date(2026,6,1),"Opening",[(a["1000"].id,500,0),(a["3000"].id,0,500)],kind="opening")
        post_entry(company,admin.id,date(2026,6,2),"Equipment purchase",[(a["1500"].id,100,0),(a["1000"].id,0,100)])
        post_entry(company,admin.id,date(2026,6,3),"Loan proceeds",[(a["1000"].id,50,0),(a["2100"].id,0,50)])
        post_entry(company,admin.id,date(2026,6,4),"Move to bank",[(a["1010"].id,200,0),(a["1000"].id,0,200)],kind="transfer")
        post_entry(company,admin.id,date(2026,6,5),"Equipment bought on credit",[(a["1500"].id,25,0),(a["2000"].id,0,25)])
        db.session.commit(); d=reports(company,date(2026,6,1),date(2026,6,30))
        assert d["cash_flows"]=={"investing":Decimal("-100.00"),"financing":Decimal("50.00")}
        assert d["cash_start"]==Decimal("500.00") and d["cash_change"]==Decimal("-50.00")
        assert d["cash_end"]==Decimal("450.00") and d["cash_start"]+d["cash_change"]==d["cash_end"]
        assert d["balanced"] and d["asset_total"]==Decimal("575.00")
        assert d["net"]==Decimal("0.00")

def test_csrf_rejects_unprotected_post():
    app=create_app("testing"); client=app.test_client()
    assert client.post("/login",data={"email":"x@example.com","password":"irrelevant"}).status_code==400

def test_login_rejects_external_next_redirect(app,client):
    register(client); client.post("/logout")
    response=client.post("/login?next=https%3A%2F%2Fevil.example",data={"email":"admin@example.com","password":"long-password-123"})
    assert response.status_code==400

def test_auth_required_and_accountant_denied_team(app):
    anon=app.test_client()
    for path in ("/dashboard","/transactions","/reports","/export/all/xlsx"):
        assert anon.get(path).status_code==302
    register(anon)
    with app.app_context():
        admin=User.query.filter_by(email="admin@example.com").one(); assert admin.password_hash!="long-password-123" and admin.check_password("long-password-123")
        company=admin.company
        acct=User(company_id=company.id,email="acct@example.com",name="Bookkeeper",role="accountant"); acct.set_password("another-long-password")
        db.session.add(acct); db.session.commit()
    anon.post("/logout")
    assert anon.post("/login",data={"email":"acct@example.com","password":"incorrect-password"}).status_code==200
    anon.post("/login",data={"email":"acct@example.com","password":"another-long-password"})
    assert anon.get("/team").status_code==403
    assert anon.get("/accounts").status_code==403 and anon.get("/opening").status_code==403

def test_production_config_rejects_default_or_example_secret(monkeypatch):
    monkeypatch.setattr(ProductionConfig,"SECRET_KEY","dev-only-not-for-production",raising=False)
    monkeypatch.setattr(ProductionConfig,"SQLALCHEMY_DATABASE_URI","sqlite:///:memory:",raising=False)
    with pytest.raises(ValueError,match="SECRET_KEY"): create_app("production")
    monkeypatch.setattr(ProductionConfig,"SECRET_KEY","replace-with-a-long-random-string",raising=False)
    with pytest.raises(ValueError,match="SECRET_KEY"): create_app("production")

def test_opening_balances_cover_balance_sheet_accounts_and_initialize_once(client,app):
    register(client)
    page=client.get("/opening")
    assert page.status_code==200 and b"Accounts Payable" in page.data and b"Equipment" in page.data
    opening={"date":"2026-07-01","balance_1":"1000.00","balance_4":"100.00","balance_5":"300.00","balance_7":"800.00"}
    response=client.post("/opening",data=opening,follow_redirects=True)
    assert b"Opening balances posted" in response.data
    with app.app_context():
        entry=JournalEntry.query.filter_by(kind="opening").one()
        assert sum((line.debit for line in entry.lines),Decimal("0.00"))==sum((line.credit for line in entry.lines),Decimal("0.00"))==Decimal("1100.00")
        data=reports(entry.company_id,date(2026,7,1),date(2026,7,31))
        assert data["net"]==Decimal("0.00") and data["cash_change"]==Decimal("0.00")
        assert data["cash_start"]==Decimal("1000.00") and data["cash_end"]==Decimal("1000.00")
        assert data["asset_total"]==Decimal("1100.00") and data["liability_total"]+data["equity_total"]==Decimal("1100.00")
        entry_id=entry.id
    duplicate=client.post("/opening",data=opening)
    assert b"already exist" in duplicate.data
    with app.app_context(): assert JournalEntry.query.filter_by(kind="opening").count()==1
    reversal=client.post(f"/transactions/{entry_id}/reverse",follow_redirects=True)
    assert b"Reversal posted" in reversal.data
    corrected={"date":"2026-07-01","balance_1":"900.00","balance_4":"100.00","balance_5":"200.00","balance_7":"800.00"}
    corrected_response=client.post("/opening",data=corrected,follow_redirects=True)
    assert b"Opening balances posted" in corrected_response.data
    with app.app_context():
        assert OpeningBalanceInitialization.query.count()==1
        data=reports(User.query.one().company_id,date(2026,7,1),date.today())
        assert data["net"]==Decimal("0.00") and data["cash_change"]==Decimal("0.00")
        assert data["cash_start"]==Decimal("900.00") and data["cash_end"]==Decimal("900.00") and data["balanced"]

def test_opening_balances_must_balance_and_are_admin_only(app):
    client=app.test_client(); register(client)
    bad=client.post("/opening",data={"date":"2026-08-01","balance_1":"100.00","balance_7":"50.00"})
    assert b"must balance" in bad.data
    with app.app_context(): assert JournalEntry.query.filter_by(kind="opening").count()==0
    with app.app_context():
        admin=User.query.one(); accountant=User(company_id=admin.company_id,email="open-accountant@example.com",name="Accountant",role="accountant"); accountant.set_password("accountant-password"); db.session.add(accountant); db.session.commit()
    client.post("/logout"); client.post("/login",data={"email":"open-accountant@example.com","password":"accountant-password"})
    assert client.get("/opening").status_code==403

def test_opening_balances_can_record_opposite_side_account_balance(client,app):
    register(client)
    response=client.post("/opening",data={"date":"2026-08-02","balance_1":"100.00","balance_5":"-20.00","balance_7":"120.00"},follow_redirects=True)
    assert b"Opening balances posted" in response.data
    with app.app_context():
        data=reports(User.query.one().company_id,date(2026,8,2),date(2026,8,2))
        liability=dict((account.code,value) for account,value in data["liabilities"])
        assert liability["2000"]==Decimal("-20.00") and data["asset_total"]==Decimal("100.00")
        assert data["equity_total"]==Decimal("120.00") and data["balanced"]

def test_account_management_permissions_crud_and_protects_used_accounts(app):
    client=app.test_client(); register(client)
    assert client.get("/accounts").status_code==200
    created=client.post("/accounts",data={"code":"7200","name":"Marketing","account_type":"expense","cash_flow_classification":"operating"},follow_redirects=True)
    assert b"Account created" in created.data
    duplicate=client.post("/accounts",data={"code":"7200","name":"Duplicate","account_type":"expense","cash_flow_classification":"operating"},follow_redirects=True)
    assert b"already exists" in duplicate.data
    with app.app_context():
        admin=User.query.one(); account=Account.query.filter_by(company_id=admin.company_id,code="7200").one(); account_id=account.id
        post_entry(admin.company_id,admin.id,date(2026,9,1),"Marketing spend",[(account.id,Decimal("5.00"),0),(Account.query.filter_by(company_id=admin.company_id,code="1000").one().id,0,Decimal("5.00"))]); db.session.commit()
    edit=client.post(f"/accounts/{account_id}/edit",data={"code":"7201","name":"Marketing Updated","account_type":"expense","cash_flow_classification":"operating"},follow_redirects=True)
    assert b"Account updated" in edit.data
    blocked=client.post(f"/accounts/{account_id}/edit",data={"code":"7201","name":"Marketing Updated","account_type":"asset","cash_flow_classification":"operating"},follow_redirects=True)
    assert b"cannot change after posted entries" in blocked.data
    deleted=client.post(f"/accounts/{account_id}/delete",follow_redirects=True)
    assert b"used by a posted journal entry" in deleted.data
    with app.app_context(): assert Account.query.filter_by(id=account_id).one().active
    unused=client.post("/accounts",data={"code":"7300","name":"Unused","account_type":"expense","cash_flow_classification":"operating"})
    with app.app_context(): unused_id=Account.query.filter_by(code="7300").one().id
    assert b"Account deleted" in client.post(f"/accounts/{unused_id}/delete",follow_redirects=True).data
    with app.app_context(): assert Account.query.filter_by(id=unused_id).first() is None

def test_public_registration_can_be_disabled_and_cli_creates_initial_admin(app):
    app.config["ENABLE_PUBLIC_REGISTRATION"]=False
    client=app.test_client()
    assert client.get("/").status_code==200 and b"Create company" not in client.get("/").data
    assert client.get("/register").status_code==404 and client.post("/register").status_code==404
    runner=app.test_cli_runner()
    result=runner.invoke(args=["create-admin","--company","Private Co","--name","Owner","--email","owner@example.com","--password","secure-admin-password"],input="secure-admin-password\n")
    assert result.exit_code==0, result.output
    with app.app_context():
        user=User.query.one(); assert user.is_admin and user.company.name=="Private Co" and user.check_password("secure-admin-password")

def test_csv_import_uses_selected_cash_bank_account_and_rejects_foreign_account(app):
    first=app.test_client(); register(first,"first@example.com","First Co")
    with app.app_context(): foreign_bank=Account.query.filter_by(company_id=User.query.filter_by(email="first@example.com").one().company_id,code="1010").one().id
    second=app.test_client(); register(second,"second@example.com","Second Co")
    csv_data=b"date,type,amount,description,account_code\n2026-10-02,income,38.50,Test sale,4000\n"
    preview=second.post("/import",data={"file":(io.BytesIO(csv_data),"cash.csv")},content_type="multipart/form-data")
    assert b"cash_account_id" in preview.data and b"Bank" in preview.data
    token=re.search(rb'name="batch_token" value="([^"]+)"',preview.data).group(1).decode()
    rejected=second.post("/import",data={"confirm":"1","batch_token":token,"cash_account_id":str(foreign_bank)})
    assert b"belonging to this company" in rejected.data
    # The preview remains available after a rejected destination; choose this company's Bank.
    with app.app_context(): own_bank=Account.query.filter_by(company_id=User.query.filter_by(email="second@example.com").one().company_id,code="1010").one().id
    imported=second.post("/import",data={"confirm":"1","batch_token":token,"cash_account_id":str(own_bank)},follow_redirects=True)
    assert b"Imported 1" in imported.data
    with app.app_context():
        entry=JournalEntry.query.filter_by(company_id=User.query.filter_by(email="second@example.com").one().company_id,description="Test sale").one()
        assert any(line.account_id==own_bank and line.debit==Decimal("38.50") for line in entry.lines)
