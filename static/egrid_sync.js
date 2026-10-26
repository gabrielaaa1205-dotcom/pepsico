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
    function attachCards(){for(const card of document.querySelectorAll('.invoice-card')){const raw=card.querySelector('.invoice-card-number')?.textContent||'';const match=raw.match(/E001-\d+/);if(!match)continue;const obj=(d.invoices||{})[match[0]];card.querySelector('.egrid-inline')?.remove();const box=document.createElement('div');box.className='egrid-inline';box.style.cssText='margin-top:12px;padding-top:9px;border-top:1px solid #dfebe6;font-size:12px';for(const [kind,label] of [['enviado','Envío'],['recibido','Recepción'],['aprobado','Aprobación'],['rechazado','Rechazo']]){const ev=obj?.events?.[kind];const p=document.createElement('div');p.textContent=label+': '+(ev?ev.date||'Correo sin fecha':'Sin evidencia');box.append(p)}for(const pdf of obj?.pdfs||[]){const a=document.createElement('a');a.href='/api/egrid/pdf/'+pdf.id;a.target='_blank';a.textContent='📄 Abrir factura PDF: '+pdf.filename;a.style.cssText='display:block;margin:8px 0;color:#146a5c;font-weight:700';box.append(a)}if(!(obj?.pdfs||[]).length){const no=document.createElement('div');no.textContent='Factura PDF: pendiente de localizar en el correo';no.style.cssText='margin-top:8px;color:#8a5c23';box.append(no)}card.append(box)}}attachCards();if(typeof renderInvoices==='function'&&!window.egridWrapped){const original=renderInvoices;window.renderInvoices=function(){original();attachCards()};window.egridWrapped=true}
  }
  button.onclick=async()=>{
    button.disabled=true;status.textContent='Leyendo correo de DreamHost…';
    try{
      const r=await fetch('/api/egrid/sync',{method:'POST',headers:{'X-CSRF-Token':window.EGRID_CSRF||''}});
      const responseText=await r.text();let d;try{d=JSON.parse(responseText)}catch(_){throw Error('El servidor interrumpió la sincronización. Intenta nuevamente; los lotes procesados se conservan.')}if(!r.ok)throw Error(d.error||'Error de sincronización');
      status.textContent='Lote terminado · evidencias nuevas: '+d.new_events+' · PDF nuevos: '+d.new_pdfs+' · Pulsa nuevamente para continuar con correos antiguos. '+(d.warnings||[]).join('; ');
      await load();
    }catch(e){status.textContent=e.message}finally{button.disabled=false}
  };
  try{
    const r=await fetch('/api/egrid/status');if(!r.ok)throw Error('Acceso permitido solo a administradores y supervisores');
    const d=await r.json();status.textContent=d.configured?'Buzón configurado, pero falta importar los correos. Haz clic en Sincronizar Enviados y Recibidos.':'Contraseña IMAP aún no disponible en Render';
    button.disabled=!d.configured;
    if(d.configured)await load()
  }catch(e){status.textContent=e.message;button.disabled=true}
})();