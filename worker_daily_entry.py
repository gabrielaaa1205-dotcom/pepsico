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

PAGE='''TO_BE_REPLACED'''
