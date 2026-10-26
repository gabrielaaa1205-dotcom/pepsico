(async function(){
  const panel=document.createElement('section');panel.className='panel';
  panel.innerHTML='<h2>PepsiCo · Seguimiento de correos e-grid</h2><p id="egrid-state" class="muted">Verificando configuración del correo…</p><button type="button" id="egrid-sync" class="btn">Sincronizar Enviados y Recibidos</button><div id="egrid-evidence"></div>';
  const main=document.querySelector('main');if(!main)return;main.prepend(panel);
  const status=panel.querySelector('#egrid-state');
  const button=panel.querySelector('#egrid-sync');
  const target=panel.querySelector('#egrid-evidence');
  async function load(){
    const r=await fetch('/api/egrid/invoices');if(!r.ok)throw Error('No se puede leer el seguimiento de e-grid');
    const d=await r.json();target.replaceChildren();
    for(const [invoice,obj] of Object.entries(d.invoices||{})){
      const card=document.createElement('details');card.style.cssText='padding:12px;margin:8px 0;background:#f4f9f7;border:1px solid #daeae3;border-radius:8px';
      const head=document.createElement('summary');head.textContent='Factura '+invoice+' · evidencias y PDF';card.append(head);
      const events=obj.events||{};
      for(const [kind,label] of [['enviado','Factura enviada'],['recibido','Recepción confirmada'],['aprobado','Aprobada'],['rechazado','Rechazada']]){
        const e=events[kind],line=document.createElement('p');
        line.textContent=label+': '+(e?(e.date||'Sin fecha')+' · '+(e.subject||''):'Sin correo de respaldo');card.append(line);
      }
      for(const pdf of obj.pdfs||[]){const a=document.createElement('a');a.href='/api/egrid/pdf/'+pdf.id;a.target='_blank';a.textContent='Ver PDF: '+pdf.filename;a.style.cssText='display:block;margin:6px 0';card.append(a)}
      target.append(card);
    }
    if(!target.childElementCount){target.textContent='Aún no hay evidencias importadas.'}
  }
  button.onclick=async()=>{
    button.disabled=true;status.textContent='Leyendo correo de DreamHost…';
    try{
      const r=await fetch('/api/egrid/sync',{method:'POST',headers:{'X-CSRF-Token':window.EGRID_CSRF||''}});
      const d=await r.json();if(!r.ok)throw Error(d.error||'Error de sincronización');
      status.textContent='Evidencias nuevas: '+d.new_events+' · PDF nuevos: '+d.new_pdfs+' '+(d.warnings||[]).join('; ');
      await load();
    }catch(e){status.textContent=e.message}finally{button.disabled=false}
  };
  try{
    const r=await fetch('/api/egrid/status');if(!r.ok)throw Error('Acceso permitido solo a administradores y supervisores');
    const d=await r.json();status.textContent=d.configured?'Correo listo: '+d.mailbox:'Contraseña IMAP aún no disponible en Render';
    button.disabled=!d.configured;
    if(d.configured)await load()
  }catch(e){status.textContent=e.message;button.disabled=true}
})();