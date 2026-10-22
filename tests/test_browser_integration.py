"""Prueba visual con Chromium usando rutas aisladas para entornos sin localhost."""
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine import preview_info, build_export


def main():
    import base64
    from playwright.sync_api import sync_playwright
    html=(ROOT/'static'/'index.html').read_text().replace('<link rel="stylesheet" href="/style.css" />','').replace('<script defer src="/app.js"></script>','')
    css=(ROOT/'static'/'style.css').read_text()
    js=(ROOT/'static'/'app.js').read_text()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--no-proxy-server'])
        page=browser.new_page(viewport={'width':1440,'height':960},device_scale_factor=1,accept_downloads=True)
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.set_content(html)
        page.add_style_tag(content=css)
        page.expose_function('backendPreview',lambda payload:preview_info(payload))
        def export_backend(kind,payload):
            body,filename,mime=build_export(payload,kind)
            return {'b64':base64.b64encode(body).decode(),'filename':filename,'mime':mime}
        page.expose_function('backendExport',export_backend)
        page.evaluate("""() => {
            window.fetch = async (url,options={}) => {
               const payload=JSON.parse(options.body || '{}');
               if(url==='/api/preview') {
                   try {const result=await window.backendPreview(payload);return {ok:true,json:async()=>result};}
                   catch(error){return {ok:false,json:async()=>({error:String(error)})};}
               }
               if(url.startsWith('/api/export/')) {
                   try {
                       const result=await window.backendExport(url.split('/').pop(),payload);
                       const bytes=Uint8Array.from(atob(result.b64),c=>c.charCodeAt(0));
                       return {ok:true,headers:{get:(k)=>k.toLowerCase()==='content-disposition'?`attachment; filename="${result.filename}"`:null},blob:async()=>new Blob([bytes],{type:result.mime})};
                   } catch(error){return {ok:false,json:async()=>({error:String(error)})};}
               }
               return {ok:false,json:async()=>({error:'Not Found'})};
            };
        }""")
        page.add_script_tag(content=js)
        page.evaluate('init()')
        print('Browser: UI loaded',flush=True)
        page.screenshot(path='/mnt/data/AURA_v1_5_interfaz_vacia.png',full_page=True)
        print('Browser: screenshot done',flush=True)
        page.locator('#demoBtn').click()
        page.wait_for_function("document.querySelector('#totalLabels').textContent === '23'")
        assert page.locator('#capacity').inner_text()=='40'
        assert page.locator('#paperStage svg g path').count()>23  # caracteres convertidos a curvas
        print('Browser: preview done',flush=True)
        page.locator('#factorInput').fill('1.50')
        page.locator('#multiplyBtn').click()
        page.wait_for_function("document.querySelector('#productRows input[data-field=price]').value.includes('52.50')")
        page.locator('#undoBtn').click()
        page.wait_for_function("document.querySelector('#productRows input[data-field=price]').value.includes('35.00')")
        page.locator('#format').select_option('A3')
        page.wait_for_function("document.querySelector('#capacity').textContent === '116'")
        page.locator('#orientation').select_option('landscape')
        page.wait_for_function("document.querySelector('#capacity').textContent === '100'")
        page.locator('#format').select_option('A4')
        page.locator('#orientation').select_option('portrait')
        page.wait_for_function("document.querySelector('#capacity').textContent === '40'")
        print('Browser: price and config done',flush=True)
        with page.expect_download(timeout=15000) as download_event:
            page.locator('#exportSvg').click()
        download=download_event.value
        assert download.suggested_filename.endswith('svg'),download.suggested_filename
        print('Browser: cutting downloaded',flush=True)
        page.locator('#artworkFormat').select_option('svg')
        assert 'SVG de impresión' in page.locator('#exportBundle').inner_text()
        assert 'SVG de impresión' in page.locator('#exportArtwork').inner_text()
        print('Browser: format switched',flush=True)
        with page.expect_download(timeout=15000) as download_art:
            page.locator('#exportArtwork').click()
        assert download_art.value.suggested_filename.endswith('_impresion.svg')
        print('Browser: artwork downloaded',flush=True)
        with page.expect_download(timeout=15000) as download_bundle:
            page.locator('#exportBundle').click()
        assert download_bundle.value.suggested_filename.endswith('.zip')
        print('Browser: bundle downloaded',flush=True)
        page.locator('#artworkFormat').select_option('png')
        assert 'PNG de impresión' in page.locator('#exportArtwork').inner_text()
        page.locator('#productRows input[data-field="labelName"]').first.fill('Collar Corazón')
        page.locator('#productRows input[data-field="labelName"]').first.dispatch_event('change')
        page.wait_for_function("document.querySelector('#nameFitWarning').classList.contains('hidden')")
        assert page.locator('#paperStage svg').count()==1
        print('Browser: label alias updated',flush=True)
        page.screenshot(path='/mnt/data/AURA_v1_5_interfaz_segura.png',full_page=True)
        # La validación rechaza contenido inadecuado antes de dejar exportar.
        page.locator('#productRows input[data-field=labelName]').first.fill('Pulsera Ajustable de Acero Inoxidable Plateada')
        page.locator('#productRows input[data-field=labelName]').first.dispatch_event('change')
        page.wait_for_function("document.querySelector('#nameFitWarning').classList.contains('hidden')===false")
        assert page.locator('#exportBundle').is_disabled()
        assert 'Exportación bloqueada' in page.locator('#nameFitWarning').inner_text()
        page.locator('#productRows input[data-field=labelName]').first.fill('Collar Aurora')
        page.locator('#productRows input[data-field=labelName]').first.dispatch_event('change')
        page.wait_for_function("document.querySelector('#nameFitWarning').classList.contains('hidden')")
        assert page.locator('#exportBundle').is_enabled()
        print('Integración visual y bloqueos: PASS')
        print('A4: 40 etiquetas/pliego | 23 ejemplos visibles')
        print('Edición masiva, deshacer, pliegos, impresión SVG, troquel SVG, ZIP y alias: PASS')
        print('Errores JavaScript:',errors)
        assert not errors,errors
        browser.close()

if __name__=='__main__':main()
