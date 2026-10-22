/* AURA Etiquetas: cliente con sesión privada, sin servicios de terceros. */
'use strict';

const $ = (id) => document.getElementById(id);
const csrfToken = document.querySelector('meta[name="aura-csrf"]')?.content || '';
const csrfHeader = {'X-CSRF-Token': csrfToken};
const state = {
  products: [],
  filter: '', tablePage: 0, page: 0, info: null,
  file: null, parsed: null, history: null,
  previewSerial: 0, busy: false
};
const ROWS_PER_PAGE = 35;
const FIELDS = ['format','orientation','sheetWidth','sheetHeight','labelWidth','labelHeight','margin','gapX','gapY','dpi','fontPrice','fontName','fontCode','borderPrint','artworkFormat','cutColor','blankLabels','safeMargin'];
const money = new Intl.NumberFormat('es-PE',{minimumFractionDigits:2,maximumFractionDigits:2});
const fmtInt = new Intl.NumberFormat('es-PE');
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (x) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));

function toast(message, error=false) {
  const node = $('toast');
  node.textContent=message;
  node.classList.toggle('error',error);
  node.classList.add('show');
  clearTimeout(toast.timeout);
  toast.timeout=setTimeout(()=>node.classList.remove('show'),4000);
}
function roundPrice(x) {return Math.round((x+Number.EPSILON)*100)/100;}
function priceNumber(v) {
  if (typeof v === 'number') return Number.isFinite(v) ? Math.max(0,v) : 0;
  let s=String(v??'').replace(/[^\d.,-]/g,'');
  if (s.includes(',')&&s.includes('.')) s = s.lastIndexOf(',')>s.lastIndexOf('.') ? s.replaceAll('.','').replace(',','.') : s.replaceAll(',','');
  else if(s.includes(',')){ const parts=s.split(','); s=parts.length===2&&parts[1].length!==3?parts.join('.'):parts.join(''); }
  else if(s.includes('.')) {const parts=s.split('.'); if(parts.length===2&&parts[1].length===3) s=parts.join('');}
  const n=Number(s);return Number.isFinite(n)?Math.max(0,roundPrice(n)):0;
}
function settings() {
  const obj={};
  FIELDS.forEach(k=>{
    const el=$(k);obj[k]=el.type==='checkbox'?el.checked:(el.type==='number'?Number(el.value):el.value);
  });
  return obj;
}
function payload(page=state.page) {return {settings: settings(),products:state.products.map(p=>({code:p.code,name:p.name,labelName:p.labelName||'',price:p.price,copies:p.copies})),page};}
function filteredProducts() {
  const q=state.filter.trim().toLocaleLowerCase('es');
  return state.products.map((p,i)=>({p,i})).filter(({p})=>!q||(p.name+' '+(p.labelName||'')+' '+p.code).toLocaleLowerCase('es').includes(q));
}
function updateList(){
  const filtered=filteredProducts(), pages=Math.max(1,Math.ceil(filtered.length/ROWS_PER_PAGE));
  state.tablePage=Math.max(0,Math.min(state.tablePage,pages-1));
  const slice=filtered.slice(state.tablePage*ROWS_PER_PAGE,(state.tablePage+1)*ROWS_PER_PAGE);
  $('productRows').innerHTML=slice.length?slice.map(({p,i})=>`
    <tr data-row="${i}">
      <td><input aria-label="Código de fila ${i+1}" data-field="code" data-index="${i}" maxlength="80" value="${esc(p.code)}" /></td>
      <td><input aria-label="Nombre original de fila ${i+1}" data-field="name" data-index="${i}" maxlength="120" value="${esc(p.name)}" /></td>
      <td><input aria-label="Nombre en etiqueta de fila ${i+1}" data-field="labelName" data-index="${i}" maxlength="120" placeholder="Automático" title="Opcional: deja vacío para usar el original" value="${esc(p.labelName||'')}" /></td>
      <td><input aria-label="Precio de fila ${i+1}" data-field="price" data-index="${i}" inputmode="decimal" value="${esc(money.format(p.price))}" /></td>
      <td><input aria-label="Copias de fila ${i+1}" data-field="copies" data-index="${i}" type="number" min="0" max="2000" step="1" value="${p.copies}" /></td>
      <td><button type="button" class="delete-row" aria-label="Eliminar fila ${i+1}" data-delete="${i}" title="Eliminar producto">×</button></td>
    </tr>`).join(''):'<tr><td colspan="6" class="table-empty">No hay productos que mostrar.</td></tr>';
  $('productCount').textContent=fmtInt.format(state.products.length);
  $('tableShowing').textContent=`${fmtInt.format(filtered.length)} productos`;
  $('productFooter').textContent=state.products.length ? `${fmtInt.format(state.products.reduce((sum,p)=>sum+p.copies,0))} etiquetas en total` : 'Sin productos';
  $('rowsPage').textContent=`${state.tablePage+1} / ${pages}`;
  $('prevRows').disabled=state.tablePage<=0;
  $('nextRows').disabled=state.tablePage>=pages-1;
  $('undoBtn').disabled=!state.history;
}
function remember(){state.history=state.products.map(p=>({...p}));$('undoBtn').disabled=false;}
function addRow(){
  remember();state.products.push({code:'',name:'Nueva joya',labelName:'',price:0,copies:1});
  state.filter='';$('searchProducts').value='';
  state.tablePage=Math.floor((state.products.length-1)/ROWS_PER_PAGE);
  updateList();schedulePreview();
  setTimeout(()=>{
    const input=$('productRows').querySelector('tr:last-child input[data-field="name"]');
    input?.focus();input?.select();
  },0);
}
function loadDemo(){
  remember();
  state.products=[
    {code:'AU-001',name:'Collar Aurora',price:35,copies:3},
    {code:'AU-002',name:'Aretes Perla',price:45,copies:5},
    {code:'AU-003',name:'Pulsera Dalia',price:29.90,copies:4},
    {code:'AU-004',name:'Anillo Luna',price:39,copies:2},
    {code:'AU-005',name:'Cadena Minimal',price:59.90,copies:3},
    {code:'AU-006',name:'Argollas Doradas',price:42,copies:6}
  ];
  state.page=0;state.tablePage=0;state.filter='';$('searchProducts').value='';
  $('fileStatus').textContent='Modo demostración · 6 joyas';updateList();schedulePreview();toast('Ejemplo cargado. Puedes editar todo antes de exportar.');
}
function resetAll(){
  if(state.products.length&&!confirm('¿Crear un pliego nuevo? Se descartarán los cambios actuales.'))return;
  state.products=[];state.history=null;state.page=0;state.tablePage=0;state.filter='';state.file=null;
  $('searchProducts').value='';$('fileInput').value='';$('fileStatus').textContent='Sin archivo importado';
  updateList();schedulePreview();toast('Nuevo pliego en blanco.');
}
async function importFile(file, sheet=null) {
  if (!file) return;
  if(file.size >15*1024*1024){toast('El archivo supera el límite de 15 MB.',true);return;}
  if(!/\.(xlsx|xlsm|xls|csv)$/i.test(file.name)){toast('Sube un archivo .xlsx, .xls, .xlsm o .csv.',true);return;}
  state.file=file;
  working(true,'Leyendo inventario…','Detectando hojas, columnas y productos.');
  try {
    const qp=new URLSearchParams({filename:file.name});if(sheet)qp.set('sheet',sheet);
    const response=await fetch('/api/import?'+qp.toString(),{method:'POST',headers:{'Content-Type':'application/octet-stream', ...csrfHeader},body:await file.arrayBuffer()});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'No fue posible leer el archivo.');
    state.parsed=data;showMapping(data);$('fileStatus').textContent=`${file.name} · ${fmtInt.format(data.rows.length)} filas detectadas`;
  }catch(e){toast(e.message,true)}finally{working(false)}
}
function optionList(data,value,allowEmpty=true){
  const blank=allowEmpty?'<option value="">— No asignar —</option>':'';
  return blank+data.headers.map((header,i)=>`<option value="${i}" ${value===i?'selected':''}>${esc(header)}</option>`).join('');
}
function showMapping(data){
  for(const [key,selectId] of Object.entries({code:'mapCode',name:'mapName',price:'mapPrice',copies:'mapCopies'})){
    $(selectId).innerHTML=optionList(data,data.mapping[key]);
  }
  $('sheetSelectWrap').classList.toggle('hidden',data.sheets.length<=1);
  $('sheetSelect').innerHTML=data.sheets.map(s=>`<option value="${esc(s)}" ${s===data.sheet?'selected':''}>${esc(s)}</option>`).join('');
  const cells=data.rows.slice(0,3);
  $('mappingSample').innerHTML=`<table><thead><tr>${data.headers.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${cells.map(row=>`<tr>${data.headers.map((_,i)=>`<td>${esc(row[i]??'')}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  $('mappingDialog').showModal();
}
function closeMapping(){$('mappingDialog').close();}
function confirmMapping(){
  const mapping={};for(const [key,id] of Object.entries({code:'mapCode',name:'mapName',price:'mapPrice',copies:'mapCopies'}))mapping[key]=$(id).value===''?null:Number($(id).value);
  if(mapping.code==null&&mapping.name==null){toast('Asocia al menos las columnas nombre o código.',true);return;}
  const rows=state.parsed.rows;
  if(!rows.length){toast('No se encontraron productos.',true);return;}
  remember();let invalidPrices=0;
  state.products=rows.map(r=>{
    const cell=(key)=>mapping[key]===null?'':(r[mapping[key]]??'');
    const rawPrice=cell('price');const price=priceNumber(rawPrice);
    if(rawPrice&&!/[0-9]/.test(String(rawPrice)))invalidPrices++;
    const rawCopies=cell('copies'); const n=rawCopies===''?1:Number(String(rawCopies).replace(',','.'));
    return {code:String(cell('code')).trim(),name:String(cell('name')).trim(),labelName:'',price,
      copies:Number.isInteger(n)&&n>=0?Math.min(n,2000):1};
  }).filter(p=>p.name||p.code);
  state.filter='';$('searchProducts').value='';state.page=0;state.tablePage=0;
  closeMapping();updateList();schedulePreview();
  if(state.parsed.truncated)toast('Se importaron las primeras 5000 filas. Divide el archivo para procesar más.');
  else if(invalidPrices)toast(`Importados ${state.products.length} productos. Revisa ${invalidPrices} precios vacíos o incorrectos.`);
  else toast(`${state.products.length} productos importados correctamente.`);
}
function working(active,title='',caption=''){
  state.busy=active;
  $('workingOverlay').classList.toggle('hidden',!active);
  if(title)$('workingTitle').textContent=title;
  if(caption)$('workingCaption').textContent=caption;
  document.querySelectorAll('#exportBundle,#exportBundleTop,#exportArtwork,#exportSvg').forEach(b=>b.disabled=active || Boolean(state.info && state.info.validForExport===false));
}
let previewTimer=null;
function schedulePreview(){clearTimeout(previewTimer);previewTimer=setTimeout(refreshPreview,240)}
async function refreshPreview(){
  const serial=++state.previewSerial;
  try{
    const response=await fetch('/api/preview',{method:'POST',headers:{'Content-Type':'application/json', ...csrfHeader},body:JSON.stringify(payload())});
    const info=await response.json();if(serial!==state.previewSerial)return;
    if(!response.ok)throw new Error(info.error||'Configuración no válida');
    state.info=info;state.page=info.page;
    const blocked=(info.validationErrors||[]);
    $('nameFitWarning').classList.toggle('hidden',blocked.length===0);
    $('nameFitWarning').textContent=blocked.length?
      `Exportación bloqueada: ${info.nameWarnings} producto(s) no respetan los márgenes de seguridad. `+
      blocked.slice(0,4).map(x=>`Fila ${x.product} (${x.code||x.name}): ${x.message}`).join(' | ')+
      (blocked.length>4?' | Revisa también los demás productos.':''):'';
    document.querySelectorAll('#exportBundle,#exportBundleTop,#exportArtwork,#exportSvg').forEach(b=>b.disabled=blocked.length>0);
    const blankInfo=[];
    if(info.blankCopies>0) blankInfo.push(`${fmtInt.format(info.blankCopies)} etiqueta(s) vacía(s) añadida(s).`);
    if(info.remainingToFillLastPage>0) blankInfo.push(`Faltan ${fmtInt.format(info.remainingToFillLastPage)} para completar el último pliego.`);
    else if(info.copies>0) blankInfo.push('El último pliego ya está completo.');
    $('blankFillInfo').classList.toggle('hidden', blankInfo.length===0);
    $('blankFillInfo').textContent=blankInfo.join(' ');
    $('totalLabels').textContent=fmtInt.format(info.copies);
    $('capacity').textContent=fmtInt.format(info.capacity);
    $('totalSheets').textContent=fmtInt.format(info.pages);
    $('paperMeasure').textContent=`${settings().format==='PERSONALIZADO'?'Personalizado':settings().format} · ${info.width} × ${info.height} mm`;
    $('pageIndicator').textContent=`Pliego ${info.pages?info.page+1:0} de ${info.pages}`;
    $('prevPage').disabled=info.page<=0;
    $('nextPage').disabled=info.page>=info.pages-1;
    $('previewError').classList.add('hidden');
    if(info.copies){$('paperStage').innerHTML=info.svg;}
    else $('paperStage').innerHTML='<div class="empty-preview"><div class="empty-icon">✧</div><strong>Tu diseño aparecerá aquí</strong><p>Importa productos para visualizar cómo quedará cada hoja antes de imprimir.</p></div>';
  }catch(e){
    if(serial!==state.previewSerial)return;
    state.info=null;$('nameFitWarning').classList.add('hidden');$('blankFillInfo').classList.add('hidden');
    $('previewError').textContent=e.message;
    $('previewError').classList.remove('hidden');
    $('paperStage').innerHTML='';
    $('totalSheets').textContent='—';$('capacity').textContent='—';
  }
}
async function download(kind){
  if(state.busy)return;
  if(state.info && !state.info.validForExport){toast('Corrige los productos señalados antes de exportar.', true);return;}
  const blanks=Number(settings().blankLabels)||0;
  if(!state.products.some(p=>p.copies>0&&(p.name||p.code)) && blanks<=0){toast('Primero agrega productos con al menos una copia o indica etiquetas vacías adicionales.',true);return;}
  working(true,'Preparando tus archivos…','Generando los pliegos a escala real. A 600 DPI, una hoja A3 puede tardar un poco.');
  try{
    const res=await fetch('/api/export/'+kind,{method:'POST',headers:{'Content-Type':'application/json', ...csrfHeader},body:JSON.stringify(payload())});
    if(!res.ok){let err;try{err=(await res.json()).error}catch{}throw new Error(err||'No se pudo generar el archivo.');}
    const blob=await res.blob();
    const disposition=res.headers.get('Content-Disposition')||'';
    const match=/filename="([^"]+)"/.exec(disposition);
    const filename=match?match[1]:`AURA_etiquetas.${kind==='zip'?'zip':kind}`;
    const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=filename;a.style.display='none';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),12000);
    toast(`Archivo generado: ${filename}`);
  }catch(e){toast(e.message,true)}finally{working(false)}
}
function updateExportLabels(){
  const fmt=$('artworkFormat').value.toUpperCase();
  $('exportBundle').innerHTML=`Descargar ${fmt} de impresión + SVG de corte <span>↓</span>`;
  $('exportArtwork').textContent=`Solo ${fmt} de impresión`;
}
function normalizeHexColor(value){
  const match=String(value||'').trim().match(/^#?([0-9a-fA-F]{6})$/);
  return match ? ('#'+match[1].toUpperCase()) : null;
}
function syncCutColor(fromText=false){
  const picker=$('cutColor'), input=$('cutColorText');
  if(fromText){
    const normalized=normalizeHexColor(input.value);
    if(normalized){ input.value=normalized; picker.value=normalized.toLowerCase(); schedulePreview(); }
    return normalized;
  }
  input.value=picker.value.toUpperCase();
  schedulePreview();
  return input.value;
}
function init(){
  $('logoutBtn')?.addEventListener('click', async () => {
    if (!confirm('¿Cerrar tu sesión privada? Los cambios sin descargar se perderán.')) return;
    try { await fetch('/logout', {method:'POST', headers:csrfHeader}); } finally { window.location.href='/login'; }
  });
  $('dropZone').addEventListener('click',()=>$('fileInput').click());
  $('dropZone').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('fileInput').click()}});
  $('dropZone').addEventListener('dragover',e=>{e.preventDefault();$('dropZone').classList.add('dragging')});
  $('dropZone').addEventListener('dragleave',()=>$('dropZone').classList.remove('dragging'));
  $('dropZone').addEventListener('drop',e=>{e.preventDefault();$('dropZone').classList.remove('dragging');importFile(e.dataTransfer.files[0])});
  $('fileInput').addEventListener('change',e=>{if(e.target.files[0])importFile(e.target.files[0])});
  $('demoBtn').addEventListener('click',loadDemo);
  $('resetBtn').addEventListener('click',resetAll);
  $('addRowBtn').addEventListener('click',addRow);$('addRowBtn2').addEventListener('click',addRow);
  $('searchProducts').addEventListener('input',e=>{state.filter=e.target.value;state.tablePage=0;updateList()});
  $('prevRows').addEventListener('click',()=>{state.tablePage--;updateList()});
  $('nextRows').addEventListener('click',()=>{state.tablePage++;updateList()});
  $('productRows').addEventListener('change',e=>{
    const el=e.target,field=el.dataset.field,index=Number(el.dataset.index);
    if(!field||!state.products[index])return;
    remember();
    const product=state.products[index];
    if(field==='price')product.price=priceNumber(el.value);
    else if(field==='copies')product.copies=Math.min(2000,Math.max(0,parseInt(el.value,10)||0));
    else product[field]=el.value;
    updateList();schedulePreview();
  });
  $('productRows').addEventListener('click',e=>{
    const button=e.target.closest('[data-delete]');if(!button)return;
    const index=Number(button.dataset.delete);remember();state.products.splice(index,1);updateList();schedulePreview();
  });
  $('multiplyBtn').addEventListener('click',()=>{
    const factor=Number($('factorInput').value);
    if(!Number.isFinite(factor)||factor<0||factor>1000){toast('El factor debe estar entre 0 y 1000.',true);return;}
    if(!state.products.length){toast('Aún no hay productos.',true);return;}
    remember();state.products.forEach(p=>p.price=roundPrice(p.price*factor));updateList();schedulePreview();toast(`Precios multiplicados por ${factor}.`);
  });
  $('copiesBulkBtn').addEventListener('click',()=>{
    const copies=Number($('copiesBulkInput').value);
    if(!Number.isInteger(copies)||copies<0||copies>2000){toast('Usa un número entero de copias entre 0 y 2000.',true);return;}
    if(!state.products.length){toast('Aún no hay productos.',true);return;}
    remember();state.products.forEach(p=>p.copies=copies);updateList();schedulePreview();toast(`Actualizadas ${copies} copias por producto.`);
  });
  $('undoBtn').addEventListener('click',()=>{
    if(!state.history)return;
    state.products=state.history;state.history=null;updateList();schedulePreview();toast('Último cambio deshecho.');
  });
  FIELDS.forEach(k=>$(k).addEventListener('change',()=>{
    if(k==='format')$('customDimensions').classList.toggle('hidden',$('format').value!=='PERSONALIZADO');
    if(k==='artworkFormat')updateExportLabels();
    else {state.page=0;schedulePreview();}
  }));
  $('prevPage').addEventListener('click',()=>{if(state.page>0){state.page--;schedulePreview()}});
  $('nextPage').addEventListener('click',()=>{if(state.info&&state.page<state.info.pages-1){state.page++;schedulePreview()}});
  $('exportBundle').addEventListener('click',()=>download('zip'));
  $('exportBundleTop').addEventListener('click',()=>download('zip'));
  $('exportArtwork').addEventListener('click',()=>download($('artworkFormat').value==='svg'?'artwork-svg':'png'));
  $('exportSvg').addEventListener('click',()=>download('svg'));
  $('cutColor').addEventListener('input',()=>syncCutColor(false));
  $('cutColorText').addEventListener('change',()=>{ if(!syncCutColor(true)) { $('cutColorText').value=$('cutColor').value.toUpperCase(); toast('Usa un color hexadecimal válido, por ejemplo #FF0000.', true); } });
  $('cutColorText').addEventListener('blur',()=>{ if(!normalizeHexColor($('cutColorText').value)) $('cutColorText').value=$('cutColor').value.toUpperCase(); });
  $('closeMapping').addEventListener('click',closeMapping);
  $('cancelMapping').addEventListener('click',closeMapping);
  $('confirmMapping').addEventListener('click',confirmMapping);
  $('sheetSelect').addEventListener('change',e=>{closeMapping();importFile(state.file,e.target.value)});
  syncCutColor(false);
  updateExportLabels();updateList();schedulePreview();
}
document.addEventListener('DOMContentLoaded',init);
