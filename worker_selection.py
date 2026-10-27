"""Worker box-selection incident reports. Independent of stock, production and billing."""
import datetime, io, json, hashlib
from flask import Blueprint, request, session, abort, jsonify, render_template_string, send_file
from server import db, check_csrf
bp_worker=Blueprint('worker_reports',__name__)
def allowed():
    if not session.get('uid'):abort(401)
def setup(c):
    c.execute("""CREATE TABLE IF NOT EXISTS worker_selection_reports (
      id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      worker TEXT NOT NULL, worked_on DATE NOT NULL, transport TEXT NOT NULL,
      pallets INTEGER, malo INTEGER, defects JSONB NOT NULL,
      selected_colors JSONB NOT NULL DEFAULT '{}'::jsonb,
      notes TEXT NOT NULL DEFAULT '', creator BIGINT REFERENCES users(id),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    c.execute("""CREATE TABLE IF NOT EXISTS worker_selection_photos (
      id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      report_id BIGINT NOT NULL REFERENCES worker_selection_reports(id) ON DELETE CASCADE,
      content BYTEA NOT NULL, mimetype TEXT NOT NULL, filename TEXT NOT NULL,
      digest TEXT NOT NULL, UNIQUE(report_id,digest))""")
def integer(v):
    if v is None or str(v).strip()=='':return None
    n=int(v)
    if n<0 or n>1000000:raise ValueError('Cantidad fuera de rango')
    return n
@bp_worker.route('/seleccion-trabajadores')
def page():
    allowed()
    return render_template_string(PAGE,csrf=session['csrf'])
@bp_worker.route('/api/seleccion-trabajadores',methods=['GET','POST'])
def collection():
    allowed()
    if request.method=='GET':
        with db() as c:
            setup(c)
            rows=c.execute("""SELECT r.id,r.worker,r.worked_on,r.transport,r.pallets,r.malo,r.defects,r.selected_colors,r.notes,r.created_at,
              (SELECT count(*) FROM worker_selection_photos p WHERE p.report_id=r.id) photos
              FROM worker_selection_reports r ORDER BY r.worked_on DESC,r.id DESC LIMIT 1000""").fetchall()
        return jsonify(reports=[dict(x,worked_on=x['worked_on'].isoformat(),created_at=x['created_at'].isoformat()) for x in rows])
    check_csrf()
    if session.get('role') not in ('admin','supervisor','operario'):abort(403)
    try:
        worker=request.form.get('worker','').strip()[:100]
        transport=request.form.get('transport','').strip()[:140]
        date=datetime.date.fromisoformat(request.form['worked_on'])
        if not worker or not transport:return jsonify(error='Indica trabajador y transporte'),400
        pallets=integer(request.form.get('pallets'))
        malo=integer(request.form.get('malo'))
        defects={k:integer(request.form.get(k)) for k in ('sucio','excremento','pisadas','grasa','oxido','plaga')}
        selected={k:integer(request.form.get(k)) for k in ('azul','morado','verde','negro')}
        notes=request.form.get('notes','').strip()[:1500]
        photos=request.files.getlist('photos')
        if len(photos)>8:return jsonify(error='Máximo 8 fotos por registro'),400
        attachments=[]
        for photo in photos:
            if not photo.filename:continue
            data=photo.read(3*1024*1024+1)
            if len(data)>3*1024*1024:return jsonify(error='Máximo 3 MB por foto'),400
            mime='image/jpeg' if data.startswith(b'\xff\xd8\xff') else 'image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else None
            if not mime:return jsonify(error='Solo fotos JPG o PNG'),400
            attachments.append((data,mime,photo.filename[:180],hashlib.sha256(data).hexdigest()))
    except (ValueError,KeyError) as e:return jsonify(error='Revisa las cantidades y la fecha'),400
    with db() as c:
        setup(c)
        item=c.execute("""INSERT INTO worker_selection_reports(worker,worked_on,transport,pallets,malo,defects,selected_colors,notes,creator)
             VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s) RETURNING id""",
             (worker,date,transport,pallets,malo,json.dumps(defects),json.dumps(selected),notes,session['uid'])).fetchone()
        for data,mime,name,digest in attachments:
            c.execute("""INSERT INTO worker_selection_photos(report_id,content,mimetype,filename,digest)
                  VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",(item['id'],data,mime,name,digest))
        c.execute("INSERT INTO audit(actor,operation,object_key) VALUES (%s,%s,%s)",(session['uid'],'CREATE_WORKER_SELECTION',str(item['id'])))
    return jsonify(ok=True,id=item['id']),201
