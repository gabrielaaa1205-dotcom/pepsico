"""Run as a separate Render Cron Job (not inside Gunicorn workers)."""
import datetime, email, hashlib, imaplib, json, os, ssl, sys
from email.header import decode_header, make_header
from email.utils import parseaddr
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from mail_import import parse

ALLOWED_FROM=os.getenv('MAIL_ALLOWED_SENDER','laura.guzman@cianse.net').strip().lower()
MAIL_USER=os.environ['MAIL_USERNAME']
MAIL_PASS=os.environ['MAIL_PASSWORD']
DB=os.environ['DATABASE_URL']
HOST=os.getenv('MAIL_IMAP_HOST','imap.dreamhost.com')
PORT=int(os.getenv('MAIL_IMAP_PORT','993'))


def tables(c):
    c.execute('''CREATE TABLE IF NOT EXISTS mail_ingestions (
      id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, mailbox TEXT NOT NULL,
      message_uid TEXT NOT NULL, file_name TEXT NOT NULL, sha256 TEXT NOT NULL,
      status TEXT NOT NULL, details TEXT, processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE(mailbox,message_uid,sha256))''')
    c.execute('CREATE INDEX IF NOT EXISTS ix_mail_ingestions_hash ON mail_ingestions(sha256)')

def merge(current,incoming):
    changed=[];issues=[];count=0
    for kind,records in incoming.items():
        base=list(current.get(kind,[]))
        # Conservative identity: production/stock by day, outgoing by guide+date,
        # receipts by full content because imported receipts may lack guide IDs.
        def ident(o):
            if kind=='S':return (o.get('f'),o.get('guia'))
            if kind in ('P','T'):return (o.get('f'),)
            return (json.dumps(o,sort_keys=True,ensure_ascii=False),)
        lookup={ident(o):o for o in base}
        toadd=[]
        for row in records:
            key=ident(row)
            if key in lookup:
                if lookup[key]!=row:issues.append(f'{kind}: existe registro diferente para {key}; no se reemplazó')
            else:
                toadd.append(row);lookup[key]=row
        if toadd:current[kind]=sorted(base+toadd,key=lambda o:(o.get('f',''),o.get('guia','')));changed.append(kind);count+=len(toadd)
    return current,changed,count,issues

def ingest(connection,mailbox,uid,name,blob):
    digest=hashlib.sha256(blob).hexdigest()
    with connection.transaction():
        duplicate=connection.execute('SELECT 1 FROM mail_ingestions WHERE sha256=%s AND status IN (\'imported\',\'duplicate\') LIMIT 1',(digest,)).fetchone()
        exists=connection.execute('SELECT 1 FROM mail_ingestions WHERE mailbox=%s AND message_uid=%s AND sha256=%s',(mailbox,uid,digest)).fetchone()
        if exists:return 'already_checked'
        if duplicate:
            connection.execute('INSERT INTO mail_ingestions(mailbox,message_uid,file_name,sha256,status,details) VALUES (%s,%s,%s,%s,%s,%s)',(mailbox,uid,name,digest,'duplicate','Archivo previamente procesado'))
            return 'duplicate'
    try: data=parse(blob)
    except Exception as exc:
        with connection.transaction():
            connection.execute('INSERT INTO mail_ingestions(mailbox,message_uid,file_name,sha256,status,details) VALUES (%s,%s,%s,%s,%s,%s)',(mailbox,uid,name,digest,'error',str(exc)[:2000]))
        return 'error: '+str(exc)
    with connection.transaction():
        # Lock a stable shared key; protects from concurrent web UI writes on same row.
        row=connection.execute("SELECT value,revision FROM state WHERE key='kx-data' FOR UPDATE").fetchone()
        value=dict(row['value']) if row else {'S':[],'E':[],'P':[],'T':[],'meta':{}}
        value,changed,n,issues=merge(value,data)
        status='review' if issues else ('imported' if n else 'duplicate')
        if n:
            meta=dict(value.get('meta') or {});meta['at']=datetime.datetime.now(datetime.timezone.utc).isoformat();meta['src']='correo DreamHost';value['meta']=meta
            if row:
                connection.execute("UPDATE state SET value=%s::jsonb,revision=revision+1,updated_at=now() WHERE key='kx-data'",(json.dumps(value,ensure_ascii=False),))
            else:
                connection.execute("INSERT INTO state(key,value,revision) VALUES ('kx-data',%s::jsonb,1)",(json.dumps(value,ensure_ascii=False),))
            connection.execute('INSERT INTO audit(operation,object_key) VALUES (%s,%s)',('MAIL_IMPORT','kx-data'))
        info=f'{n} registros nuevos; categorías {changed}; '+('; '.join(issues[:12]))
        connection.execute('INSERT INTO mail_ingestions(mailbox,message_uid,file_name,sha256,status,details) VALUES (%s,%s,%s,%s,%s,%s)',(mailbox,uid,name,digest,status,info[:4000]))
    return status+': '+str(n)+' nuevos'+(' / discrepancias para revisar' if issues else '')

def run():
    with psycopg.connect(DB,row_factory=dict_row,autocommit=True) as connection:
        tables(connection)
        with imaplib.IMAP4_SSL(HOST,PORT,ssl_context=ssl.create_default_context(),timeout=45) as imap:
            imap.login(MAIL_USER,MAIL_PASS)
            typ,_=imap.select('INBOX',readonly=True)
            if typ!='OK':raise RuntimeError('No se pudo seleccionar bandeja INBOX')
            # Only recent mail to bound workload; UID + digest protect against repeats.
            since=(datetime.date.today()-datetime.timedelta(days=int(os.getenv('MAIL_LOOKBACK_DAYS','14')))).strftime('%d-%b-%Y')
            typ,items=imap.uid('SEARCH',None,'SINCE',since)
            if typ!='OK':raise RuntimeError('Error consultando mensajes')
            for uid in (items[0] or b'').split()[-200:]:
                typ,parts=imap.uid('FETCH',uid,'(BODY.PEEK[])')
                if typ!='OK' or not parts or not isinstance(parts[0],tuple):continue
                msg=email.message_from_bytes(parts[0][1])
                sender=parseaddr(msg.get('From',''))[1].lower().strip()
                if sender!=ALLOWED_FROM:continue
                for part in msg.walk():
                    if part.is_multipart():continue
                    filename=part.get_filename()
                    if not filename:continue
                    try:name=str(make_header(decode_header(filename)))
                    except Exception:name=filename
                    if not name.lower().endswith('.xlsx'):continue
                    blob=part.get_payload(decode=True) or b''
                    if len(blob)>14*1024*1024:
                        print('Archivo excede límite de tamaño:',name,flush=True);continue
                    try:
                        status=ingest(connection,MAIL_USER,uid.decode(),name,blob)
                        print(uid.decode(),name,status,flush=True)
                    except Exception as ex:print('ERROR de importación:',name,str(ex),file=sys.stderr,flush=True)
            imap.logout()

if __name__=='__main__':run()
