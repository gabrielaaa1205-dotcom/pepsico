from pathlib import Path
p=Path(__file__).parent/'templates/app.html'
s=p.read_text()
needle='const ENC = '
insert='''// Estado compartido protegido por sesión del servidor.
const CLOUD_CSRF = {{ cloud_csrf|tojson }};
const CLOUD_ROLE = {{ cloud_role|tojson }};
const CLOUD_USER = {{ cloud_username|tojson }};
const CLOUD = { state: {}, queue: Promise.resolve(), blocked: false };
const cloudReady = fetch('/api/state', {credentials:'same-origin'})
  .then(async r => {if (!r.ok) throw new Error('No se pudo conectar con la base de datos.'); CLOUD.state = await r.json();})
  .catch(e => { CLOUD.blocked = true; alert('ERROR DE CONEXIÓN: ' + e.message + ' No se permitirá trabajar sin sincronización.'); });
function cloudSave(key, value, method='PUT') {
  if (CLOUD.blocked || CLOUD_ROLE === 'consulta') {alert('No tienes permisos para guardar, o no hay conexión al servidor.'); return false;}
  // Cola serializada, con control de versión para prevenir sobrescrituras simultáneas.
  CLOUD.queue = CLOUD.queue.then(async () => {
    let version = CLOUD.state[key]?.revision || 0;
    let res = await fetch('/api/state/' + key, {method, credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRF-Token':CLOUD_CSRF},body:JSON.stringify({value,revision:version})});
    if (!res.ok) {
      CLOUD.blocked = true;
      const r = await res.json().catch(()=>({error:'Error del servidor'}));
      alert('NO SE GUARDÓ EL CAMBIO: ' + (r.error || 'Error') + '. Recarga la página antes de continuar.');
      return;
    }
    const r = await res.json();
    if (method==='DELETE') delete CLOUD.state[key];
    else CLOUD.state[key] = {value,revision:r.revision};
  }).catch(e=>{CLOUD.blocked=true;alert('Sin conexión. No se guardaron los cambios: '+e.message)});
  return true;
}
'''
s=s.replace(needle,insert+needle,1)
s=s.replace('  const snap = await openData(dkRaw);','  await cloudReady;\n  if (CLOUD.blocked) return;\n  const snap = await openData(dkRaw);',1)
# override old logout
s=s.replace("  location.reload();\n});", "  fetch('/logout', {method:'POST',headers:{'X-CSRF-Token':CLOUD_CSRF}}).finally(()=>{location.href='/login'});\n});",1)
old="""const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
};"""
new="""const LS = {
  get(k, d) { if (k==='kx-ui') {try {return JSON.parse(sessionStorage.getItem('kx-ui')) || d;}catch {return d;}} return CLOUD.state[k]?.value ?? d; },
  set(k, v) { if (k==='kx-ui') {sessionStorage.setItem(k,JSON.stringify(v));return true;} return cloudSave(k, structuredClone(v)); },
};"""
assert old in s
s=s.replace(old,new)
s=s.replace("try { localStorage.removeItem('kx-data'); } catch { } UI.key = null; render();", "cloudSave('kx-data', null, 'DELETE'); UI.key = null; render();")
# offer access to users page
s=s.replace('</body></html>', '''<div id="cloud-indicator" style="position:fixed;bottom:8px;right:8px;background:#155b43;color:white;padding:7px 12px;border-radius:8px;z-index:9999;font:12px system-ui">☁ Datos centrales · {{cloud_username}}{% if cloud_role == 'admin' %} · <a style="color:white" href="/admin/users">Usuarios</a>{% endif %}</div></body></html>''')
p.write_text(s)
