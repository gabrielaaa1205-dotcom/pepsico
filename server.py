import os, secrets, json, datetime
from functools import wraps
from flask import Flask, request, session, redirect, url_for, render_template, jsonify, abort, make_response
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.getenv('APP_ENV') == 'production', MAX_CONTENT_LENGTH=15*1024*1024, PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=10))
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError('DATABASE_URL es obligatorio. Configura PostgreSQL.')

def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def initialize():
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS users (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN (\'admin\',\'supervisor\',\'operario\',\'consulta\')), active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ DEFAULT now())')
        c.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value JSONB NOT NULL, revision BIGINT NOT NULL DEFAULT 1, updated_by BIGINT REFERENCES users(id), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        c.execute('CREATE TABLE IF NOT EXISTS audit (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, actor BIGINT REFERENCES users(id), operation TEXT NOT NULL, object_key TEXT, happened_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        admin = c.execute('SELECT id FROM users LIMIT 1').fetchone()
        if not admin:
            username=os.environ.get('INITIAL_ADMIN_USERNAME', '').strip().lower()
            password=os.environ.get('INITIAL_ADMIN_PASSWORD', '')
            if not username or len(password)<12:
                raise RuntimeError('Define INITIAL_ADMIN_USERNAME y INITIAL_ADMIN_PASSWORD (mínimo 12 caracteres).')
            c.execute('INSERT INTO users(username,password_hash,role) VALUES (%s,%s,%s)', (username, generate_password_hash(password), 'admin'))
initialize()

def login_required(fn):
    @wraps(fn)
    def inner(*args, **kwargs):
        if not session.get('uid'):
            if request.path.startswith('/api/'):
                return jsonify(error='Sesión expirada'), 401
            return redirect('/login')
        return fn(*args, **kwargs)
    return inner

def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        @login_required
        def inner(*args, **kwargs):
            if session.get('role') not in roles: abort(403)
            return fn(*args, **kwargs)
        return inner
    return decorator

def check_csrf():
    if request.headers.get('X-CSRF-Token') != session.get('csrf'):
        abort(403)

@app.after_request
def headers(response):
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Cache-Control']='no-store'
    response.headers['Content-Security-Policy']="default-src 'self' https://cdnjs.cloudflare.com; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    return response

@app.route('/health')
def health(): return jsonify(status='ok')

@app.route('/login',methods=['GET','POST'])
def login():
    error=None
    if request.method=='POST':
        user=request.form.get('username','').strip().lower()
        pw=request.form.get('password','')
        with db() as c:
            row=c.execute('SELECT * FROM users WHERE username=%s AND active=true',(user,)).fetchone()
        if row and check_password_hash(row['password_hash'],pw):
            session.clear();session['uid']=row['id'];session['username']=row['username'];session['role']=row['role'];session['csrf']=secrets.token_urlsafe(32);session.permanent=True
            return redirect('/')
        error='Usuario o contraseña incorrectos.'
    return render_template('login.html',error=error)

@app.route('/logout',methods=['POST'])
@login_required
def logout():
    check_csrf();session.clear();return jsonify(ok=True)

@app.route('/')
@login_required
def index():
    return render_template('app.html',cloud_csrf=session['csrf'],cloud_username=session['username'],cloud_role=session['role'])

KEYS={'kx-data','kx-cfg','kx-edits','kx-ui'}
@app.route('/api/state')
@login_required
def state_all():
    with db() as c:
        rows=c.execute('SELECT key,value,revision FROM state').fetchall()
    return jsonify({r['key']:{'value':r['value'],'revision':r['revision']} for r in rows})

@app.route('/api/state/<key>',methods=['PUT','DELETE'])
@role_required('admin','supervisor','operario')
def state_key(key):
    check_csrf()
    if key not in KEYS:abort(404)
    if key=='kx-cfg' and session['role'] not in ('admin','supervisor'): abort(403)
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict) or not isinstance(payload.get('revision'),int) or payload['revision']<0: return jsonify(error='Solicitud inválida'),400
    with db() as c:
        existing=c.execute('SELECT revision FROM state WHERE key=%s FOR UPDATE',(key,)).fetchone()
        old=existing['revision'] if existing else 0
        if old!=payload['revision']:
            return jsonify(error='Otra persona modificó estos datos. Recarga para ver la versión actual.',revision=old),409
        if request.method=='DELETE':
            c.execute('DELETE FROM state WHERE key=%s',(key,))
            revision=0
        else:
            value=payload.get('value')
            if not isinstance(value,(dict,list)): return jsonify(error='Formato inválido'),400
            revision=old+1
            c.execute('INSERT INTO state(key,value,revision,updated_by) VALUES (%s,%s::jsonb,%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,revision=EXCLUDED.revision,updated_by=EXCLUDED.updated_by,updated_at=now()', (key,json.dumps(value,ensure_ascii=False),revision,session['uid']))
        c.execute('INSERT INTO audit(actor,operation,object_key) VALUES (%s,%s,%s)',(session['uid'],request.method,key))
    return jsonify(ok=True,revision=revision)

@app.route('/admin/users',methods=['GET','POST'])
@role_required('admin')
def users():
    error=None; success=None
    if request.method=='POST':
        if request.form.get('csrf') != session['csrf']:abort(403)
        name=request.form.get('username','').strip().lower();pw=request.form.get('password','');role=request.form.get('role','')
        if not name or not name.replace('_','').replace('.','').isalnum() or len(name)>45 or len(pw)<12 or role not in ('admin','supervisor','operario','consulta'):
            error='Nombre, rol o contraseña inválidos. La clave debe tener 12 caracteres como mínimo.'
        else:
            try:
                with db() as c:c.execute('INSERT INTO users(username,password_hash,role) VALUES (%s,%s,%s)',(name,generate_password_hash(pw),role))
                success='Cuenta creada.'
            except psycopg.errors.UniqueViolation:error='Ese usuario ya existe.'
    with db() as c:rows=c.execute('SELECT id,username,role,active,created_at FROM users ORDER BY id').fetchall()
    return render_template('users.html',users=rows,csrf=session['csrf'],error=error,success=success)

@app.route('/admin/users/<int:user_id>/password',methods=['POST'])
@role_required('admin')
def password_reset(user_id):
    if request.form.get('csrf')!=session['csrf']:abort(403)
    password=request.form.get('password','')
    if len(password)<12: return 'Contraseña demasiado corta',400
    with db() as c:
        c.execute('UPDATE users SET password_hash=%s WHERE id=%s',(generate_password_hash(password),user_id))
        c.execute('INSERT INTO audit(actor,operation,object_key) VALUES (%s,%s,%s)',(session['uid'],'PASSWORD_RESET',str(user_id)))
    return redirect('/admin/users')

if __name__=='__main__':
    app.run(host='127.0.0.1',port=8000)
