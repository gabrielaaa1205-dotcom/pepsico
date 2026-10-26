(async function(){
  const panel=document.createElement('section');panel.className='panel';
  panel.innerHTML='<h2>PepsiCo · Seguimiento de correos e-grid</h2><p id="egrid-state" class="muted">Verificando configuración del correo…</p><button type="button" id="egrid-sync" class="btn">Sincronizar Enviados y Recibidos</button><div id="egrid-evidence"></div>';
  const main=document.querySelector('main');if(!main)return;main.prepend(panel);
  const importBox=document.createElement('div');
  importBox.style.cssText='margin:12px 0;padding:13px;background:#f1f8f5;border-radius:10px';
  importBox.innerHTML='<strong>Importar facturas históricas de Outlook</strong><p class="muted">Selecciona el ZIP preparado con las facturas PDF.</p><input id="egrid-zip" type="file" accept=".zip,application/zip" style="max-width:100%;margin-right:8px"><button type="button" class="btn" id="egrid-import">Importar PDF al Kardex</button><p class="muted" id="egrid-import-status"></p>';
  panel.insertBefore(importBox,panel.querySelector('#egrid-evidence'));
  const reopen=document.createElement('button');
  reopen.type='button';reopen.className='btn ghost';reopen.textContent='Importar más facturas';
  reopen.style.cssText='display:none;margin:8px 0';
  panel.insertBefore(reopen,importBox);
  reopen.onclick=()=>{importBox.style.display='block';reopen.style.display='none';};
  const completeImport=()=>{importBox.style.display='none';reopen.style.display='inline-block';};
  const evidenceTarget=panel.querySelector('#egrid-evidence');
  const archive=document.createElement('details');
  archive.style.cssText='margin-top:12px';
  const archiveTitle=document.createElement('summary');
  archiveTitle.textContent='Ver historial de correos y facturas importadas';
  archiveTitle.style.cssText='cursor:pointer;color:#146a5c;font-weight:700;padding:10px 0';
  evidenceTarget.parentNode.insertBefore(archive,evidenceTarget);
  archive.append(archiveTitle,evidenceTarget);
  importBox.querySelector('#egrid-import').onclick=async()=>{
    const file=importBox.querySelector('#egrid-zip').files?.[0],out=importBox.querySelector('#egrid-import-status');
    if(!file){out.textContent='Selecciona un ZIP primero.';return}
    const btn=importBox.querySelector('#egrid-import');btn.disabled=true;out.textContent='Importando facturas…';
    try{
      const fd=new FormData();fd.append('file',file);
      const r=await fetch('/api/egrid/import-pdf-zip',{method:'POST',headers:{'X-CSRF-Token':window.EGRID_CSRF||''},body:fd});
      const d=await r.json();if(!r.ok)throw Error(d.error||'Error al importar');
      out.textContent='Importación terminada: '+d.new_pdfs+' facturas PDF nuevas. '+(d.skipped?.length?'No procesadas: '+d.skipped.join(', '):'');
      await load();
      if(d.new_pdfs>0 || d.already_imported)completeImport();
    }catch(e){out.textContent='Error: '+e.message}
    finally{btn.disabled=false}
  };
  const status=panel.querySelector('#egrid-state');
  const button=panel.querySelector('#egrid-sync');
  const target=panel.querySelector('#egrid-evidence');
  async function load(){
    const r=await fetch('/api/egrid/invoices');if(!r.ok)throw Error('No se puede leer el seguimiento de e-grid');
    const d=await r.json();if((d.imported_pdf_count||0)>0)completeImport();target.replaceChildren();
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
  let syncing=false;
  async function syncBatches(maxBatches=6){
    if(syncing)return;syncing=true;button.disabled=true;
    let totalEvents=0,totalPdfs=0;let scanDetails='';
    try{
      for(let step=0;step<maxBatches;step++){
        status.textContent='Buscando facturas en DreamHost · lote '+(step+1)+' de '+maxBatches+'…';
        const r=await fetch('/api/egrid/sync',{method:'POST',headers:{'X-CSRF-Token':window.EGRID_CSRF||''}});
        const body=await r.text();let d;
        try{d=JSON.parse(body)}catch(_){if(r.status===401||r.status===403)throw Error('Tu sesión expiró. Inicia sesión otra vez y vuelve al panel.');throw Error('El servidor devolvió una página en vez de datos. Consulta el panel o vuelve a iniciar sesión.')}
        if(r.status===401||r.status===403)throw Error('Tu sesión expiró. Vuelve a iniciar sesión en CIANSE PRO para sincronizar el correo.');if(!r.ok)throw Error(d.error||'Error de sincronización');
        totalEvents+=d.new_events||0;totalPdfs+=d.new_pdfs||0;scanDetails=Object.entries(d.scan||{}).map(([name,v])=>name+': '+(v.error||((v.processed_before||0)+(v.this_batch||0))+'/'+(v.matched||0)+' correos')).join(' · ');if((d.warnings||[]).length)scanDetails+=' · '+d.warnings.join('; ');
        await load();
        if(d.complete)break;
      }
      status.textContent='Búsqueda realizada · '+totalEvents+' evidencias y '+totalPdfs+' PDF nuevos. '+scanDetails;
    }catch(e){status.textContent='Sincronización incompleta: '+e.message}
    finally{syncing=false;button.disabled=false}
  }
  button.onclick=()=>syncBatches(6);
  try{
    const r=await fetch('/api/egrid/status');if(!r.ok)throw Error('Acceso permitido solo a administradores y supervisores');
    const d=await r.json();status.textContent=d.configured?'Buzón configurado, pero falta importar los correos. Haz clic en Sincronizar Enviados y Recibidos.':'Contraseña IMAP aún no disponible en Render';
    button.disabled=!d.configured;
    if(d.configured){await load();await syncBatches(6)}
  }catch(e){status.textContent=e.message;button.disabled=true}
})();