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
        defects={k:integer(request.form.get(k)) for k in ('excremento','pisadas','grasa','oxido','plaga')}
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
        'defects objeto con excremento,pisadas,grasa,oxido,plaga (enteros o null), '
        'selected_colors objeto con azul,morado,verde,negro (enteros o null), '
        'uncertain (boolean), notes (texto breve). '
        'Un guion indica dato ausente, no cero. No inventes. '
        'No sumes malo con defectos o colores: pueden solaparse. '
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
        except Exception:
            return jsonify(error='No se pudo leer una de las fotos automáticamente. No se guardó información parcial.'),502
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
                defects={k:checked(raw_defects.get(k)) for k in ('excremento','pisadas','grasa','oxido','plaga')}
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

PAGE=r"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CIANSE · Selección por trabajador</title>
<style>body{font:15px system-ui;margin:0;background:#f3f6f5;color:#142d2c}main{max-width:1200px;margin:24px auto;padding:0 16px}header{background:#063f38;color:white;padding:18px}header a{color:white}h1{font-size:24px}section{background:white;border:1px solid #d8e5e1;border-radius:12px;padding:18px;margin:16px 0}label{display:block;font-weight:600;margin:9px 0 4px}input,select,textarea{box-sizing:border-box;width:100%;padding:10px;border:1px solid #b9ccca;border-radius:7px;font:inherit}input[type=file]{padding:8px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px}.btn{background:#086858;color:#fff;border:0;border-radius:8px;padding:12px 20px;font-weight:700;cursor:pointer}.muted{color:#5b6a67;font-size:13px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;border-bottom:1px solid #e1e9e6;padding:9px;white-space:nowrap}.scroll{overflow:auto}.photo{display:inline-block;margin:4px;color:#086858}.err{color:#a22}summary{cursor:pointer}</style>
<header><a href="/">← Volver al Kardex</a><h1>Selección de cajas · Reportes por trabajador</h1><div>Registro diario de condiciones de llegada, daños y colores seleccionados</div></header>
<main><section><h2>Cargar fotografías automáticamente</h2><p class="muted">Selecciona el nombre una vez y adjunta hasta 8 fotos. Una fotografía equivale a una hoja de un transporte. El sistema transcribe sin introducir cantidades manualmente. Las cifras dudosas se marcan para revisión.</p><form id="autoForm"><label>Trabajador</label><input name="worker" placeholder="Karencita" required><label>Fotografías del cuaderno</label><input type="file" name="photos" accept="image/jpeg,image/png" multiple required><p><button class="btn">Leer fotografías automáticamente</button></p><div id="autoStatus" aria-live="polite"></div></form></section><section><h2>Agregar reporte</h2><p class="muted">Un registro por hoja/foto, fecha, trabajador y transporte. Los campos sin dato quedan vacíos; no se interpretan como cero. No modifica stock ni producción.</p>
<form id="form"><div class="grid"><div><label>Trabajador</label><input name="worker" placeholder="Karencita" required></div><div><label>Fecha del registro</label><input type="date" name="worked_on" required></div><div><label>Transporte</label><input name="transport" placeholder="DYR / Silveira / Chávez" required></div><div><label>Pallets recibidos</label><input type="number" min="0" name="pallets"></div><div><label>Cajas marcadas como «malo»</label><input type="number" min="0" name="malo"></div></div>
<h3>Estado de las cajas al recibirlas</h3><div class="grid"><div><label>Excremento</label><input type="number" min="0" name="excremento"></div><div><label>Pisadas / huellas</label><input type="number" min="0" name="pisadas"></div><div><label>Grasa</label><input type="number" min="0" name="grasa"></div><div><label>Óxido</label><input type="number" min="0" name="oxido"></div><div><label>Plaga</label><input type="number" min="0" name="plaga"></div></div>
<h3>Cajas rechazadas por color (si se anotó)</h3><div class="grid"><div><label>Azul</label><input type="number" min="0" name="azul"></div><div><label>Morado</label><input type="number" min="0" name="morado"></div><div><label>Verde</label><input type="number" min="0" name="verde"></div><div><label>Negro</label><input type="number" min="0" name="negro"></div></div>
<label>Observaciones</label><textarea name="notes" rows="2"></textarea><label>Fotos de cuaderno (máximo 8, JPG/PNG)</label><input type="file" name="photos" accept="image/jpeg,image/png" multiple><p><button class="btn" type="submit">Guardar registro y fotos</button></p><div id="save"></div></form></section>
<section><h2>Reportes guardados</h2><div class="grid"><div><label>Filtrar trabajador</label><input id="workerFilter" placeholder="Todos"></div><div><label>Filtrar día</label><input id="dayFilter" type="date"></div></div><p id="totals"></p><div class="scroll"><table><thead><tr><th>Fecha</th><th>Trabajador</th><th>Transporte</th><th>Pallets</th><th>Malo</th><th>Excremento</th><th>Pisadas</th><th>Grasa</th><th>Óxido</th><th>Plaga</th><th>Fotos</th></tr></thead><tbody id="rows"></tbody></table></div></section></main>
<script>
const csrf={{ csrf|tojson }};const autoForm=document.getElementById('autoForm');autoForm.onsubmit=async e=>{e.preventDefault();const status=document.getElementById('autoStatus');const btn=autoForm.querySelector('button');btn.disabled=true;status.textContent='Leyendo fotografías…';try{const r=await fetch('/api/seleccion-trabajadores/auto',{method:'POST',headers:{'X-CSRF-Token':csrf},body:new FormData(autoForm)});const data=await r.json();if(!r.ok)throw Error(data.error||'No se pudo leer');status.textContent=data.reports.length+' fotografías leídas. '+data.reports.filter(x=>x.status==='saved').length+' guardadas y '+data.reports.filter(x=>x.status==='review').length+' pendientes de revisión. No se alteró el Kardex.';autoForm.reset()}catch(err){status.textContent=err.message}finally{btn.disabled=false}};let all=[];const q=id=>document.getElementById(id);function el(tag,text){const n=document.createElement(tag);n.textContent=text??'';return n}
async function load(){const r=await fetch('/api/seleccion-trabajadores');if(!r.ok)throw Error('No se pudieron leer reportes');all=(await r.json()).reports;render()}
function render(){let list=all.filter(x=>x.worker.toLowerCase().includes(q('workerFilter').value.toLowerCase())&&(!q('dayFilter').value||q('dayFilter').value===x.worked_on));q('rows').replaceChildren();let pallets=0,defects=0;for(const x of list){pallets+=x.pallets||0;defects+=Object.values(x.defects||{}).reduce((s,v)=>s+(v||0),0);const tr=el('tr');for(const v of [x.worked_on,x.worker,x.transport,x.pallets,x.malo,x.defects?.excremento,x.defects?.pisadas,x.defects?.grasa,x.defects?.oxido,x.defects?.plaga])tr.append(el('td',v==null?'—':v));const td=el('td');const btn=el('button',String(x.photos));btn.type='button';btn.onclick=async()=>{const p=await(await fetch('/api/seleccion-trabajadores/'+x.id+'/fotos')).json();td.replaceChildren();for(const f of p.photos){const a=el('a',f.filename);a.href='/api/seleccion-trabajadores/foto/'+f.id;a.target='_blank';a.className='photo';td.append(a)}};td.append(btn);tr.append(td);q('rows').append(tr)}q('totals').textContent=list.length+' reportes · '+pallets+' pallets · '+defects+' incidencias clasificadas (no incluye «malo» para evitar doble conteo)'}
q('workerFilter').oninput=render;q('dayFilter').oninput=render;
q('form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,output=q('save');output.textContent='Guardando…';try{const r=await fetch('/api/seleccion-trabajadores',{method:'POST',headers:{'X-CSRF-Token':csrf},body:new FormData(form)});const d=await r.json();if(!r.ok)throw Error(d.error||'No se pudo guardar');output.textContent='Reporte guardado correctamente';form.reset();await load()}catch(err){output.textContent=err.message;output.className='err'}};load().catch(e=>q('totals').textContent=e.message)
</script></html>"""
