"""Fast manual daily box-selection reporting. Keeps legacy records intact."""
import datetime,json
from zoneinfo import ZoneInfo
from flask import Blueprint,request,session,jsonify,render_template_string,abort
from server import db,check_csrf
from worker_selection import setup,integer

bp_daily=Blueprint('worker_daily',__name__)
MERMA=('sucio','excremento','pisadas','grasa','oxido','plaga','otros')
COLORS=('azul','morado','verde','negro')
TZ=ZoneInfo('America/Lima')

def require_user():
    if not session.get('uid'):abort(401)

def ensure_columns(c):
    setup(c)
    c.execute('ALTER TABLE worker_selection_reports ADD COLUMN IF NOT EXISTS worker_first_name TEXT')
    c.execute('ALTER TABLE worker_selection_reports ADD COLUMN IF NOT EXISTS worker_last_name TEXT')
    c.execute('ALTER TABLE worker_selection_reports ADD COLUMN IF NOT EXISTS entry_method TEXT')

@bp_daily.route('/seleccion-trabajadores/registrar')
def registration_page():
    if not session.get('uid'):
        from flask import redirect
        return redirect('/login')
    if session.get('role') not in ('operario','supervisor','admin'):abort(403)
    with db() as c:
        ensure_columns(c)
        names=c.execute("""SELECT DISTINCT trim(transport) AS transport FROM worker_selection_reports
          WHERE length(trim(transport))>1 ORDER BY 1 LIMIT 60""").fetchall()
        people=c.execute("""SELECT DISTINCT trim(worker) AS worker FROM worker_selection_reports
          WHERE length(trim(worker))>2 ORDER BY 1 LIMIT 60""").fetchall()
    return render_template_string(PAGE,csrf=session['csrf'],username=session.get('username',''),
        today=datetime.datetime.now(TZ).date().isoformat(),
        transports=[r['transport'] for r in names],workers=[r['worker'] for r in people])

@bp_daily.route('/api/seleccion-trabajadores/registrar',methods=['POST'])
def register_daily():
    require_user()
    if session.get('role') not in ('operario','supervisor','admin'):abort(403)
    check_csrf()
    data=request.get_json(silent=True)
    if not isinstance(data,dict):return jsonify(error='Datos inválidos'),400
    first=str(data.get('first_name') or '').strip()
    last=str(data.get('last_name') or '').strip()
    transport=str(data.get('transport') or '').strip()
    if not first or not last:return jsonify(error='Indica nombre y apellido de la trabajadora'),400
    if not transport:return jsonify(error='Selecciona el transporte revisado'),400
    if any(len(v)>100 for v in [first,last,transport]):return jsonify(error='Texto demasiado largo'),400
    try:
        worked=datetime.date.fromisoformat(str(data.get('worked_on') or ''))
        if worked>datetime.datetime.now(TZ).date():return jsonify(error='La fecha no puede ser futura'),400
        if worked<datetime.date(2025,1,1):return jsonify(error='Revisa la fecha ingresada'),400
        merma={k:integer((data.get('merma') or {}).get(k)) or 0 for k in MERMA}
        rejected={k:integer((data.get('rejected') or {}).get(k)) or 0 for k in COLORS}
        pallets=integer(data.get('pallets'))
    except (ValueError,TypeError,AttributeError):return jsonify(error='Revisa las cantidades y la fecha'),400
    if not any(merma.values()) and not any(rejected.values()):
        return jsonify(error='Ingresa al menos una cantidad de cajas'),400
    notes=str(data.get('notes') or '').strip()[:900]
    full=' '.join((first+' '+last).split())
    with db() as c:
        ensure_columns(c)
        item=c.execute("""INSERT INTO worker_selection_reports(
          worker,worker_first_name,worker_last_name,worked_on,transport,pallets,
          malo,defects,selected_colors,notes,creator,entry_method)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,'form')
          RETURNING id,created_at""",
          (full,first,last,worked,transport,pallets,sum(merma.values()),json.dumps(merma),
           json.dumps(rejected),notes,session['uid'])).fetchone()
        c.execute("INSERT INTO audit(actor,operation,object_key) VALUES (%s,%s,%s)",
                  (session['uid'],'DAILY_WORKER_ENTRY',str(item['id'])))
    return jsonify(ok=True,id=item['id'],worker=full,transport=transport,
        merma=sum(merma.values()),rejected=sum(rejected.values()),
        registered_at=item['created_at'].astimezone(TZ).isoformat(),
        recorded_by=session.get('username','')),201


