"""Validación geométrica y de exportación para imprenta; sin reintentos manuales."""
from pathlib import Path
import io
import sys
import unittest
import zipfile
import xml.etree.ElementTree as ET
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import (sheet_config, normalize_products, preview_info, page_count, items_on_page,
                    label_position,label_path,build_export,read_table,parse_money, cutting_svg,
                    artwork_svg, centered_layout, safety_boxes, validate_print, text_shape,
                    total_copies, blank_labels_count, ink_size_mm)

NS={'s':'http://www.w3.org/2000/svg'}
SAMPLE=[{'code':'AU-001','name':'Collar Aurora','price':35,'copies':3},
        {'code':'AU-002','name':'Aretes Perla','price':49.9,'copies':2}]

class LabelsTest(unittest.TestCase):
    def test_default_a4_dimensions(self):
        cfg=sheet_config({})
        self.assertEqual((cfg.width,cfg.height,cfg.label_width,cfg.label_height),(210,297,68,12))
        self.assertEqual(cfg.capacity,40)
        self.assertEqual(cfg.safe_margin,2)
        self.assertEqual((cfg.cols,cfg.rows),(2,20))

    def test_a3_custom_and_landscape(self):
        cfg=sheet_config({'format':'A3','orientation':'landscape'})
        self.assertEqual((cfg.width,cfg.height,cfg.capacity),(420,297,100))
        cfg=sheet_config({'format':'PERSONALIZADO','sheetWidth':250,'sheetHeight':350,'safeMargin':3})
        self.assertEqual((cfg.width,cfg.height,cfg.safe_margin),(250,350,3))

    def test_safe_margin_invalid(self):
        for margin in (0,1,1.4,4.1,12):
            with self.assertRaises(ValueError):sheet_config({'safeMargin':margin})
        with self.assertRaises(ValueError):sheet_config({'labelHeight':8,'safeMargin':4})

    def test_pricing_formats(self):
        self.assertEqual(str(parse_money('S/ 1.250,50')),'1250.50')
        self.assertEqual(str(parse_money('1,250.50')),'1250.50')
        self.assertEqual(str(parse_money('35,90')),'35.90')

    def test_excel_and_csv(self):
        from openpyxl import Workbook
        wb=Workbook();ws=wb.active
        ws.append(['Código de joya','Nombre de joya','Precio en soles','Copias'])
        ws.append(['AU-01','Collar Aurora',35,3])
        bio=io.BytesIO();wb.save(bio)
        data=read_table(bio.getvalue(),'joyas.xlsx')
        self.assertEqual(data['mapping']['code'],0)
        self.assertEqual(data['mapping']['price'],2)
        csv='Código de joya;Nombre de joya;Precio en soles;Copias\nAU-01;Collar Aurora;35,00;3\n'.encode()
        self.assertEqual(read_table(csv,'joyas.csv')['rows'][0][1],'Collar Aurora')

    def test_every_ink_bbox_strictly_inside_safe_rectangles(self):
        cfg=sheet_config({})
        for product in normalize_products(SAMPLE + [{'code':'CO-33','name':'Collar Corazón Dorado','copies':1,'price':100}]):
            left,right=safety_boxes(cfg)
            for item in centered_layout(0,0,product,cfg):
                box=left if item['field']=='precio' else right
                xmin,ymin,xmax,ymax=item['bounds']
                self.assertGreaterEqual(xmin,box[0]-.001)
                self.assertGreaterEqual(ymin,box[1]-.001)
                self.assertLessEqual(xmax,box[2]+.001)
                self.assertLessEqual(ymax,box[3]+.001)
                self.assertGreaterEqual(item['point'],5)

    def test_long_names_block_and_custom_short_names_work(self):
        p={'name':'Pulsera Ajustable de Acero Inoxidable Plateada','code':'AU-1','copies':2,'price':35}
        data={'settings':{},'products':[p]}
        info=preview_info(data)
        self.assertFalse(info['validForExport'])
        self.assertEqual(info['nameWarnings'],1)
        self.assertEqual(info['validationErrors'][0]['product'],1)
        with self.assertRaisesRegex(ValueError,'Exportación bloqueada'):
            build_export(data,'artwork-svg')
        p['labelName']='Pulsera Acero'
        info=preview_info(data)
        self.assertTrue(info['validForExport'])
        art,_,_=build_export(data,'artwork-svg')
        self.assertNotIn(b'Inoxidable',art)

    def test_very_long_code_blocked(self):
        data={'settings':{},'products':[{'name':'Collar','code':'A'*80,'copies':1,'price':35}]}
        self.assertFalse(preview_info(data)['validForExport'])
        with self.assertRaisesRegex(ValueError,'código'):
            build_export(data,'svg')

    def test_extreme_price_blocked(self):
        data={'settings':{},'products':[{'name':'Collar','code':'AU-1','copies':1,'price':'999999999999999'}]}
        self.assertFalse(preview_info(data)['validForExport'])

    def test_unsupported_glyph_prevented_without_corrupt_export(self):
        data={'settings':{},'products':[{'name':'Collar 🚀','code':'AU-1','copies':1,'price':35}]}
        self.assertFalse(preview_info(data)['validForExport'])
        with self.assertRaisesRegex(ValueError,'bloqueada'):
            build_export(data,'artwork-svg')

    def test_no_truncation_or_auto_ellipsis(self):
        product=normalize_products([{'name':'Collar Corazón Dorado','code':'AU-1','copies':1,'price':35}])[0]
        parts=[t['text'] for t in centered_layout(0,0,product,sheet_config({})) if t['field']=='nombre']
        self.assertEqual(' '.join(parts),'Collar Corazón Dorado')
        self.assertNotIn('…',' '.join(parts))

    def test_blank_labels_are_truly_blank_on_art_but_contour_on_cut(self):
        data={'settings':{'blankLabels':3,'artworkFormat':'svg'}, 'products':[{'name':'Collar','code':'AU-1','copies':37,'price':30}]}
        info=preview_info(data)
        self.assertEqual(info['copies'],40)
        self.assertEqual(info['remainingToFillLastPage'],0)
        art,_,_=build_export(data,'artwork-svg')
        cut,_,_=build_export(data,'svg')
        root=ET.fromstring(art)
        self.assertEqual(len(root.findall('.//s:g[@id="ARTE_PARA_IMPRIMIR"]',NS)),1)
        self.assertEqual(len(ET.fromstring(cut).findall('.//s:path',NS)),40)
        # No rectángulos de seguridad ni troqueles visibles en el arte final.
        self.assertEqual(len(root.findall('.//s:rect',NS)),1) # fondo blanco único
        self.assertNotIn('LINEAS_DE_CORTE',art.decode())

    def test_blank_only(self):
        payload={'settings':{'blankLabels':12},'products':[]}
        self.assertTrue(preview_info(payload)['validForExport'])
        art,_,_=build_export(payload,'artwork-svg')
        cut,_,_=build_export(payload,'svg')
        root=ET.fromstring(art)
        self.assertFalse(root.find('.//s:g[@id="ARTE_PARA_IMPRIMIR"]',NS).findall('.//s:path',NS))
        self.assertEqual(len(ET.fromstring(cut).findall('.//s:path',NS)),12)

    def test_custom_color_and_cut_svg_clean(self):
        data={'settings':{'cutColor':'#00AACC'},'products':SAMPLE}
        cut,name,mime=build_export(data,'svg')
        self.assertEqual(name,'AURA_pliego_01_troquel.svg')
        root=ET.fromstring(cut)
        self.assertEqual(root.attrib['width'],'210.0mm')
        self.assertEqual(root.attrib['height'],'297.0mm')
        self.assertEqual(root.find('.//s:g[@id="LINEAS_DE_CORTE"]',NS).attrib['stroke'],'#00aacc')
        self.assertEqual(len(root.findall('.//s:path',NS)),5)
        self.assertEqual(len(root.findall('.//s:text',NS)),0)
        self.assertTrue(all(v.attrib['d'].strip().endswith('Z') for v in root.findall('.//s:path',NS)))
        self.assertNotIn('fill="#',cut.decode())

    def test_artwork_vector_is_only_ink_without_cut_or_font_dependency(self):
        data={'settings':{'borderPrint':True},'products':SAMPLE}
        art,_,_=build_export(data,'artwork-svg')
        root=ET.fromstring(art)
        self.assertEqual(len(root.findall('.//s:text',NS)),0)
        self.assertGreater(len(root.findall('.//s:path',NS)),5)
        self.assertEqual(len(root.findall('.//s:rect',NS)),1)
        self.assertNotIn('font-family',art.decode())
        self.assertNotIn('LINEAS_DE_CORTE',art.decode())
        self.assertNotIn('stroke=',art.decode())
        self.assertNotIn('stroke-width',art.decode())
        self.assertNotIn('troquel.svg',art.decode())

    def test_png_600_dpi_and_unprinted_outer_border(self):
        data={'settings':{},'products':SAMPLE}
        png,name,mime=build_export(data,'png')
        self.assertEqual(name,'AURA_pliego_01_impresion.png')
        image=Image.open(io.BytesIO(png))
        self.assertEqual(image.size,(4961,7016))
        self.assertAlmostEqual(image.info['dpi'][0],600,delta=.1)
        # Un punto sobre el borde geométrico es blanco: NO se imprime el corte.
        cfg=sheet_config({});x,y=label_position(cfg,0)
        at=(round((x+5)*600/25.4),round(y*600/25.4))
        self.assertEqual(image.getpixel(at),(255,255,255))

    def test_register_and_zip_multipage(self):
        data={'settings':{'artworkFormat':'svg','dpi':150,'blankLabels':2,'cutColor':'#002244'},
              'products':[{'name':'Anillo Luna','code':'AU-1','price':50,'copies':79}]}
        info=preview_info(data)
        self.assertTrue(info['validForExport'])
        self.assertEqual(info['pages'],3)
        zip_bytes,filename,mime=build_export(data,'zip')
        self.assertEqual(mime,'application/zip')
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            self.assertEqual(len([x for x in z.namelist() if x.endswith('_troquel.svg')]),3)
            self.assertEqual(len([x for x in z.namelist() if x.endswith('_impresion.svg')]),3)
            for page in (1,2,3):
                a=ET.fromstring(z.read(f'AURA_pliego_{page:02d}_impresion.svg'))
                b=ET.fromstring(z.read(f'AURA_pliego_{page:02d}_troquel.svg'))
                self.assertEqual(a.attrib['viewBox'],b.attrib['viewBox'])
                self.assertEqual(a.attrib['width'],b.attrib['width'])
                self.assertEqual(a.attrib['height'],b.attrib['height'])
            self.assertIn('Margen interior de seguridad: 2.0 mm',z.read('LEEME.txt').decode())

    def test_total_label_limit(self):
        cfg=sheet_config({})
        with self.assertRaisesRegex(ValueError,'total de etiquetas'):
            page_count(normalize_products([{'name':'Collar','code':'AU-1','price':10,'copies':1000}]),cfg,20000)

    def test_safety_margin_reduces_room_not_geom(self):
        c1=sheet_config({'safeMargin':1.5})
        c2=sheet_config({'safeMargin':3})
        self.assertGreater(safety_boxes(c1)[1][2]-safety_boxes(c1)[1][0],
                           safety_boxes(c2)[1][2]-safety_boxes(c2)[1][0])
        self.assertEqual(label_path(0,0,c1.label_width,c1.label_height),
                         label_path(0,0,c2.label_width,c2.label_height))

    def test_no_export_without_labels(self):
        with self.assertRaisesRegex(ValueError,'No hay etiquetas'):
            build_export({'settings':{},'products':[]},'svg')

if __name__=='__main__':unittest.main()
