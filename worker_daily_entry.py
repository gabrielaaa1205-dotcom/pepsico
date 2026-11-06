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

PAGE=r'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CIANSE PRO · Registro de selección</title><style>
:root{--dark:#123d35;--green:#08785f;--line:#dce7e2;--muted:#60756c;--paper:#f5f8f6}*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:#213c34;font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
header{background:var(--dark);color:white;padding:17px 20px}.head-inner{max-width:1050px;margin:auto;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
header a{color:#c1e8da;text-decoration:none;font-size:13px}.brand{font-size:19px;font-weight:850}main{max-width:1050px;margin:22px auto;padding:0 15px 50px}
h1{font-size:27px;margin:0 0 5px;letter-spacing:-.6px}h2{font-size:17px;margin:0 0 10px}.muted{color:var(--muted);font-size:13px}.panel{background:white;border:1px solid var(--line);border-radius:15px;padding:20px;margin:14px 0;box-shadow:0 3px 13px #122f240a}
.two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}.three{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px}
label{display:block;font-size:13px;font-weight:750;margin-bottom:5px}input,textarea{width:100%;min-height:47px;border:1px solid #cbd9d1;border-radius:10px;padding:10px 12px;color:#18382e;background:#fff;font:inherit}input:focus,textarea:focus{outline:2px solid #94d1be;border-color:#359a7d}input[type=number]{text-align:center;font-size:19px;font-weight:750;font-variant-numeric:tabular-nums}input::placeholder{color:#9aaba2}
.nums{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.num-tile{border:1px solid var(--line);border-radius:11px;padding:11px;background:#fafcfb}.num-tile label{min-height:20px}.orange{border-left:5px solid #d7a056}.purple{border-left:5px solid #9479bd}
.section-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.amount{font-size:22px;font-weight:850;font-variant-numeric:tabular-nums}
.summary{position:sticky;bottom:0;z-index:2;background:#fff;border:1px solid #cadad1;border-radius:14px;padding:14px 16px;box-shadow:0 -7px 26px #143c2c1b}.totals{display:flex;gap:12px;flex-wrap:wrap;justify-content:space-between;align-items:center}.totalbox small{color:var(--muted);display:block}.totalbox b{font-size:22px}
button{background:var(--green);color:white;border:0;border-radius:10px;padding:13px 17px;font:inherit;font-weight:800;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.secondary{background:#e7f2ee;color:#155a48}.note{font-size:12px;color:#6b7f74}.alert{padding:12px;border-radius:10px;background:#e9f6ed;color:#215b3c}.error{background:#fff0ed;color:#9c3627}
.recent-row{display:flex;align-items:start;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid var(--line)}.recent-row small{display:block;color:var(--muted)}
@media(max-width:600px){h1{font-size:23px}.two,.three{grid-template-columns:1fr}.nums{grid-template-columns:repeat(2,minmax(0,1fr))}.panel{padding:15px}.totals{gap:9px}.totalbox b{font-size:19px}button{width:100%}.summary{border-radius:11px;padding:12px}}
</style></head><body><header><div class="head-inner"><div><div class="brand">CIANSE PRO</div><div style="font-size:12px;color:#c1dcd1">Control de selección de cajas</div></div><a href="/seleccion-trabajadores">← Ver dashboard</a></div></header>
<main><h1>Registro diario de selección</h1><p class="muted">Al terminar tu trabajo, registra las cajas revisadas de cada transporte. No necesitas tomar fotos.</p>
<form id="dailyForm">
<section class="panel"><div class="section-title"><h2>1. ¿Quién realizó la selección?</h2><span class="muted">Equipo: 11 trabajadoras</span></div>
<div class="two"><div><label for="first">Nombre *</label><input id="first" name="first" autocomplete="given-name" maxlength="100" placeholder="Ej. Nicole" required></div><div><label for="last">Apellido *</label><input id="last" name="last" autocomplete="family-name" maxlength="100" placeholder="Ej. Motta" required></div></div>
<p class="note">Si varias personas registran desde el mismo dispositivo, cada una debe indicar su nombre y apellido.</p>
</section>
<section class="panel"><h2>2. ¿Qué transporte revisaste?</h2><div class="two"><div><label for="transport">Empresa de transporte *</label><input id="transport" name="transport" list="transportOptions" placeholder="Selecciona o escribe el transporte" maxlength="100" required><datalist id="transportOptions">{% for t in transports %}<option value="{{t}}">{% endfor %}</datalist></div><div><label for="worked">Fecha del trabajo *</label><input id="worked" type="date" value="{{today}}" required></div></div><div style="max-width:270px;margin-top:12px"><label for="pallets">Pallets revisados (opcional)</label><input id="pallets" type="number" min="0" max="1000000" inputmode="numeric" placeholder="Cantidad"></div><p class="note">Si revisaste dos transportes en el mismo día, guarda un registro por transporte. En el dashboard ambos aparecerán agrupados bajo tu nombre y fecha.</p></section>
<section class="panel orange"><div class="section-title"><h2>3. Cajas MERMA — por daño</h2><span class="amount" id="mermaTotal">0 cajas</span></div><p class="muted">Solo cajas dañadas, sucias o contaminadas.</p><div class="nums" id="mermaInputs">
<div class="num-tile"><label for="sucio">Sucias</label><input id="sucio" data-group="merma" type="number" min="0" max="1000000" inputmode="numeric" placeholder="0"></div>
<div class="num-tile"><label for="excremento">Con heces</label><input id="excremento" data-group="merma" type="number" min="0" max="1000000" inputmode="numeric" placeholder="0"></div>
<div class="num-tile"><label for="pisadas">Pisadas</label><input id="pisadas" data-group="merma" type="number" min="0" max="1000000" inputmode="numeric" placeholder="0"></div>
<div class="num-tile"><label for="grasa">Con grasa</label><input id="grasa" data-group="merma" type="number" min="0" max="1000000" inputmode="numeric" placeholder="0"></div>
<div class="num-tile"><label for="oxido">Con óxido</label><input id="oxido" data-group="merma" type="number" min="0" max="1000000" inputmode="numeric" placeholder="0"></div>
<div class="num-tile"><label for="plaga">Con plaga</label><input id="plaga" data-group="merma" type="number" min="0" max="1000000" inputmode="numeric" placeholder="0"></div>
<div class="num-tile"><label for="otros">Otros daños</label><input id="otros" data-group="merma" type="number" min="0" max="1000000" inputmode="numeric" placeholder="0"></div></div><p class="note">La merma se suma automáticamente. Cuenta cada caja una sola vez en su motivo principal.</p></section>
<section class="panel purple"><div class="section-title"><h2>4. Cajas RECHAZADAS — por color</h2><span class="amount" id="rejectTotal">0 cajas</span></div><p class="muted">Estas cajas son diferentes de las de Merma.</p><div class="nums">
<div class="num-tile"><label for="azul">Azules</label><input id="azul" data-group="rejected" type="number" min="0" max="1000000" inputmode="numeric" placeholder="0"></div>
<div class="num-tile"><label for="morado">Moradas</label><input id="morado" data-group="rejected" type="number" min="0" max="1000000" inputmode="numeric" placeholder="0"></div>
<div class="num-tile"><label for="verde">Verdes</label><input id="verde" data-group="rejected" type="number" min="0" max="1000000" inputmode="numeric" placeholder="0"></div>
<div class="num-tile"><label for="negro">Negras</label><input id="negro" data-group="rejected" type="number" min="0" max="1000000" inputmode="numeric" placeholder="0"></div></div></section>
<section class="panel"><label for="notes">Observaciones (opcional)</label><textarea id="notes" rows="2" maxlength="900" placeholder="Ej. Embalaje mojado, cajas aplastadas..."></textarea>
<p class="note">El sistema registrará automáticamente la hora de guardado (Perú) y el usuario de CIANSE PRO: <strong>{{username}}</strong>. No puedes modificar esa hora manualmente.</p></section>
<div id="message" role="status" aria-live="polite"></div>
<div class="summary"><div class="totals"><div class="totalbox"><small>Merma</small><b id="sMerma">0</b></div><div class="totalbox"><small>Rechazadas</small><b id="sRech">0</b></div><div class="totalbox"><small>Total</small><b id="sTotal">0</b></div><button id="send" type="submit">Guardar registro diario</button></div></div>
</form>
<section class="panel"><h2>Últimos registros guardados</h2><p class="muted">Muestra quién realizó el ingreso y la hora real de registro.</p><div id="recent">Cargando…</div></section></main>
<script>const csrf={{csrf|tojson}},today={{today|tojson}},Q=x=>document.getElementById(x),keysM=['sucio','excremento','pisadas','grasa','oxido','plaga','otros'],keysR=['azul','morado','verde','negro'],num=x=>Number(Q(x).value||0),fmt=x=>Number(x).toLocaleString('es-PE');
function totals(){const m=keysM.reduce((s,k)=>s+num(k),0),r=keysR.reduce((s,k)=>s+num(k),0);Q('mermaTotal').textContent=fmt(m)+' cajas';Q('rejectTotal').textContent=fmt(r)+' cajas';Q('sMerma').textContent=fmt(m);Q('sRech').textContent=fmt(r);Q('sTotal').textContent=fmt(m+r);return {m,r}}
for(const el of document.querySelectorAll('[data-group]'))el.addEventListener('input',totals);
Q('dailyForm').onsubmit=async e=>{e.preventDefault();const {m,r}=totals();const box=Q('message'),button=Q('send');box.className='';box.textContent='';if(m+r===0){box.textContent='Ingresa al menos una cantidad antes de guardar';box.className='alert error';box.scrollIntoView({behavior:'smooth'});return}const payload={first_name:Q('first').value.trim(),last_name:Q('last').value.trim(),worked_on:Q('worked').value,transport:Q('transport').value.trim(),pallets:Q('pallets').value||null,notes:Q('notes').value,merma:Object.fromEntries(keysM.map(k=>[k,Q(k).value||0])),rejected:Object.fromEntries(keysR.map(k=>[k,Q(k).value||0]))};button.disabled=true;button.textContent='Guardando…';try{const response=await fetch('/api/seleccion-trabajadores/registrar',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw Error(data.error||'No se pudo guardar');box.className='alert';box.textContent='✓ Guardado: '+data.worker+' · '+data.transport+' · Merma '+fmt(data.merma)+' · Rechazadas '+fmt(data.rejected)+' · '+new Date(data.registered_at).toLocaleString('es-PE',{dateStyle:'short',timeStyle:'medium',timeZone:'America/Lima'})+' · Registrado por '+data.recorded_by;Q('dailyForm').reset();Q('worked').value=today;Q('first').value=payload.first_name;Q('last').value=payload.last_name;totals();loadRecent();box.scrollIntoView({behavior:'smooth'})}catch(err){box.className='alert error';box.textContent=err.message}finally{button.disabled=false;button.textContent='Guardar registro diario'}};
async function loadRecent(){try{const res=await fetch('/api/seleccion-trabajadores/registro-reciente');const d=await res.json();if(!res.ok)throw Error(d.error||'No se pudo cargar');const root=Q('recent');root.replaceChildren();if(!d.entries.length){root.textContent='Todavía no hay registros hechos desde el formulario.';return}for(const item of d.entries.slice(0,12)){const row=document.createElement('div');row.className='recent-row';const left=document.createElement('div');const top=document.createElement('strong');top.textContent=item.worker+' · '+item.transport;const sub=document.createElement('small');sub.textContent=item.worked_on+' · Registró '+(item.recorded_by||'Usuario anterior')+' · '+new Date(item.created_at).toLocaleString('es-PE',{dateStyle:'short',timeStyle:'short',timeZone:'America/Lima'});left.append(top,sub);const right=document.createElement('strong');right.textContent='M '+fmt(item.malo)+' / R '+fmt(['azul','morado','verde','negro'].reduce((n,k)=>n+Number(item.selected_colors?.[k]||0),0));row.append(left,right);root.append(row)}}catch(err){Q('recent').textContent='No se pudo cargar el historial: '+err.message}}loadRecent();
</script></body></html>'''
