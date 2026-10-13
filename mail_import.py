"""Conservative XLSX importer. Unknown or contradictory records are quarantined."""
import datetime as dt, io, re, unicodedata, zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict

NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
REL='http://schemas.openxmlformats.org/package/2006/relationships'

def norm(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFD',str(value or '')) if unicodedata.category(c)!='Mn').upper().split())
def num(x):
    try: return float(str(x).replace(',','')) if x is not None and str(x).strip() else 0
    except (ValueError,TypeError): return 0
def date(x):
    if x is None:return None
    if isinstance(x,(int,float)) or (isinstance(x,str) and re.fullmatch(r'\d{5}(?:\.0)?',x)):
        n=float(x)
        if 30000<=n<=60000:return (dt.datetime(1899,12,30)+dt.timedelta(days=round(n))).date().isoformat()
        return None
    s=str(x).strip()
    m=re.match(r'^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})',s)
    if m:
        d,mo,y=map(int,m.groups());y=y+2000 if y<100 else y
        try:return dt.date(y,mo,d).isoformat()
        except ValueError:return None
    m=re.match(r'^\d{4}-\d{2}-\d{2}',s)
    if m:
        try:return dt.date.fromisoformat(m.group()).isoformat()
        except ValueError:return None
    return None

def read_xlsx(blob):
    if len(blob)>14*1024*1024:raise ValueError('Adjunto demasiado grande')
    z=zipfile.ZipFile(io.BytesIO(blob))
    if sum(i.file_size for i in z.infolist())>110*1024*1024:raise ValueError('Archivo Excel excede tamaño permitido')
    ss=[]
    if 'xl/sharedStrings.xml' in z.namelist():
        root=ET.fromstring(z.read('xl/sharedStrings.xml'))
        ss=[''.join(n.text or '' for n in si.findall('.//m:t',NS)) for si in root.findall('m:si',NS)]
    wb=ET.fromstring(z.read('xl/workbook.xml'))
    rel=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    paths={x.attrib['Id']:x.attrib['Target'].lstrip('/') for x in rel.findall('{%s}Relationship'%REL)}
    output={}
    for sh in wb.findall('.//m:sheets/m:sheet',NS):
        name=sh.attrib['name'];rid=sh.attrib['{%s}id'%NS['r']];path=paths[rid]
        if not path.startswith('xl/'):path='xl/'+path
        if path not in z.namelist():continue
        rows=[];root=ET.fromstring(z.read(path))
        for row in root.findall('.//m:sheetData/m:row',NS):
            if len(rows)>=20000:break
            a=[]
            for c in row.findall('m:c',NS):
                ref=c.attrib.get('r','A1');letters=re.match('[A-Z]+',ref)
                if not letters:continue
                col=0
                for ch in letters.group():col=col*26+ord(ch)-64
                if col>150:continue
                while len(a)<col:a.append(None)
                v=c.find('m:v',NS);inline=c.find('m:is',NS);val=v.text if v is not None else None
                if c.attrib.get('t')=='s' and val is not None:val=ss[int(val)]
                elif c.attrib.get('t')=='inlineStr' and inline is not None:val=''.join(n.text or '' for n in inline.findall('.//m:t',NS))
                elif c.attrib.get('t') not in ('str','e') and val is not None:
                    try:val=float(val)
                    except ValueError:pass
                a[col-1]=val
            rows.append(a)
        output[name]=rows
    return output

def cell(row,col):return row[col] if col>=0 and col<len(row) else None
def header(rows,terms):
    for i,r in enumerate(rows[:15]):
        cols=[norm(v) for v in r]
        if all(any(t in x for x in cols) for t in terms):return i,cols
    return None,None
def col(cols,*opts):
    for op in opts:
        for i,c in enumerate(cols):
            if c==op:return i
    for op in opts:
        for i,c in enumerate(cols):
            if op in c:return i
    return -1

def salidas(rows):
    h,c=header(rows,['GUIA','MERMA','TOTAL CAJAS'])
    if c is None:raise ValueError('Encabezados de salidas no reconocidos')
    fields={'f':col(c,'F. ENVIO','FECHA'),'tr':col(c,'TRANSPORTE'),'dest':col(c,'LUGAR DE DESTINO','DESTINO'), 'guia':col(c,'N° GUIA REMISION CIANSE','GUIA'),'pal':col(c,'CANTIDAD DE PALLETS','PALLETS'),'me':col(c,'MERMA'),'t':col(c,'TOTAL CAJAS')}
    a={k:col(c,*v) for k,v in {'v':['CAJA VERDE APTA'],'n':['CAJA NEGRO APTA'],'c':['CAJA CHICA APTA'],'z':['CAJA AZUL APTA'],'m':['CAJA MORADO APTA'],'e':['CAJA EXPORTACION APTAS']}.items()}
    x={k:col(c,*v) for k,v in {'v':['RECHAZO VERDE'],'n':['RECHAZO NEGRO'],'c':['RECHAZO CHICA'],'z':['RECHAZO AZUL'],'m':['RECHAZO MORADO'],'e':['CAJA EXPORTACION']}.items()}
    if x['e']==a['e']:x['e']=next((i for i,s in enumerate(c) if s=='CAJA EXPORTACION' and i!=a['e']),-1)
    out=[]
    for r in rows[h+1:]:
        f=date(cell(r,fields['f']));g=str(cell(r,fields['guia']) or '').strip()
        if not (f and g):continue
        good={k:num(cell(r,j)) for k,j in a.items()};reject={k:num(cell(r,j)) for k,j in x.items()}
        merma=num(cell(r,fields['me']));total=num(cell(r,fields['t'])) or sum(good.values())+sum(reject.values())+merma
        if any(v<0 for v in [*good.values(),*reject.values(),merma,total]):raise ValueError('Cantidades negativas en salidas')
        if abs(total-sum(good.values())-sum(reject.values())-merma)>0.01:raise ValueError('El total de una guía no cuadra con sus categorías: '+g)
        out.append(dict(f=f,tr=str(cell(r,fields['tr']) or '').strip(),dest=norm(cell(r,fields['dest'])),guia=g,pal=num(cell(r,fields['pal'])),a=good,x=reject,me=merma,t=total))
    return out

def entradas(rows):
    h,c=header(rows,['FECHA DE RECEPCION','TOTAL CAJAS'])
    if c is None:return []
    cc={k:col(c,*v) for k,v in {'f':['FECHA DE RECEPCION'],'tr':['TRANSPORTE'],'p':['PROCEDENCIA'],'d':['NOMBRE PUNTO DISTRIBUIDOR'],'ch':['CAJAS CHICAS'],'gr':['CAJAS GRANDES']}.items()}
    out=[]
    for r in rows[h+1:]:
        f=date(cell(r,cc['f']));ch=num(cell(r,cc['ch']));gr=num(cell(r,cc['gr']))
        if not f or ch+gr<=0:continue
        p=norm(cell(r,cc['p']));p=p if p in ('LIMA','PROVINCIA') else 'OTRO'
        out.append(dict(f=f,tr=re.sub(r'^TRANSPORTES? ', 'T. ', norm(cell(r,cc['tr']))),p=p,d=str(cell(r,cc['d']) or '').strip(),ch=ch,gr=gr))
    return out

def seleccion(rows):
    h,c=header(rows,['SELECCIONADAS BUENAS'])
    if c is None:raise ValueError('No se reconoce selección diaria')
    cc={k:col(c,*v) for k,v in {'f':['DIA'],'ing':['INGRESO'],'sal':['SALIDA'],'b':['SELECCIONADAS BUENAS'],'r':['SELECCIONADAS RECHAZADAS'],'e':['SELECCIONADAS PARA ELIMINAR']}.items()}
    by={};last=None
    for r in rows[h+1:]:
        f=date(cell(r,cc['f']))
        if f:last=f
        raw_b=cell(r,cc['b']);raw_r=cell(r,cc['r'])
        if not isinstance(raw_b,(int,float)) and not isinstance(raw_r,(int,float)):continue
        f=f or last
        if not f:continue
        o=by.setdefault(f,dict(f=f,ing=0,sal=0,b=0,r=0,e=0))
        for k in ('ing','sal','b','r','e'):o[k]+=num(cell(r,cc[k]))
    return [x for x in by.values() if x['b']+x['r']+x['e']>0]

def stock(rows):
    out=[];cur=None;j=-1
    for r in rows:
        labels=[norm(x) for x in r[:12]]
        ix=next((i for i,t in enumerate(labels) if t.startswith('POR SELECCIONAR')),-1)
        if ix>=0:
            f=next((x for x in [cell(r,0),cell(r,1),*r[:ix]] if date(x)),None)
            if f is not None:
                j=ix;cur=dict(f=date(f),ps=0,buen=0,rech=0,elim=0,tg=0,tc=0);out.append(cur)
        if cur is None or j<0:continue
        lab=norm(cell(r,j));g=num(cell(r,j+1));ch=num(cell(r,j+2))
        if lab=='TOTAL' or lab.startswith('TOTAL '):cur['tg']=g;cur['tc']=ch;cur=None;continue
        if lab.startswith('POR SELECCIONAR'):cur['ps']+=g+ch
        elif 'BUEN ESTADO' in lab or 'BUENAS' in lab:cur['buen']+=g+ch
        elif 'RECHAZ' in lab:cur['rech']+=g+ch
        elif 'ELIMINAR' in lab or 'MERMA' in lab:cur['elim']+=g+ch
    return list({o['f']:o for o in out}.values())

def parse(blob):
    sheets=read_xlsx(blob);names=list(sheets)
    find=lambda term: next((k for k in names if term in norm(k)),None)
    if find('SALIDAS'):
        s=salidas(sheets[find('SALIDAS')]);e=entradas(sheets[find('ENTRADAS')]) if find('ENTRADAS') else []
        if not s:raise ValueError('No se encontraron guías de salida')
        return {'S':s,'E':e}
    if find('SELECCION'):
        p=seleccion(sheets[find('SELECCION')]);
        if not p:raise ValueError('No se encontraron días de producción')
        return {'P':p}
    candidates=[stock(rows) for rows in sheets.values()]
    t=max(candidates,key=len,default=[])
    if not t:raise ValueError('No se reconoce el formato del stock')
    return {'T':t}