@bp_daily.route('/api/seleccion-trabajadores/registrar-lote',methods=['POST'])
def register_daily_batch():
    """Save one daily submission with up to twelve independent transport lines atomically."""
    require_user()
    if session.get('role') not in ('operario','supervisor','admin'):abort(403)
    check_csrf()
    data=request.get_json(silent=True)
    if not isinstance(data,dict):return jsonify(error='Datos inválidos'),400
    first=str(data.get('first_name') or '').strip()
    last=str(data.get('last_name') or '').strip()
    if not first or not last or len(first)>100 or len(last)>100:
        return jsonify(error='Indica nombre y apellido de la trabajadora'),400
    try:
        day=datetime.date.fromisoformat(str(data.get('worked_on') or ''))
        if day>datetime.datetime.now(TZ).date() or day<datetime.date(2025,1,1):
            return jsonify(error='Revisa la fecha del trabajo'),400
    except ValueError:return jsonify(error='Fecha inválida'),400
    lines=data.get('transports')
    if not isinstance(lines,list) or not 1<=len(lines)<=12:
        return jsonify(error='Ingresa entre 1 y 12 transportes'),400
    token=str(data.get('request_id') or '').strip()
    import uuid
    try:
        uuid.UUID(token)
        if len(token)!=36:raise ValueError()
    except (ValueError,TypeError,AttributeError):
        return jsonify(error='Identificador de envío inválido'),400
    prepared=[]
    for idx,line in enumerate(lines,1):
        if not isinstance(line,dict):return jsonify(error=f'Transporte {idx}: datos inválidos'),400
        transport=str(line.get('transport') or '').strip()
        if not transport or len(transport)>100:
            return jsonify(error=f'Transporte {idx}: indica la empresa de transporte'),400
        try:
            if not isinstance(line.get('merma'),dict) or not isinstance(line.get('rejected'),dict):
                raise ValueError('Categorías inválidas')
            merma={k:integer(line['merma'].get(k)) or 0 for k in MERMA}
            rejects={k:integer(line['rejected'].get(k)) or 0 for k in COLORS}
            pallets=integer(line.get('pallets'))
        except (ValueError,TypeError):
            return jsonify(error=f'Transporte {idx}: revisa las cantidades'),400
        if not any(merma.values()) and not any(rejects.values()):
            return jsonify(error=f'Transporte {idx}: registra al menos una caja'),400
        prepared.append((transport,pallets,merma,rejects,str(line.get('notes') or '').strip()[:600]))
    full=' '.join((first+' '+last).split())
    with db() as c:
        ensure_columns(c)
        c.execute("""CREATE TABLE IF NOT EXISTS worker_selection_batches(
          request_id UUID PRIMARY KEY,
          first_name TEXT NOT NULL,last_name TEXT NOT NULL,worked_on DATE NOT NULL,
          creator BIGINT REFERENCES users(id),
          report_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        # One submission / one database transaction: prevents partial saves.
        batch=c.execute("""INSERT INTO worker_selection_batches(request_id,first_name,last_name,worked_on,creator)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING created_at""",
            (token,first,last,day,session['uid'])).fetchone()
        if batch is None:
            previous=c.execute("""SELECT creator,report_ids FROM worker_selection_batches
              WHERE request_id=%s""",(token,)).fetchone()
            if previous['creator']!=session['uid']:abort(409)
            return jsonify(ok=True,duplicate=True,ids=previous['report_ids'],
                message='Este envío ya estaba guardado. No se duplicaron transportes.'),200
        ids=[]
        for transport,pallets,merma,rejects,notes in prepared:
            item=c.execute("""INSERT INTO worker_selection_reports(
                worker,worker_first_name,worker_last_name,worked_on,transport,
                pallets,malo,defects,selected_colors,notes,creator,entry_method)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,'form')
                RETURNING id""",
                (full,first,last,day,transport,pallets,sum(merma.values()),
                 json.dumps(merma),json.dumps(rejects),notes,session['uid'])).fetchone()
            ids.append(item['id'])
        c.execute("UPDATE worker_selection_batches SET report_ids=%s::jsonb WHERE request_id=%s",
                  (json.dumps(ids),token))
        c.execute("INSERT INTO audit(actor,operation,object_key) VALUES (%s,%s,%s)",
                  (session['uid'],'CREATE_WORKER_SELECTION_BATCH',token))
        created=batch['created_at']
    return jsonify(ok=True,ids=ids,worker=full,transport_count=len(ids),
        merma=sum(sum(x[2].values()) for x in prepared),
        rejected=sum(sum(x[3].values()) for x in prepared),
        recorded_by=session.get('username',''),
        registered_at=created.astimezone(TZ).isoformat()),201

@bp_daily.route('/api/seleccion-trabajadores/registro-reciente')
def recent_entries():
    require_user()
    with db() as c:
        ensure_columns(c)
        rows=c.execute("""SELECT r.id,r.worker,r.worked_on,r.transport,r.malo,r.selected_colors,
          r.created_at,r.entry_method,u.username AS recorded_by
          FROM worker_selection_reports r LEFT JOIN users u ON u.id=r.creator
          WHERE r.entry_method='form'
          ORDER BY r.created_at DESC LIMIT 30""").fetchall()
    return jsonify(entries=[dict(x,worked_on=x['worked_on'].isoformat(),
       created_at=x['created_at'].astimezone(TZ).isoformat()) for x in rows])

PAGE=r'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CIANSE PRO · Registro diario por transportes</title><style>
:root{--deep:#153e35;--green:#087f62;--soft:#eff7f3;--line:#dce7e1;--muted:#63766e}*{box-sizing:border-box}body{margin:0;background:#f5f8f6;color:#1c3c31;font:15px/1.45 system-ui,-apple-system,sans-serif}header{background:var(--deep);color:white;padding:16px 20px}header .wrap{max-width:1020px;margin:auto;display:flex;justify-content:space-between;gap:10px;align-items:center}header a{color:#c5eadd;font-size:13px}main{max-width:1020px;padding:20px 14px 100px;margin:auto}h1{font-size:26px;letter-spacing:-.5px;margin:0}h2{font-size:17px;margin:0}.muted{color:var(--muted);font-size:13px}.panel,.transport-card{border:1px solid var(--line);background:white;border-radius:15px;padding:18px;margin:15px 0;box-shadow:0 2px 10px #163f3109}.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}label{display:block;font-size:13px;font-weight:750;margin-bottom:5px}input,textarea{border:1px solid #ccdad1;border-radius:9px;background:white;width:100%;padding:10px 12px;min-height:44px;font:inherit;color:#193b30}input:focus,textarea:focus{outline:2px solid #8fceba}input[type=number]{text-align:center;font-size:18px;font-weight:750;padding:8px}input::placeholder{color:#a2b0a8}button{cursor:pointer;font:inherit;border:0;border-radius:10px;background:var(--green);color:white;font-weight:750;padding:12px 16px}button.secondary{background:#e5f2ec;color:#16624d}button.outline{background:white;border:1px solid #bed6c9;color:#1e634d}button.ghost{background:transparent;color:#7d534a;padding:7px 9px}button:disabled{opacity:.55}.titlebar,.transport-top{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}.tTitle{font-weight:850;font-size:17px}.tTitle small{display:block;color:var(--muted);font-size:12px;font-weight:500}.transport-top{cursor:pointer}.transport-head{display:flex;align-items:center;gap:10px}.num{height:34px;min-width:34px;display:grid;place-items:center;border-radius:9px;background:#e3f2eb;font-weight:850;color:#18624d}.summary-pill{font-size:12px;background:#f2f6f3;border-radius:50px;padding:7px 11px;font-weight:700}.transport-body{margin-top:14px;border-top:1px solid var(--line);padding-top:15px}.split{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px;margin-top:13px}.group{border-radius:11px;padding:13px;background:#fff7eb}.group.reject{background:#f3eef9}.group h3{font-size:14px;margin:0 0 4px}.fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:12px}.field{background:#ffffff9a;border:1px solid #0000000b;border-radius:9px;padding:8px}.field label{font-size:12px}.subtotal{font-size:21px;font-weight:850}.help{font-size:12px;color:#64756c;margin:8px 0}.message{padding:12px;border-radius:10px;background:#e6f6eb;color:#185b38;margin:10px 0;white-space:pre-line}.message.error{background:#fff0ea;color:#9b3e2e}.sticky{position:sticky;bottom:0;background:white;border:1px solid #c7dacf;border-radius:13px;padding:12px 16px;box-shadow:0 -6px 25px #17382918;z-index:2}.summaryTotals{display:flex;justify-content:space-between;align-items:center;gap:15px;flex-wrap:wrap}.numberSummary{display:flex;gap:20px;flex-wrap:wrap}.numberSummary small{display:block;color:#657a70}.numberSummary strong{font-size:21px}.recent{padding:9px 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:12px}.recent small{display:block;color:var(--muted)}@media(max-width:650px){h1{font-size:23px}.grid2,.split{grid-template-columns:1fr}.panel,.transport-card{padding:14px}.numberSummary{gap:12px}.numberSummary strong{font-size:18px}.sticky button{width:100%}.summaryTotals{gap:10px}.fields{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style></head><body>
<header><div class="wrap"><div><strong style="font-size:19px">CIANSE PRO</strong><div style="font-size:12px;color:#bde0d0">Selección de cajas · registro operativo</div></div><a href="/seleccion-trabajadores">← Dashboard</a></div></header>
<main><h1>Un registro por día</h1><p class="muted">Anota todos los transportes que revisaste hoy y guarda todo una sola vez. Preparado para las 11 trabajadoras.</p>
<form id="form">
<section class="panel"><h2>1. Trabajadora y fecha</h2><div class="grid2" style="margin-top:12px"><div><label for="first">Nombre *</label><input id="first" autocomplete="given-name" placeholder="Ej. Nicole" required maxlength="100"></div><div><label for="last">Apellido *</label><input id="last" autocomplete="family-name" placeholder="Ej. Motta" required maxlength="100"></div><div><label for="day">Fecha de trabajo *</label><input id="day" type="date" value="{{today}}" required></div><div><label>Registrado por</label><input value="{{username}}" disabled></div></div><p class="help">La hora real del registro se guardará automáticamente al presionar «Guardar día».</p></section>
<div class="titlebar"><div><h2>2. Transportes revisados</h2><p class="muted">Normalmente 4; puedes agregar más o dejar alguno vacío.</p></div><button class="secondary" type="button" id="add">+ Agregar transporte</button></div>
<div id="transportList"></div>
<datalist id="transportOptions">{% for t in transports %}<option value="{{t}}">{% endfor %}</datalist>
<p class="help">Cada transporte mantiene sus cantidades de Merma y Rechazadas. El dashboard los reunirá bajo tu nombre y el mismo día.</p>
<div id="message" role="status" aria-live="polite"></div>
<div class="sticky"><div class="summaryTotals"><div class="numberSummary"><div><small>Transportes</small><strong id="nTransports">0</strong></div><div><small>Merma</small><strong id="sMerma">0</strong></div><div><small>Rechazadas</small><strong id="sRech">0</strong></div><div><small>Total</small><strong id="sTotal">0</strong></div></div><button id="submit" type="submit">Guardar día completo</button></div></div>
</form>
<section class="panel"><h2>Últimos ingresos</h2><p class="muted">Consulta la hora de registro y qué usuario de CIANSE realizó el ingreso.</p><div id="recent">Cargando…</div></section>
</main><script>
const csrf={{csrf|tojson}},today={{today|tojson}},Q=id=>document.getElementById(id),fmt=n=>Number(n||0).toLocaleString('es-PE');
const MERMA=[['sucio','Sucias'],['excremento','Heces'],['pisadas','Pisadas'],['grasa','Grasa'],['oxido','Óxido'],['plaga','Plaga'],['otros','Otros daños']],RECH=[['azul','Azules'],['morado','Moradas'],['verde','Verdes'],['negro','Negras']];
let cards=[],requestId=crypto.randomUUID();
function numbers(card,group){return Object.fromEntries((group==='merma'?MERMA:RECH).map(([key])=>[key,Number(card.querySelector('[data-group="'+group+'"][data-key="'+key+'"]').value||0)]))}
function sum(values){return Object.values(values).reduce((a,v)=>a+v,0)}
function read(card){return {transport:card.querySelector('.transport-name').value.trim(),pallets:card.querySelector('.pallets').value||null,notes:card.querySelector('.notes').value.trim(),merma:numbers(card,'merma'),rejected:numbers(card,'rejected')}}
function refresh(){let active=0,m=0,r=0;for(const card of cards){const v=read(card),mm=sum(v.merma),rr=sum(v.rejected);if(v.transport||mm+rr)active++;m+=mm;r+=rr;card.querySelector('.summary-pill').textContent='Merma '+fmt(mm)+' · Rech. '+fmt(rr);card.querySelector('.mermaTotal').textContent=fmt(mm);card.querySelector('.rejectTotal').textContent=fmt(rr);card.querySelector('.transport-label').textContent=v.transport||'Sin transporte';}Q('nTransports').textContent=active;Q('sMerma').textContent=fmt(m);Q('sRech').textContent=fmt(r);Q('sTotal').textContent=fmt(m+r)}
function makeFields(group,fields){const wrap=document.createElement('div');wrap.className='fields';for(const [key,label] of fields){const cell=document.createElement('div');cell.className='field';const lab=document.createElement('label');lab.textContent=label;const inp=document.createElement('input');inp.type='number';inp.min='0';inp.max='1000000';inp.inputMode='numeric';inp.placeholder='0';inp.dataset.key=key;inp.dataset.group=group;inp.addEventListener('input',refresh);cell.append(lab,inp);wrap.append(cell)}return wrap}
function addCard(expanded=false){if(cards.length>=12)return;const card=document.createElement('section');card.className='transport-card';const top=document.createElement('div');top.className='transport-top';const heading=document.createElement('div');heading.className='transport-head';const number=document.createElement('span');number.className='num';number.textContent=cards.length+1;const title=document.createElement('div');title.className='tTitle';title.textContent='Transporte '+(cards.length+1);const label=document.createElement('small');label.className='transport-label';label.textContent='Sin transporte';title.append(label);heading.append(number,title);const status=document.createElement('span');status.className='summary-pill';status.textContent='Merma 0 · Rech. 0';top.append(heading,status);const body=document.createElement('div');body.className='transport-body';body.hidden=!expanded;top.addEventListener('click',()=>{body.hidden=!body.hidden});const two=document.createElement('div');two.className='grid2';const t=document.createElement('div');const tlabel=document.createElement('label');tlabel.textContent='Transporte / camión';const tn=document.createElement('input');tn.className='transport-name';tn.setAttribute('list','transportOptions');tn.placeholder='Ej. DYR o XIONFI';tn.maxLength=100;tn.addEventListener('input',refresh);t.append(tlabel,tn);const p=document.createElement('div');const pl=document.createElement('label');pl.textContent='Pallets revisados (opcional)';const pi=document.createElement('input');pi.className='pallets';pi.type='number';pi.min='0';pi.inputMode='numeric';pi.placeholder='0';p.append(pl,pi);two.append(t,p);body.append(two);const split=document.createElement('div');split.className='split';for(const [g,fields,titleText] of [['merma',MERMA,'Merma · tipo de daño'],['rejected',RECH,'Rechazadas · color']]){const group=document.createElement('div');group.className='group'+(g==='rejected'?' reject':'');const h=document.createElement('h3');h.textContent=titleText;const total=document.createElement('div');total.className='subtotal '+(g==='merma'?'mermaTotal':'rejectTotal');total.textContent='0';group.append(h,total,makeFields(g,fields));split.append(group)}body.append(split);const nl=document.createElement('label');nl.textContent='Observaciones (opcional)';nl.style.marginTop='12px';const notes=document.createElement('textarea');notes.className='notes';notes.rows=2;notes.placeholder='Ej. Cajas aplastadas';body.append(nl,notes);const remove=document.createElement('button');remove.type='button';remove.className='ghost';remove.textContent='Eliminar este transporte';remove.addEventListener('click',()=>{cards=cards.filter(c=>c!==card);card.remove();cards.forEach((c,i)=>{c.querySelector('.num').textContent=i+1;c.querySelector('.tTitle').firstChild.textContent='Transporte '+(i+1)});refresh()});body.append(remove);card.append(top,body);cards.push(card);Q('transportList').append(card);refresh();return card}
for(let i=0;i<4;i++)addCard(i===0);
Q('add').onclick=()=>{const c=addCard(true);if(c){c.scrollIntoView({behavior:'smooth',block:'center'});c.querySelector('.transport-name').focus()}};
Q('form').onsubmit=async event=>{event.preventDefault();const active=cards.map(read).filter(v=>v.transport||sum(v.merma)+sum(v.rejected)>0);const msg=Q('message');msg.className='';msg.textContent='';if(!active.length){msg.className='message error';msg.textContent='Ingresa al menos un transporte y sus cantidades.';return}const invalid=active.findIndex(v=>!v.transport||sum(v.merma)+sum(v.rejected)===0);if(invalid>=0){msg.className='message error';msg.textContent='El transporte '+(invalid+1)+' necesita un nombre y al menos una caja.';msg.scrollIntoView({behavior:'smooth'});return}if(active.length>12){msg.className='message error';msg.textContent='Máximo 12 transportes por envío';return}const data={request_id:requestId,first_name:Q('first').value.trim(),last_name:Q('last').value.trim(),worked_on:Q('day').value,transports:active};const button=Q('submit');button.disabled=true;button.textContent='Guardando los transportes…';try{const res=await fetch('/api/seleccion-trabajadores/registrar-lote',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});const saved=await res.json();if(!res.ok)throw Error(saved.error||'No se pudo guardar');msg.className='message';msg.textContent='✓ Registro diario guardado. '+active.length+' transportes · Merma '+fmt(saved.merma)+' · Rechazadas '+fmt(saved.rejected)+' · Registró '+saved.recorded_by+' · '+new Date(saved.registered_at).toLocaleString('es-PE',{timeZone:'America/Lima',dateStyle:'short',timeStyle:'medium'});requestId=crypto.randomUUID();Q('transportList').replaceChildren();cards=[];for(let i=0;i<4;i++)addCard(i===0);Q('day').value=today;await loadRecent();msg.scrollIntoView({behavior:'smooth'})}catch(err){msg.className='message error';msg.textContent=err.message+' · Puedes intentar guardar nuevamente sin duplicar el envío.';msg.scrollIntoView({behavior:'smooth'})}finally{button.disabled=false;button.textContent='Guardar día completo'}};
async function loadRecent(){try{const response=await fetch('/api/seleccion-trabajadores/registro-reciente');const data=await response.json();if(!response.ok)throw Error('No se pudo cargar');const root=Q('recent');root.replaceChildren();if(!data.entries.length){root.textContent='Sin registros recientes.';return}for(const x of data.entries.slice(0,12)){const item=document.createElement('div');item.className='recent';const a=document.createElement('div');const name=document.createElement('strong');name.textContent=x.worker+' · '+x.transport;const sub=document.createElement('small');sub.textContent=x.worked_on+' · '+(x.recorded_by||'Usuario anterior')+' · '+new Date(x.created_at).toLocaleString('es-PE',{dateStyle:'short',timeStyle:'short',timeZone:'America/Lima'});a.append(name,sub);const b=document.createElement('strong');b.textContent='M '+fmt(x.malo)+' / R '+fmt(Object.values(x.selected_colors||{}).reduce((n,v)=>n+Number(v||0),0));item.append(a,b);root.append(item)}}catch(e){Q('recent').textContent='No se pudo cargar el historial.'}}loadRecent();
</script></body></html>'''
