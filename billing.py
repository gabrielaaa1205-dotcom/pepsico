"""Control de facturación por bloques, aislado de los Kardex históricos."""
import re, json, datetime
from decimal import Decimal
from flask import Blueprint, request, jsonify, render_template, session, abort
from psycopg.rows import dict_row

billing = Blueprint("billing", __name__)
TYPES = {"buenas", "rechazadas", "merma"}
STATES = {"emitida", "aprobada", "rechazada", "anulada", "pagada"}

def init_billing(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS billing_blocks (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        invoice TEXT NOT NULL, category TEXT NOT NULL,
        date_from DATE NOT NULL, date_to DATE NOT NULL,
        amount NUMERIC(14,2), status TEXT NOT NULL DEFAULT 'emitida',
        notes TEXT NOT NULL DEFAULT '', created_by BIGINT REFERENCES users(id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(invoice,category,date_from,date_to),
        CHECK (date_from <= date_to),
        CHECK(category IN ('buenas','rechazadas','merma')),
        CHECK(status IN ('emitida','aprobada','rechazada','anulada','pagada'))
    )""")

def guard(write=False):
    if not session.get("uid"): abort(401)
    if write:
        if session.get("role") not in ("admin", "supervisor"): abort(403)
        if request.headers.get("X-CSRF-Token") != session.get("csrf"): abort(403)

def valid_date(v):
    return datetime.date.fromisoformat(str(v))

@billing.route("/billing")
def page():
    guard()
    return render_template("billing.html", csrf=session["csrf"], role=session["role"])

@billing.route("/api/billing/blocks", methods=["GET","POST"])
def blocks():
    guard(request.method=="POST")
    from server import db
    if request.method=="GET":
        with db() as c:
            init_billing(c)
            rows=c.execute("SELECT id,invoice,category,date_from,date_to,amount,status,notes FROM billing_blocks ORDER BY date_from DESC,id DESC").fetchall()
        for r in rows:
            r["date_from"]=str(r["date_from"]);r["date_to"]=str(r["date_to"])
            if r["amount"] is not None:r["amount"]=float(r["amount"])
        return jsonify(rows)
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict):return jsonify(error="Datos inválidos"),400
    try:
        invoice=str(payload.get("invoice","")).strip().upper().replace(" ","")
        category=str(payload.get("category","")).lower()
        status=str(payload.get("status","emitida")).lower()
        d1=valid_date(payload.get("date_from"));d2=valid_date(payload.get("date_to"))
        amount=payload.get("amount")
        amount=Decimal(str(amount)) if amount not in (None,"") else None
        notes=str(payload.get("notes",""))[:500]
        if not re.fullmatch(r"[A-Z0-9-]{4,40}",invoice) or category not in TYPES or status not in STATES or d1>d2 or (amount is not None and (not amount.is_finite() or amount<0)):raise ValueError()
    except (ValueError,TypeError,ArithmeticError):
        return jsonify(error="Verifica factura, fechas, categoría, estado e importe"),400
    with db() as c:
        init_billing(c)
        existing=c.execute("SELECT id FROM billing_blocks WHERE invoice=%s AND category=%s AND date_from=%s AND date_to=%s",(invoice,category,d1,d2)).fetchone()
        if existing:return jsonify(error="Este bloque ya existe; no se duplicó"),409
        c.execute("INSERT INTO billing_blocks(invoice,category,date_from,date_to,amount,status,notes,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",(invoice,category,d1,d2,amount,status,notes,session["uid"]))
        c.execute("INSERT INTO audit(actor,operation,object_key) VALUES(%s,%s,%s)",(session["uid"],"BILLING_BLOCK_CREATED",invoice))
    return jsonify(ok=True),201

@billing.route("/api/billing/reconciliation")
def reconciliation():
    guard()
    from server import db
    with db() as c:
        init_billing(c)
        items=c.execute("SELECT invoice,category,date_from,date_to,status,amount FROM billing_blocks").fetchall()
        row=c.execute("SELECT value FROM state WHERE key='kx-data'").fetchone()
        cfg_row=c.execute("SELECT value FROM state WHERE key='kx-cfg'").fetchone()
    data=row["value"] if row else {}
    cfg=cfg_row["value"] if cfg_row else {}
    rates={"buenas":float(cfg.get("pBuena",0.143)), "rechazadas":float(cfg.get("pRech",0.093)), "merma":float(cfg.get("pMerma",0.070))}
    # Only recognized components are counted; ambiguous transport is never auto-confirmed.
    output=[]
    for r in data.get("S",[]) if isinstance(data,dict) else []:
        try:
            date=valid_date(r["f"]);guide=str(r.get("guia",""))
            a=r.get("a",{});x=r.get("x",{})
            if not isinstance(a,dict) or not isinstance(x,dict):continue
            good=sum(float(v or 0) for v in a.values())
            reject=sum(float(v or 0) for v in x.values())
            waste=float(r.get("me",0) or 0)
        except (KeyError,ValueError,TypeError):continue
        for cat,qty in (("buenas",good),("rechazadas",reject),("merma",waste)):
            if qty<=0:continue
            candidates=[b for b in items if b["category"]==cat and b["date_from"]<=date<=b["date_to"] and b["status"]!="anulada"]
            accepted=[b for b in candidates if b["status"] in ("emitida","aprobada","pagada")]
            state="pendiente" if not candidates else ("revision" if len(candidates)!=1 or not accepted else "facturada")
            # Matching is by date and concept, NOT proof of invoice quantity or payment.
            output.append({"date":str(date),"guide":guide,"category":cat,"quantity":qty,"value":round(qty*rates[cat],2),"state":state,"invoice":accepted[0]["invoice"] if state=="facturada" else None,
              "reason":"Coincidencia provisional: validar cantidades y montos" if state=="facturada" else ("Bloques superpuestos/rechazados; revisar" if state=="revision" else "Sin bloque")})
    return jsonify({"rows":output,"disclaimer":"La coincidencia fecha/concepto es provisional. Las guías con transporte CIANSE, cantidades parciales o diferencias requieren validación; no constituye confirmación contable."})

@billing.route("/api/billing/import-preview", methods=["POST"])
def import_preview():
    """Read-only preview: no writes; unresolved years/concepts remain for review."""
    guard(True)
    from io import BytesIO
    from python_calamine import CalamineWorkbook
    up=request.files.get("file")
    if not up or not up.filename.lower().endswith((".xlsx",".xlsm",".xls")):
        return jsonify(error="Adjunta un libro Excel válido"),400
    raw=up.read(15*1024*1024+1)
    if len(raw)>15*1024*1024:return jsonify(error="Archivo demasiado grande"),413
    try:wb=CalamineWorkbook.from_filelike(BytesIO(raw))
    except Exception:return jsonify(error="Excel ilegible"),400
    preview=[];skipped=[]
    import calendar
    months={"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,"julio":7,"agosto":8,"setiembre":9,"septiembre":9,"octubre":10,"noviembre":11,"diciembre":12,"ene":1,"feb":2,"mar":3,"abr":4,"may":5,"jun":6,"jul":7,"ago":8,"set":9,"sep":9,"oct":10,"nov":11,"dic":12}
    def dates(label,year):
        value=str(label).lower()
        matches=re.findall(r"\b(0?[1-9]|[12][0-9]|3[01])\b",value)
        mm=next((v for k,v in months.items() if re.search(r"\b"+k+r"\b",value)),None)
        if not mm or len(matches)<2 or not year:return None
        try:
            start=datetime.date(year,mm,int(matches[0]))
            end=datetime.date(year,mm,int(matches[1]))
            return start,end
        except ValueError:return None
    def normalize_invoice(v):
        m=re.search(r"E\s*0*0?1\s*-?\s*(\d{3,7})",str(v).upper())
        return "E001-"+m.group(1) if m else None
    for sheet in wb.sheets:
        name=sheet.name
        if "pepsico" not in name.lower() or "factur" not in name.lower():continue
        year_match=re.search(r"20(?:2[0-9])",name)
        year=int(year_match.group()) if year_match else None
        data=sheet.to_python()
        for index,row in enumerate(data):
            cells=["" if v is None else str(v).strip() for v in row]
            invoices=[normalize_invoice(v) for v in cells]
            invoices=[v for v in invoices if v]
            if not invoices:continue
            inv=invoices[0]
            period=next((v for v in cells if re.search(r"\bdel?\s+\d{1,2}\s+al\s+\d{1,2}",v.lower())),None)
            category=next((("rechazadas" if "rechaz" in v.lower() else "buenas" if "buen" in v.lower() else "merma") for v in cells if any(x in v.lower() for x in ["rechaz","buenas","merma"])),None)
            dt=dates(period,year) if period else None
            amount=next((float(v) for v in row[3:6] if isinstance(v,(int,float)) and v>0),None)
            status="rechazada" if any("rechazada - pendiente" in v.lower() or "no tiene pe" in v.lower() or "pendiente pe" in v.lower() for v in cells) else ("aprobada" if any("aprobada comercialmente" in v.lower() for v in cells) else "emitida")
            item={"sheet":name,"excel_row":index+1,"invoice":inv,"category":category,"date_from":str(dt[0]) if dt else None,"date_to":str(dt[1]) if dt else None,"amount":amount,"status":status,"period_text":period,"needs_review":not (dt and category),"year_confirmed":bool(year)}
            preview.append(item)
    return jsonify({"preview":preview,"requires_review":sum(x["needs_review"] for x in preview),"instructions":"No se guardó ningún registro. Debes confirmar años y conceptos faltantes antes de importar."})
