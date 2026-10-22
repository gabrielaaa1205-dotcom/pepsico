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
