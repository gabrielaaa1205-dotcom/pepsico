"""Read-only 2026 billing comparison for existing production Kardex.
Historical invoice metadata from BORRADORES - BLOCK.xlsx, tab
Facturación Pepsico 2024 (2), rows 190-210. NEVER mutates state.
"""
import datetime
import json
from flask import Blueprint, render_template, request, session, redirect, jsonify

bp_billing2026=Blueprint('billing2026',__name__)

# (invoice, category, inclusive period, net amount as recorded, portal status, Excel row)
INVOICES=[
('E001-2191','merma','2026-01-02','2026-01-31',None,'Enviado',190),
('E001-2190','rechazadas','2026-01-02','2026-01-31',None,'Enviado',191),
('E001-2148','merma','2026-02-01','2026-02-28',6645.25,'Aprobado',192),
('E001-2147','rechazadas','2026-02-01','2026-02-28',5355.31,'Aprobado',193),
('E001-2135','buenas','2026-02-01','2026-02-28',41898.14,'Aprobado',194),
('E001-2148','buenas','2026-03-01','2026-03-31',6645.25,'Enviado',195),
('E001-2188','rechazadas','2026-03-01','2026-03-31',3968.97,'Enviado',196),
('E001-2189','merma','2026-03-01','2026-03-31',9817.09,'Enviado',197),
('E001-2203','buenas','2026-04-01','2026-04-30',42843.09,'Enviado',198),
('E001-2276','rechazadas','2026-04-01','2026-04-30',4411.55,'Enviado',199),
('E001-2277','merma','2026-04-01','2026-04-30',7727.31,'Enviado',200),
('E001-2252','rechazadas','2026-05-01','2026-05-30',5064.61,'Enviado',201),
('E001-2253','buenas','2026-05-01','2026-05-30',53051.86,'Enviado',202),
('E001-2275','merma','2026-05-01','2026-05-30',6309.90,'Enviado',203),
('E001-2295','buenas','2026-06-01','2026-06-30',54199.29,'Enviado',204),
('E001-2355','rechazadas','2026-06-01','2026-06-30',5574.79,'Enviado',205),
('E001-2354','merma','2026-06-01','2026-06-30',7727.31,'Enviado',206),
('E001-2353','buenas','2026-07-01','2026-07-31',62096.32,'Enviado',207),
('E001-2432','merma','2026-07-01','2026-07-31',9004.31,'Enviado',208),
('E001-2431','rechazadas','2026-07-01','2026-07-31',6118.11,'Enviado',209),
('E001-2430','buenas','2026-08-01','2026-08-31',51524.76,'Enviado',210),
]
AMBIGUOUS={'E001-2148'} # Same number appears for different concepts/periods in Excel.

def secure():
    return bool(session.get('uid'))

def summary():
    from server import db
    with db() as conn:
        data=conn.execute("SELECT value FROM state WHERE key='kx-data'").fetchone()
        cfg=conn.execute("SELECT value FROM state WHERE key='kx-cfg'").fetchone()
    source=(data or {}).get('value') or {}
    prices=(cfg or {}).get('value') or {}
    rates={'buenas':float(prices.get('pBuena',0.143)), 'rechazadas':float(prices.get('pRech',0.093)), 'merma':float(prices.get('pMerma',0.070))}
    tr_name=str(prices.get('trRech') or '').strip().upper()
    output=[]
    for guide in source.get('S',[]):
        d=str(guide.get('f',''))
        if not d.startswith('2026-'):continue
        a=guide.get('a') or {}; x=guide.get('x') or {}
        if not isinstance(a,dict) or not isinstance(x,dict):continue
        try:
            good=sum(float(v or 0) for v in a.values())
            bad=sum(float(v or 0) for v in x.values())
            waste=float(guide.get('me') or 0)
        except (ValueError,TypeError):continue
        transport=str(guide.get('tr') or '').upper()
        if tr_name and tr_name in transport:
            bad+=good
            good=0
        for category,n in [('buenas',good),('rechazadas',bad),('merma',waste)]:
            if n<=0:continue
            matched=[v for v in INVOICES if v[1]==category and v[2]<=d<=v[3]]
            if not matched:status='sin_coincidencia'
            elif len(matched)>1 or any(v[0] in AMBIGUOUS for v in matched):status='revisar'
            else:status='asociacion_provisional'
            output.append({'date':d,'guide':str(guide.get('guia') or ''),'category':category,
              'quantity':n,'estimated':round(n*rates[category],2),'status':status,
              'invoice':matched[0][0] if len(matched)==1 else None,
              'invoice_status':matched[0][5] if len(matched)==1 else None,
              'source_row':matched[0][6] if len(matched)==1 else None})
    return output

@bp_billing2026.route('/facturacion-kardex')
def billing_report():
    if not secure():return redirect('/login')
    rows=summary()
    return render_template('facturacion_kardex.html',rows=rows,invoices=INVOICES)

@bp_billing2026.route('/api/facturacion-kardex')
def billing_api():
    if not secure():return jsonify(error='Sesión requerida'),401
    return jsonify({'rows':summary(),'note':'Coincidencia por concepto y fecha, NO conciliación definitiva de cantidad/factura/pago. Ausencia en Excel no prueba falta de emisión.'})