@bp_worker.route('/api/seleccion-trabajadores/<int:report_id>/fotos')
def list_photos(report_id):
    allowed()
    with db() as c:
        setup(c)
        rows=c.execute("SELECT id,filename FROM worker_selection_photos WHERE report_id=%s ORDER BY id",(report_id,)).fetchall()
    return jsonify(photos=rows)
@bp_worker.route('/api/seleccion-trabajadores/foto/<int:photo_id>')
def get_photo(photo_id):
    allowed()
    with db() as c:
        setup(c)
        p=c.execute("SELECT content,mimetype,filename FROM worker_selection_photos WHERE id=%s",(photo_id,)).fetchone()
    if not p:abort(404)
    return send_file(io.BytesIO(bytes(p['content'])),mimetype=p['mimetype'],download_name=p['filename'])

@bp_worker.route('/api/seleccion-trabajadores/auto',methods=['POST'])
def automatic_upload():
    """Photo-only intake. Parse with a configured vision model; preserve unverified images."""
    allowed()
    check_csrf()
    if session.get('role') not in ('admin','supervisor','operario'):abort(403)
    import os,base64,urllib.request,urllib.error
    worker=request.form.get('worker','').strip()[:100]
    files=request.files.getlist('photos')
    if not worker or not files:return jsonify(error='Indica trabajador y adjunta las fotos'),400
    if len(files)>8:return jsonify(error='Máximo 8 fotos por carga'),400
    images=[]
    for p in files:
        content=p.read(3*1024*1024+1)
        if len(content)>3*1024*1024:return jsonify(error='Máximo 3 MB por foto'),400
        mime='image/jpeg' if content.startswith(bytes.fromhex('ffd8ff')) else 'image/png' if content.startswith(bytes.fromhex('89504e470d0a1a0a')) else None
        if not mime:return jsonify(error='Se admiten JPG y PNG'),400
        images.append((p.filename[:180],content,mime))
    api_key=os.environ.get('OPENAI_API_KEY')
    if not api_key:return jsonify(error='La lectura automática de manuscritos aún no está habilitada en el servidor. Se necesita configurar un proveedor de visión; no se han alterado los datos.'),503
    instructions=('Lee SOLO la foto manuscrita de selección de cajas. Devuelve un objeto JSON con: '
        'worked_on en YYYY-MM-DD o null, transport (texto o null), pallets (entero o null), malo (entero o null), '
        'defects objeto con sucio,excremento,pisadas,grasa,oxido,plaga (enteros o null), '
        'selected_colors objeto con azul,morado,verde,negro (enteros o null), '
        'uncertain (boolean), notes (texto breve). '
        'Un guion indica dato ausente, no cero. No inventes. '
        'Los defectos excremento, pisadas, grasa, oxido y plaga describen el segmento MALO. Los rechazos por color son OTRO segmento completamente separado de MALO. No confundas los conteos. '
        'Transportes DYR, SILVEIRA, CHAVEZ, XIONFI son posibles, no los presupongas. '
        'Si un numero es dudoso marca uncertain true y usa null.')
    prepared=[]
    for filename,content,mime in images:
        payload={'model':os.environ.get('WORKER_VISION_MODEL','gpt-4.1-mini'),
          'temperature':0,'response_format':{'type':'json_object'},
          'messages':[{'role':'system','content':instructions},
            {'role':'user','content':[{'type':'text','text':'Extrae un único registro de esta hoja.'},
               {'type':'image_url','image_url':{'url':'data:'+mime+';base64,'+base64.b64encode(content).decode()}}]}],
          'max_tokens':450}
        req=urllib.request.Request('https://api.openai.com/v1/chat/completions',
          data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+api_key},method='POST')
        try:
            with urllib.request.urlopen(req,timeout=35) as response:
                result=json.loads(response.read(150000))
            parsed=json.loads(result['choices'][0]['message']['content'])
        except urllib.error.HTTPError as err:
            # Return a useful category without exposing credentials or provider payload.
            status=err.code
            reason={400:'Formato de solicitud rechazado por el proveedor de IA',401:'La clave de API no es válida o fue revocada',403:'La cuenta API no tiene permiso para usar el modelo',404:'El modelo de lectura de imágenes no está disponible',413:'La fotografía es demasiado grande para el proveedor',429:'La API no tiene saldo, alcanzó su cuota o su límite de solicitudes',500:'El proveedor de IA tuvo un error interno',502:'El proveedor de IA no respondió correctamente',503:'El proveedor de IA está temporalmente no disponible'}.get(status,'Error de comunicación con el proveedor de IA')
            return jsonify(error=reason+' (código '+str(status)+'). No se guardaron datos parciales.'),502
        except (urllib.error.URLError,TimeoutError):
            return jsonify(error='No se logró conectar con la API de lectura. Revisa la conectividad del servidor.'),502
        except (ValueError,KeyError,TypeError):
            return jsonify(error='La IA respondió con un formato inesperado. No se guardaron datos parciales.'),502
        prepared.append((filename,content,mime,parsed))
    created=[]
    with db() as c:
        setup(c)
        for filename,content,mime,p in prepared:
            d=p.get('worked_on')
            uncertain=bool(p.get('uncertain'))
            try:day=datetime.date.fromisoformat(d) if d else None
            except ValueError:day=None
            transport=str(p.get('transport') or '').strip()
            # Never substitute an invented date or transporter. Unclear reports are review-only.
            if not day or not transport:uncertain=True
            c.execute("""CREATE TABLE IF NOT EXISTS worker_selection_auto_reviews(
              id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
              worker TEXT NOT NULL, filename TEXT NOT NULL, photo BYTEA NOT NULL,
              mime TEXT NOT NULL, parsed JSONB NOT NULL,
              status TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now())""")
            status='review' if uncertain else 'parsed'
            if not uncertain:
                def checked(v):
                    try:return integer(v)
                    except (ValueError,TypeError):return None
                raw_defects=p.get('defects') or {}
                raw_colors=p.get('selected_colors') or {}
                defects={k:checked(raw_defects.get(k)) for k in ('sucio','excremento','pisadas','grasa','oxido','plaga')}
                colors={k:checked(raw_colors.get(k)) for k in ('azul','morado','verde','negro')}
                # A recognized image can be submitted twice. Do not count it twice.
                digest=hashlib.sha256(content).hexdigest()
                prior=c.execute("""SELECT r.id FROM worker_selection_reports r
                    JOIN worker_selection_photos i ON i.report_id=r.id
                    WHERE i.digest=%s LIMIT 1""",(digest,)).fetchone()
                if prior:
                    created.append({'id':prior['id'],'filename':filename,'status':'duplicate','data':p})
                    continue
                report=c.execute("""INSERT INTO worker_selection_reports
                    (worker,worked_on,transport,pallets,malo,defects,selected_colors,notes,creator)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s) RETURNING id""",
                    (worker,day,transport,checked(p.get('pallets')),checked(p.get('malo')),
                     json.dumps(defects),json.dumps(colors),str(p.get('notes') or '')[:1500],session['uid'])).fetchone()
                c.execute("""INSERT INTO worker_selection_photos(report_id,content,mimetype,filename,digest)
                    VALUES (%s,%s,%s,%s,%s)""",(report['id'],content,mime,filename,digest))
                created.append({'id':report['id'],'filename':filename,'status':'saved','data':p})
                continue
            row=c.execute("""INSERT INTO worker_selection_auto_reviews(worker,filename,photo,mime,parsed,status)
                 VALUES (%s,%s,%s,%s,%s::jsonb,%s) RETURNING id""",
              (worker,filename,content,mime,json.dumps(p),status)).fetchone()
            created.append({'id':row['id'],'filename':filename,'status':status,'data':p})
    return jsonify(ok=True,reports=created,note='Lectura automática almacenada para revisión; no actualiza stock ni facturación.')

