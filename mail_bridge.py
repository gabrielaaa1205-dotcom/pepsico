"""Authenticated scheduling entrypoint for Laura's Excel mail imports."""
import os, secrets, threading, datetime, logging
from flask import Blueprint, request, session, jsonify, abort, render_template_string
from server import db, check_csrf
bp_mail_bridge=Blueprint('mail_bridge',__name__)
LOCK_ID=676202611
def prepare(c):
    c.execute("""CREATE TABLE IF NOT EXISTS mail_sync_runs(
      id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      finished_at TIMESTAMPTZ, status TEXT NOT NULL,
      details TEXT NOT NULL DEFAULT '')""")
def execute():
    with db() as guard:
        got=guard.execute('SELECT pg_try_advisory_lock(%s) AS ok',(LOCK_ID,)).fetchone()['ok']
        if not got:return
        try:
            with db() as c:
                prepare(c)
                row=c.execute("INSERT INTO mail_sync_runs(status) VALUES ('running') RETURNING id").fetchone()
                run_id=row['id']
            try:
                from mail_sync import run
                run()
                status='completed';details='Sincronización del buzón finalizada; consultar importaciones y discrepancias.'
            except Exception as exc:
                logging.exception('Fallo al sincronizar Excel de Laura')
                status='error';details=(type(exc).__name__+': '+str(exc))[:450]
            with db() as c:
                c.execute("UPDATE mail_sync_runs SET finished_at=now(),status=%s,details=%s WHERE id=%s",(status,details,run_id))
        finally:
            guard.execute('SELECT pg_advisory_unlock(%s)',(LOCK_ID,))
def authorize():
    supplied=request.headers.get('X-Mail-Sync-Token','')
    expected=os.getenv('MAIL_SYNC_WEBHOOK_TOKEN','')
    if expected and secrets.compare_digest(supplied,expected):return 'scheduler'
    if session.get('uid') and session.get('role') in ('admin','supervisor'):
        check_csrf();return 'user'
    abort(403)
@bp_mail_bridge.route('/api/mail/refresh',methods=['POST'])
def refresh():
    authorize()
    thread=threading.Thread(target=execute,name='cianse-mail-import',daemon=True)
    thread.start()
    return jsonify(ok=True,status='started',info='Importación programada. Los resultados aparecerán en el historial.'),202
@bp_mail_bridge.route('/api/mail/sync-status')
def sync_status():
    if not session.get('uid'):abort(401)
    with db() as c:
        prepare(c)
        rows=c.execute('SELECT started_at,finished_at,status,details FROM mail_sync_runs ORDER BY id DESC LIMIT 5').fetchall()
        ing=c.execute('SELECT max(processed_at) latest,count(*) FILTER (WHERE status=%s) imported,count(*) FILTER (WHERE status=%s) review FROM mail_ingestions',('imported','review')).fetchone()
    return jsonify(runs=[dict(x,started_at=x['started_at'].isoformat(),finished_at=x['finished_at'].isoformat() if x['finished_at'] else None) for x in rows],ingestions=dict(ing,latest=ing['latest'].isoformat() if ing['latest'] else None))