PAGE=r"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CIANSE PRO · Selección</title>
<style>
:root{--deep:#103f38;--green:#087d65;--line:#e1e8e6;--muted:#62766f;--paper:#f4f7f6;--red:#b74b3f}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:#18372f;font:14px/1.5 system-ui,-apple-system,sans-serif}button,input,select{font:inherit}button{cursor:pointer}header{padding:16px clamp(16px,3vw,46px);background:var(--deep);color:white;display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}header a{color:#c7eee2;text-decoration:none}.brand{font-size:21px;font-weight:800;letter-spacing:-.4px}.sub{color:#bed8d0;font-size:12px}main{max-width:1440px;margin:0 auto;padding:24px clamp(14px,3vw,40px) 60px}.topline{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}h1{font-size:27px;margin:0;letter-spacing:-.6px}h2{font-size:18px;margin:0 0 14px}h3{font-size:15px;margin:14px 0 8px}p{margin:5px 0 12px}.muted{color:var(--muted)}.panel{background:white;border:1px solid var(--line);border-radius:15px;padding:20px;box-shadow:0 3px 14px #152e2610}.filters{display:flex;gap:12px;flex-wrap:wrap;align-items:end;margin:20px 0}.filters>*{flex:1;min-width:155px}label{font-size:12px;font-weight:700;color:#506860;display:block;margin-bottom:5px}input,select,textarea{width:100%;min-height:42px;border:1px solid #d5e1dc;border-radius:9px;background:white;padding:9px 11px;color:#18372f}input:focus,select:focus{outline:2px solid #a4dcd0;outline-offset:1px}.btn{background:var(--green);color:white;border:0;border-radius:10px;padding:11px 16px;font-weight:750}.btn.secondary{background:#e5f1ed;color:#145b4a}.btn.ghost{background:#fff;border:1px solid #cfdbd5;color:#245446}.btn:disabled{opacity:.6}.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.kpi{padding:19px;border:1px solid var(--line);background:white;border-radius:14px}.kpi b{font-size:30px;letter-spacing:-1px;display:block;font-variant-numeric:tabular-nums}.kpi small{color:var(--muted);font-weight:700}.kpi em{font-size:12px;color:var(--muted);font-style:normal}.kpi.malo{border-top:4px solid #c77b35}.kpi.rech{border-top:4px solid #8d70b6}.kpi.total{border-top:4px solid var(--green)}.layout{display:grid;grid-template-columns:1.4fr 1fr;gap:15px;margin-top:16px}.barrow{display:grid;grid-template-columns:112px 1fr 54px;gap:9px;align-items:center;margin:10px 0}.track{background:#edf2f0;border-radius:15px;height:13px;overflow:hidden}.fill{background:#368b78;height:100%;border-radius:15px}.fill.amber{background:#d4a04e}.fill.purple{background:#a38ac5}.barlabel{color:#536860;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.barval{text-align:right;font-weight:750;font-variant-numeric:tabular-nums}.sectionhead{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin:22px 0 12px}.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:14px;background:white}table{border-collapse:collapse;width:100%;min-width:1050px}th{text-align:left;color:#61776e;font-size:11px;letter-spacing:.4px;text-transform:uppercase;background:#f8faf9;position:sticky;top:0}th,td{padding:12px 13px;border-bottom:1px solid #e9eeeb;white-space:nowrap}td{font-variant-numeric:tabular-nums}tr:hover td{background:#fafcfb}.pill{display:inline-block;border-radius:50px;background:#f4e9d9;color:#895819;padding:3px 9px;font-weight:750}.pill.violet{background:#efe9f7;color:#76539a}.pill.green{background:#dff1eb;color:#146752}.two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.four{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.actions{display:flex;gap:8px;flex-wrap:wrap}.drop{padding:24px;border:2px dashed #a9c8ba;border-radius:12px;background:#f6faf8;text-align:center}.drop input{max-width:390px;border:0}.message{margin-top:12px;color:#266353;white-space:pre-wrap}.error{color:#b43f3f}.hide{display:none!important}.details{margin-top:15px}.photoBtn{border:0;background:#e3f2ed;color:#086755;border-radius:8px;padding:6px 10px}.segment{color:#66786e;font-size:12px}.timechips{display:flex;gap:6px;flex-wrap:wrap}.timechips button{border:1px solid #cddbd4;border-radius:30px;padding:7px 12px;background:white;color:#416358}.timechips button.active{background:#155b49;color:white}.empty{padding:30px;text-align:center;color:#7a8d82}@media(max-width:1000px){.kpis{grid-template-columns:repeat(2,1fr)}.layout{grid-template-columns:1fr}}@media(max-width:550px){.kpis{gap:8px}.kpi{padding:13px}.kpi b{font-size:25px}.two,.four{grid-template-columns:1fr 1fr}.panel{padding:15px}.filters>*{min-width:135px}.barrow{grid-template-columns:92px 1fr 42px}h1{font-size:22px}}
.report-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:14px;margin-top:12px}.quality-card{background:#fff;border:1px solid #dce7e2;border-radius:13px;padding:17px}.quality-columns{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:10px}.quality-section{background:#fff4e5;border-radius:10px;padding:10px}.quality-section.rejected{background:#f3ecfa}.quality-line{display:flex;justify-content:space-between;font-size:12px;padding:3px 0;border-bottom:1px solid #0001}.quality-heading{font-weight:800;font-size:19px}.quality-bottom{margin-top:12px;font-weight:700}#legacyTable{display:none}.date-group{grid-column:1/-1;margin:6px 0 12px}.date-heading{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;padding:13px 16px;background:#eaf4f0;border-left:4px solid #087d65;border-radius:10px;margin-bottom:12px}.date-heading strong{text-transform:capitalize;font-size:17px}.date-heading span{font-size:13px;color:#3d7162}.date-group>.report-cards{margin:0}@media(max-width:550px){.report-cards{grid-template-columns:1fr}} </style></head><body>
<header><div><div class="brand">CIANSE <span style="color:#9fd8c7">PRO</span> <span style="font-size:13px;font-weight:500">/ Calidad</span></div><div class="sub">Selección de cajas · Control operativo por trabajador</div></div><a href="/">← Volver al Kardex</a></header>
<main><div class="topline"><div><h1>Dashboard de selección · 11 trabajadoras</h1><p class="muted">Seguimiento diario de cajas en mal estado y rechazadas, sin mezclar categorías.</p></div><button class="btn" id="openUpload">＋ Cargar fotografías</button></div>
<div class="filters panel"><div><label>Trabajador</label><select id="workerFilter"><option value="">Todos los trabajadores</option></select></div><div><label>Desde</label><input id="from" type="date"></div><div><label>Hasta</label><input id="to" type="date"></div><div style="flex:1.2"><label>Periodo rápido</label><div class="timechips"><button data-period="all" class="active">Todo</button><button data-period="7">7 días</button><button data-period="30">30 días</button></div></div><button id="clear" class="btn ghost">Limpiar</button></div>
<div class="kpis"><div class="kpi"><small>REPORTES</small><b id="kReports">—</b><em id="kWorkers">De 11 trabajadoras del equipo</em></div><div class="kpi malo"><small>MERMA · ESTADO DE LLEGADA</small><b id="kMalo">—</b><em>Sucio, excremento, pisadas, grasa, óxido y plaga</em></div><div class="kpi rech"><small>RECHAZADAS · POR COLOR</small><b id="kRech">—</b><em>Azul, morado, verde y negro</em></div><div class="kpi total"><small>TOTAL · MERMA + RECHAZADAS</small><b id="kTotal">—</b><em id="kPallets">Pallets registrados</em></div></div>
<div class="layout"><section class="panel"><div class="sectionhead" style="margin:0 0 12px"><h2>Producción de reportes por día</h2><span class="muted">Merma / Rechazadas</span></div><div id="dailyBars"></div></section><section class="panel"><h2>Motivos de merma</h2><p class="segment">Desglose de MERMA · no sumar al total otra vez</p><div id="defectBars"></div></section></div>
<div class="layout"><section class="panel"><h2>Rechazadas por color</h2><div id="colorBars"></div></section><section class="panel"><h2>Trabajadores</h2><div id="workerBars"></div></section></div>
<div class="sectionhead"><div><h2 style="margin:0">Detalle de reportes</h2><div class="muted" id="countLabel"></div></div><button class="btn secondary" id="export">↓ Exportar CSV</button></div>
<div class="tablewrap" id="legacyTable"><table><thead><tr><th>Fecha</th><th>Trabajador</th><th>Transporte</th><th>Pallets</th><th>MERMA</th><th>Sucio</th><th>Excremento</th><th>Pisadas</th><th>Grasa</th><th>Óxido</th><th>Plaga</th><th>Azul</th><th>Morado</th><th>Verde</th><th>Negro</th><th>RECHAZADAS</th><th>MERMA + RECHAZADAS</th><th>Evidencias</th></tr></thead><tbody id="rows"></tbody></table></div>
<div id="cardsView" class="report-cards"></div><section id="upload" class="panel hide" style="margin-top:18px"><div class="sectionhead" style="margin-top:0"><h2>Lectura automática de fotos</h2><button class="btn ghost" id="closeUpload">Cerrar</button></div><p class="muted">Una foto por hoja. La IA separa Merma de Rechazadas por color. Las lecturas dudosas requieren revisión.</p><form id="autoForm"><div class="two"><div><label>Trabajador</label><input name="worker" required placeholder="Ej. Karencita"></div><div><label>Fotografías (máximo 8)</label><input type="file" name="photos" accept="image/jpeg,image/png" multiple required></div></div><div style="margin-top:13px"><button class="btn" type="submit">Analizar y guardar fotografías</button></div><div class="message" id="autoStatus" role="status"></div></form>
<details class="details"><summary>Ingreso manual opcional</summary><form id="form"><div class="four"><div><label>Trabajador</label><input name="worker" required></div><div><label>Fecha</label><input name="worked_on" type="date" required></div><div><label>Transporte</label><input name="transport" required></div><div><label>Pallets</label><input name="pallets" type="number" min="0"></div></div><h3>Segmento MERMA (Malo)</h3><div class="four"><div><label>Total Merma</label><input name="malo" type="number" min="0"></div><div><label>Sucio</label><input name="sucio" type="number" min="0"></div><div><label>Excremento</label><input name="excremento" type="number" min="0"></div><div><label>Pisadas</label><input name="pisadas" type="number" min="0"></div><div><label>Grasa</label><input name="grasa" type="number" min="0"></div><div><label>Óxido</label><input name="oxido" type="number" min="0"></div><div><label>Plaga</label><input name="plaga" type="number" min="0"></div></div><h3>Segmento Rechazadas · por color</h3><div class="four"><div><label>Azul</label><input name="azul" type="number" min="0"></div><div><label>Morado</label><input name="morado" type="number" min="0"></div><div><label>Verde</label><input name="verde" type="number" min="0"></div><div><label>Negro</label><input name="negro" type="number" min="0"></div></div><label>Observaciones</label><input name="notes"><label>Fotos</label><input name="photos" type="file" accept="image/jpeg,image/png" multiple><p><button class="btn">Guardar registro</button></p><div id="save" class="message"></div></form></details></section>
</main><script>
const csrf={{ csrf|tojson }},q=id=>document.getElementById(id),fmt=n=>Number(n||0).toLocaleString('es-PE');let all=[],view=[];
const val=x=>Number(x)||0, rejects=r=>['azul','morado','verde','negro'].reduce((s,k)=>s+val(r.selected_colors?.[k]),0), total=r=>val(r.malo)+rejects(r);
function cell(text,cls){const c=document.createElement('td');c.textContent=text??'—';if(cls){const z=document.createElement('span');z.className=cls;z.textContent=c.textContent;c.replaceChildren(z)}return c}
function bars(id,items,color){const target=q(id);target.replaceChildren();const top=Math.max(1,...items.map(p=>p[1]));if(!items.length){target.textContent='Sin datos en el periodo seleccionado';target.className='empty';return}target.className='';for(const [name,count] of items){const row=document.createElement('div');row.className='barrow';const label=document.createElement('span');label.className='barlabel';label.textContent=name;const track=document.createElement('div');track.className='track';const fill=document.createElement('div');fill.className='fill '+color;fill.style.width=(count/top*100)+'%';track.append(fill);const v=document.createElement('span');v.className='barval';v.textContent=fmt(count);row.append(label,track,v);target.append(row)}}
function aggregate(items,key,fun){const out=new Map;for(const x of items){const k=key(x);out.set(k,(out.get(k)||0)+fun(x))}return [...out.entries()].sort((a,b)=>b[1]-a[1])}
function render(){view=all.filter(x=>(!q('workerFilter').value||x.worker===q('workerFilter').value)&&(!q('from').value||x.worked_on>=q('from').value)&&(!q('to').value||x.worked_on<=q('to').value));const malo=view.reduce((a,x)=>a+val(x.malo),0),rech=view.reduce((a,x)=>a+rejects(x),0),pallet=view.reduce((a,x)=>a+val(x.pallets),0),missing=view.filter(x=>x.malo==null).length;q('kReports').textContent=fmt(view.length);q('kWorkers').textContent=new Set(view.map(x=>x.worker)).size+' de 11 trabajadoras con reportes';q('kMalo').textContent=fmt(malo);q('kRech').textContent=fmt(rech);q('kTotal').textContent=fmt(malo+rech)+(missing?'*':'');q('kPallets').textContent=fmt(pallet)+' pallets · '+(missing?'*subtotal parcial':'dos segmentos');
const days=aggregate(view,x=>x.worked_on,x=>total(x)).sort((a,b)=>a[0].localeCompare(b[0])).slice(-12);bars('dailyBars',days.flatMap(([d])=>[[d.slice(5).split('-').reverse().join('/')+' Merma',view.filter(x=>x.worked_on===d).reduce((a,x)=>a+val(x.malo),0)],[d.slice(5).split('-').reverse().join('/')+' Rech.',view.filter(x=>x.worked_on===d).reduce((a,x)=>a+rejects(x),0)]]),'');
bars('defectBars',['sucio','excremento','pisadas','grasa','oxido','plaga'].map(k=>[k[0].toUpperCase()+k.slice(1),view.reduce((a,x)=>a+val(x.defects?.[k]),0)]),'amber');
bars('colorBars',['azul','morado','verde','negro'].map(k=>[k[0].toUpperCase()+k.slice(1),view.reduce((a,x)=>a+val(x.selected_colors?.[k]),0)]),'purple');
bars('workerBars',aggregate(view,x=>x.worker,x=>total(x)).slice(0,10),'');
q('rows').replaceChildren();for(const x of view){const tr=document.createElement('tr');for(const v of [x.worked_on,x.worker,x.transport,x.pallets])tr.append(cell(v));tr.append(cell(x.malo==null?'—':fmt(x.malo),'pill'));for(const k of ['sucio','excremento','pisadas','grasa','oxido','plaga'])tr.append(cell(x.defects?.[k]==null?'—':fmt(x.defects[k])));for(const k of ['azul','morado','verde','negro'])tr.append(cell(x.selected_colors?.[k]==null?'—':fmt(x.selected_colors[k])));tr.append(cell(fmt(rejects(x)),'pill violet'),cell(x.malo==null?'—':fmt(total(x)),'pill green'));const td=document.createElement('td'),btn=document.createElement('button');btn.type='button';btn.className='photoBtn';btn.textContent=x.photos+' fotos';btn.onclick=async()=>{const res=await fetch('/api/seleccion-trabajadores/'+x.id+'/fotos');const data=await res.json();td.replaceChildren();for(const p of data.photos||[]){const a=document.createElement('a');a.href='/api/seleccion-trabajadores/foto/'+p.id;a.target='_blank';a.rel='noopener';a.textContent='Ver foto '+p.id+' ';td.append(a)}};td.append(btn);tr.append(td);q('rows').append(tr)}const panel=q('cardsView');panel.replaceChildren();const byDay=new Map();for(const x of view){const day=x.worked_on||'Sin fecha';if(!byDay.has(day))byDay.set(day,[]);byDay.get(day).push(x)}for(const [day,items] of [...byDay].sort((a,b)=>b[0].localeCompare(a[0]))){const section=document.createElement('section');section.className='date-group';const heading=document.createElement('div');heading.className='date-heading';const d=document.createElement('strong');d.textContent=day==='Sin fecha'?day:new Date(day+'T12:00:00').toLocaleDateString('es-PE',{weekday:'long',day:'numeric',month:'long',year:'numeric'});const stats=document.createElement('span');stats.textContent=new Set(items.map(z=>z.worker.trim().toLowerCase())).size+' trabajadoras · '+items.length+' reportes de transporte · Merma '+fmt(items.reduce((n,x)=>n+val(x.malo),0))+' · Rechazadas '+fmt(items.reduce((n,x)=>n+rejects(x),0));heading.append(d,stats);section.append(heading);const dayCards=document.createElement('div');dayCards.className='report-cards';section.append(dayCards);panel.append(section);const grouped=new Map();for(const z of items){const key=(z.worker||'Sin nombre').trim().toLowerCase();if(!grouped.has(key))grouped.set(key,[]);grouped.get(key).push(z)}for(const reports of grouped.values()){const x={worker:reports[0].worker,transport:reports.map(z=>z.transport).join(', '),pallets:reports.every(z=>z.pallets==null)?null:reports.reduce((n,z)=>n+val(z.pallets),0),malo:reports.every(z=>z.malo==null)?null:reports.reduce((n,z)=>n+val(z.malo),0),defects:{},selected_colors:{},photos:reports.reduce((n,z)=>n+val(z.photos),0)};for(const k of ['sucio','excremento','pisadas','grasa','oxido','plaga'])x.defects[k]=reports.reduce((n,z)=>n+val(z.defects?.[k]),0);for(const k of ['azul','morado','verde','negro'])x.selected_colors[k]=reports.reduce((n,z)=>n+val(z.selected_colors?.[k]),0);const card=document.createElement('article');card.className='quality-card';const title=document.createElement('div');title.className='quality-heading';title.textContent=x.worker;const meta=document.createElement('div');meta.className='muted';meta.textContent=reports.length+' reporte(s) · '+(x.pallets??'—')+' pallets';card.append(title,meta);const cols=document.createElement('div');cols.className='quality-columns';const m=document.createElement('div');m.className='quality-section';const r=document.createElement('div');r.className='quality-section rejected';function addLine(box,key,value){const line=document.createElement('div');line.className='quality-line';const a=document.createElement('span');a.textContent=key;const b=document.createElement('strong');b.textContent=value==null?'—':fmt(value);line.append(a,b);box.append(line)}addLine(m,'MERMA',x.malo);for(const [k,label] of [['sucio','Sucias'],['excremento','Heces'],['pisadas','Pisadas'],['grasa','Grasa'],['oxido','Óxido'],['plaga','Plaga']])addLine(m,label,x.defects?.[k]);addLine(r,'RECHAZADAS',rejects(x));for(const [k,label] of [['azul','Azules'],['morado','Moradas'],['verde','Verdes'],['negro','Negras']])addLine(r,label,x.selected_colors?.[k]);cols.append(m,r);card.append(cols);const bottom=document.createElement('div');bottom.className='quality-bottom';bottom.textContent='Total: '+(x.malo==null?'incompleto':fmt(total(x)))+' cajas';card.append(bottom);const details=document.createElement('details');details.style.marginTop='12px';const summary=document.createElement('summary');summary.textContent='Ver desglose por transporte ('+reports.length+')';details.append(summary);for(const z of reports){const line=document.createElement('div');line.className='quality-line';const label=document.createElement('span');label.textContent=z.transport+' · '+(z.pallets??'—')+' pallets';const amount=document.createElement('strong');amount.textContent='Merma '+fmt(z.malo)+' / Rech. '+fmt(rejects(z));line.append(label,amount);details.append(line);if(z.photos){const button=document.createElement('button');button.className='btn secondary';button.textContent='Fotos '+z.transport+' ('+z.photos+')';button.onclick=async()=>{const data=await(await fetch('/api/seleccion-trabajadores/'+z.id+'/fotos')).json();for(const p of data.photos||[]){const a=document.createElement('a');a.href='/api/seleccion-trabajadores/foto/'+p.id;a.target='_blank';a.rel='noopener';a.textContent=' Ver foto '+p.id;details.append(a)}};details.append(button)}}card.append(details);dayCards.append(card)}};q('countLabel').textContent=view.length+' registros'+(missing?' · '+missing+' sin total Malo (totales parciales)':'')}
async function load(){const r=await fetch('/api/seleccion-trabajadores');if(!r.ok)throw Error('No se pudo cargar información');all=(await r.json()).reports;const selected=q('workerFilter').value;const names=[...new Set(all.map(x=>x.worker))].sort();q('workerFilter').replaceChildren(new Option('Todos los trabajadores',''));for(const n of names)q('workerFilter').add(new Option(n,n));q('workerFilter').value=selected;render()}
for(const id of ['workerFilter','from','to'])q(id).addEventListener('change',()=>{document.querySelectorAll('.timechips button').forEach(b=>b.classList.remove('active'));render()});
document.querySelectorAll('.timechips button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.timechips button').forEach(z=>z.classList.remove('active'));b.classList.add('active');if(b.dataset.period==='all'){q('from').value='';q('to').value=''}else{const end=new Date(),start=new Date(end);start.setDate(start.getDate()-Number(b.dataset.period)+1);q('from').value=start.toISOString().slice(0,10);q('to').value=end.toISOString().slice(0,10)}render()});
q('clear').onclick=()=>{q('workerFilter').value='';q('from').value='';q('to').value='';render()};q('openUpload').onclick=()=>{q('upload').classList.remove('hide');q('upload').scrollIntoView({behavior:'smooth'})};q('closeUpload').onclick=()=>q('upload').classList.add('hide');
q('autoForm').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,btn=form.querySelector('button'),status=q('autoStatus');btn.disabled=true;status.textContent='Analizando fotografías…';try{const r=await fetch('/api/seleccion-trabajadores/auto',{method:'POST',headers:{'X-CSRF-Token':csrf},body:new FormData(form)});const d=await r.json();if(!r.ok)throw Error(d.error||'No fue posible leer');status.textContent=d.reports.filter(x=>x.status==='saved').length+' registros guardados, '+d.reports.filter(x=>x.status==='review').length+' por revisar, '+d.reports.filter(x=>x.status==='duplicate').length+' duplicados.';form.reset();await load()}catch(e){status.textContent=e.message;status.className='message error'}finally{btn.disabled=false}};
q('form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,output=q('save');output.textContent='Guardando…';try{const r=await fetch('/api/seleccion-trabajadores',{method:'POST',headers:{'X-CSRF-Token':csrf},body:new FormData(form)});const d=await r.json();if(!r.ok)throw Error(d.error||'No se pudo guardar');output.textContent='Guardado correctamente';form.reset();await load()}catch(e){output.textContent=e.message}};
q('export').onclick=()=>{const headers=['Fecha','Trabajador','Transporte','Pallets','Malo','Sucio','Excremento','Pisadas','Grasa','Oxido','Plaga','Azul','Morado','Verde','Negro','Rechazadas','Total'];const data=view.map(x=>[x.worked_on,x.worker,x.transport,x.pallets,x.malo,...['sucio','excremento','pisadas','grasa','oxido','plaga'].map(k=>x.defects?.[k]),...['azul','morado','verde','negro'].map(k=>x.selected_colors?.[k]),rejects(x),x.malo==null?'':total(x)]);const csv=[headers,...data].map(row=>row.map(v=>'"'+String(v??'').replace(/"/g,'""')+'"').join(';')).join('\r\n');const blob=new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='CIANSE_Seleccion_Reportes.csv';a.click();URL.revokeObjectURL(a.href)};load().catch(e=>{q('countLabel').textContent=e.message});
</script></body></html>"""