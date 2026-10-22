"""Motor de pliegos de etiquetas. Unidades geométricas: milímetros."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from io import BytesIO, StringIO
from itertools import islice
import csv
import math
import re
import unicodedata
import zipfile
from functools import lru_cache
from fontTools.ttLib import TTFont
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

MM_PER_INCH = 25.4
FORMATS = {"A4": (210.0, 297.0), "A3": (297.0, 420.0), "CARTA": (215.9, 279.4), "PERSONALIZADO": None}
MAX_UPLOAD_MB = 15
MAX_LABELS = 20000
MAX_PAGES = 500


def num(value: Any, default: float = 0) -> float:
    if isinstance(value, bool):
        return default
    try:
        f = float(value)
        return f if math.isfinite(f) else default
    except (ValueError, TypeError, OverflowError):
        return default


def int_num(value: Any, default=0) -> int:
    f = num(value, float(default))
    return int(f) if float(f).is_integer() else default


def parse_money(raw: Any) -> Decimal:
    if raw is None or str(raw).strip() == "":
        return Decimal("0.00")
    if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
        value = str(raw)
    else:
        value = re.sub(r"[^0-9,.\-]", "", str(raw))
        if "," in value and "." in value:
            if value.rfind(",") > value.rfind("."):
                value = value.replace(".", "").replace(",", ".")
            else:
                value = value.replace(",", "")
        elif "," in value:
            if value.count(",") > 1:
                value = value.replace(",", "")
            else:
                left, right = value.split(",")
                value = left + ("" if len(right) == 3 and len(left) >= 1 else ".") + right
        elif "." in value and value.count(".") == 1:
            left, right = value.split(".")
            if len(right) == 3 and len(left) >= 1:
                value = left + right
        elif value.count(".") > 1:
            value = value.replace(".", "")
    try:
        result = Decimal(value)
        if not result.is_finite() or result < 0:
            raise ValueError("El precio debe ser mayor o igual que cero.")
        return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Precio inválido: {raw!s}") from e


def clean_text(value: Any, max_length=120) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()[:max_length]


def norm(s: Any) -> str:
    s = unicodedata.normalize("NFKD", str(s).strip().lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


ALIASES = {
    "code": ["codigo joya", "codigo de joya", "codigo de producto", "codigo producto", "codigo", "sku", "referencia", "ref", "code", "cod", "id producto", "id"],
    "name": ["nombre de joya", "nombre joya", "nombre producto", "nombre de producto", "nombre", "producto", "descripcion", "articulo", "joya", "item", "name"],
    "price": ["precio de venta", "precio venta", "precio en soles", "precio unitario", "precio final", "precio", "pvp", "venta", "price", "s soles"],
    "copies": ["cantidad de etiquetas", "cantidad etiquetas", "copias", "etiquetas", "imprimir", "cantidad a imprimir", "unidades a imprimir", "cantidad", "copies"],
}


def suggest_columns(headers: list[str]) -> dict[str, int | None]:
    result = {k: None for k in ALIASES}
    normalized = [norm(h) for h in headers]
    used = set()
    for k, aliases in ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                i = normalized.index(alias)
                if i not in used:
                    result[k] = i
                    used.add(i)
                    break
        if result[k] is None:
            for i, text in enumerate(normalized):
                if i in used or len(text) < 4:
                    continue
                if any((text.startswith(a + " ") or text.endswith(" " + a)) for a in aliases if len(a) >= 5):
                    result[k] = i
                    used.add(i)
                    break
    return result


def read_table(data: bytes, filename: str, sheet: str | None = None) -> dict:
    filename = filename.lower()
    if filename.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
        try:
            sheets = wb.sheetnames
            if sheet and sheet not in sheets:
                raise ValueError("No se encontró esa hoja del Excel.")
            chosen = sheet or next((n for n in sheets if wb[n].max_row and wb[n].max_row > 1), sheets[0])
            matrix = list(islice(wb[chosen].iter_rows(values_only=True), 5001))
        finally:
            wb.close()
    elif filename.endswith(".xls"):
        try:
            import xlrd
        except ImportError as e:
            raise ValueError("Para .xls antiguo, instala la dependencia xlrd del requirements.txt.") from e
        wb = xlrd.open_workbook(file_contents=data)
        sheets = wb.sheet_names()
        if sheet and sheet not in sheets:
            raise ValueError("No se encontró esa hoja del Excel.")
        chosen = sheet or sheets[0]
        sh = wb.sheet_by_name(chosen)
        matrix = [sh.row_values(i) for i in range(min(sh.nrows, 5001))]
    elif filename.endswith(".csv"):
        chosen, sheets = "CSV", ["CSV"]
        try:
            decoded = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            decoded = data.decode("cp1252")
        snippet = decoded[:10000]
        try:
            dialect = csv.Sniffer().sniff(snippet, delimiters=",;\t|")
            dialect_delim = dialect.delimiter
        except csv.Error:
            dialect_delim = ";" if snippet.count(";") > snippet.count(",") else ","
        matrix = list(csv.reader(StringIO(decoded), delimiter=dialect_delim))[:5001]
    else:
        raise ValueError("Formato no admitido. Usa .xlsx, .xlsm, .xls o .csv.")
    matrix = [list(r) for r in matrix if any(v is not None and str(v).strip() for v in r)]
    if not matrix:
        raise ValueError("El archivo no contiene filas con información.")
    width = max(len(r) for r in matrix)
    header_candidates = [clean_text(v, 80) for v in matrix[0]]
    guess = suggest_columns(header_candidates)
    recognized = sum(x is not None for x in guess.values()) >= 2
    if recognized:
        headers = [h or f"Columna {i + 1}" for i, h in enumerate(header_candidates)]
        content = matrix[1:]
    else:
        headers = [f"Columna {i + 1}" for i in range(width)]
        content = matrix
        guess = {k: None for k in ALIASES}
    rows = []
    for row in content:
        row = list(row) + [None] * (width - len(row))
        if not any(v is not None and str(v).strip() for v in row):
            continue
        rows.append([clean_text(v, 250) for v in row])
    return {"headers": headers, "rows": rows, "mapping": guess, "sheet": chosen, "sheets": sheets, "truncated": len(matrix) >= 5001}


@dataclass(frozen=True)
class Sheet:
    width: float
    height: float
    label_width: float
    label_height: float
    margin: float
    gap_x: float
    gap_y: float
    cols: int
    rows: int
    start_x: float
    font_price: float
    font_name: float
    font_code: float
    border_print: bool  # solo guías en vista previa, NUNCA en arte final
    dpi: int
    safe_margin: float

    @property
    def capacity(self):
        return self.cols * self.rows


def sheet_config(raw: dict[str, Any]) -> Sheet:
    fmt = str(raw.get("format", "A4")).upper()
    if fmt not in FORMATS:
        raise ValueError("Formato de hoja no válido.")
    default_dims = FORMATS[fmt]
    w = num(raw.get("sheetWidth"), 210) if default_dims is None else default_dims[0]
    h = num(raw.get("sheetHeight"), 297) if default_dims is None else default_dims[1]
    if str(raw.get("orientation", "portrait")) == "landscape":
        w, h = max(w, h), min(w, h)
    else:
        w, h = min(w, h), max(w, h)
    lw, lh = num(raw.get("labelWidth"), 68), num(raw.get("labelHeight"), 12)
    margin = num(raw.get("margin"), 8)
    gx, gy = num(raw.get("gapX"), 2), num(raw.get("gapY"), 2)
    fonts = [num(raw.get(k), default) for k, default in (("fontPrice", 9), ("fontName", 7.4), ("fontCode", 6.6))]
    dpi = int_num(raw.get("dpi"), 600)
    safe_margin = num(raw.get("safeMargin"), 2.0)
    if not 50 <= w <= 1000 or not 50 <= h <= 1000:
        raise ValueError("La hoja debe medir entre 50 y 1000 mm por lado.")
    if not 35 <= lw <= 250 or not 8 <= lh <= 60 or lw/lh < 3.2:
        raise ValueError("La etiqueta debe medir entre 35–250 mm de ancho y 8–60 mm de alto, con proporción alargada.")
    if not 0 <= margin <= 40 or gx < 0 or gy < 0 or gx > 30 or gy > 30:
        raise ValueError("Los márgenes y separaciones deben ser positivos y razonables.")
    if any(not 3 <= f <= 25 for f in fonts):
        raise ValueError("Las tipografías deben medir entre 3 y 25 puntos.")
    if not 1.5 <= safe_margin <= 4:
        raise ValueError("El margen de seguridad debe ser de 1,5 a 4 mm; por defecto 2 mm.")
    if 2 * safe_margin >= lh:
        raise ValueError("Margen de seguridad demasiado grande para la altura de la etiqueta.")
    if dpi not in (150, 300, 600):
        raise ValueError("Selecciona 150, 300 o 600 DPI.")
    usable_w, usable_h = w - 2*margin, h - 2*margin
    cols = math.floor((usable_w + gx + 1e-6)/(lw + gx))
    rows = math.floor((usable_h + gy + 1e-6)/(lh + gy))
    if cols <= 0 or rows <= 0:
        raise ValueError("No cabe ninguna etiqueta: reduce tamaño, márgenes o separaciones.")
    block_w = cols*lw + (cols-1)*gx
    return Sheet(w, h, lw, lh, margin, gx, gy, cols, rows, (w-block_w)/2, *fonts, bool(raw.get("borderPrint", True)), dpi, safe_margin)


def normalize_products(products: list[dict]) -> list[dict]:
    if not isinstance(products, list):
        raise ValueError("Lista de productos inválida.")
    result = []
    total = 0
    if len(products) > 5000:
        raise ValueError("Importa un máximo de 5000 productos por lote.")
    for i, row in enumerate(products):
        if not isinstance(row, dict):
            raise ValueError(f"Producto {i+1} no válido.")
        if any(len(str(row.get(k) or '')) > limit for k, limit in [('code',80),('name',120),('labelName',120)]):
            raise ValueError(f'Fila {i+1}: texto demasiado largo para importar sin pérdida. Acorta los campos a 80 caracteres de código y 120 de nombre.')
        code = clean_text(row.get("code"), 80)
        name = clean_text(row.get("name"), 120)
        label_name = clean_text(row.get("labelName"), 120)
        if not name and not code:
            continue
        copies = int_num(row.get("copies", 1), -1)
        if copies < 0 or copies > 2000:
            raise ValueError(f"Copias inválidas en producto {i+1} (0 a 2000).")
        price = parse_money(row.get("price", 0))
        if copies:
            result.append({"code": code, "name": name, "labelName": label_name, "price": price, "copies": copies})
            total += copies
        if total > MAX_LABELS:
            raise ValueError(f"Máximo {MAX_LABELS:,} etiquetas por exportación. Divide tu lote.")
    return result


def blank_labels_count(raw: dict[str, Any]) -> int:
    count = int_num(raw.get('blankLabels', 0), -1)
    if count < 0 or count > MAX_LABELS:
        raise ValueError(f'Las etiquetas vacías deben estar entre 0 y {MAX_LABELS:,}.')
    return count


def total_copies(products: list[dict], extra_blank: int = 0) -> int:
    return sum(p['copies'] for p in products) + extra_blank


def page_count(products: list[dict], cfg: Sheet, extra_blank: int = 0) -> int:
    count = total_copies(products, extra_blank)
    if count > MAX_LABELS:
        raise ValueError(f'El total de etiquetas, incluidas las vacías, no puede superar {MAX_LABELS:,}.')
    n = math.ceil(count / cfg.capacity) if count else 0
    if n > MAX_PAGES:
        raise ValueError(f"El lote ocuparía más de {MAX_PAGES} pliegos. Aumenta tamaño de hoja o divide el lote.")
    return n


def items_on_page(products: list[dict], cfg: Sheet, page: int, extra_blank: int = 0) -> list[tuple[int, dict | None]]:
    first, last = page * cfg.capacity, (page + 1)*cfg.capacity
    position = 0
    items: list[tuple[int, dict | None]] = []
    for product in products:
        next_pos = position + product['copies']
        if next_pos > first and position < last:
            for absolute in range(max(first, position), min(last, next_pos)):
                items.append((absolute-first, product))
        position = next_pos
        if position >= last:
            break
    blank_start, blank_end = position, position + extra_blank
    if blank_end > first and blank_start < last:
        for absolute in range(max(first, blank_start), min(last, blank_end)):
            items.append((absolute-first, None))
    return items


def label_position(cfg: Sheet, slot: int) -> tuple[float, float]:
    row, col = divmod(slot, cfg.cols)
    return cfg.start_x + col*(cfg.label_width+cfg.gap_x), cfg.margin + row*(cfg.label_height+cfg.gap_y)


def label_path(x: float, y: float, w: float, h: float) -> str:
    """Curva cerrada para el troquel; todas las coordenadas absolutas en mm."""
    r = min(h*.23, 3.4)
    sa, ns, ne, sb = .355*w, .405*w, .595*w, .645*w
    d = .24*h
    f = lambda a: f"{a:.4f}".rstrip("0").rstrip(".") if a != 0 else "0"
    xy = lambda a, b: f"{f(x+a)} {f(y+b)}"
    return (f"M {xy(r,0)} L {xy(sa,0)} L {xy(ns,d)} "
            f"L {xy(ne,d)} L {xy(sb,0)} L {xy(w-r,0)} "
            f"Q {xy(w,0)} {xy(w,r)} L {xy(w,h-r)} "
            f"Q {xy(w,h)} {xy(w-r,h)} L {xy(sb,h)} "
            f"L {xy(ne,h-d)} L {xy(ns,h-d)} L {xy(sa,h)} "
            f"L {xy(r,h)} Q {xy(0,h)} {xy(0,h-r)} "
            f"L {xy(0,r)} Q {xy(0,0)} {xy(r,0)} Z")


def label_points(x: float, y: float, w: float, h: float, steps=16) -> list[tuple[float, float]]:
    r = min(h*.23, 3.4)
    sa, ns, ne, sb = .355*w, .405*w, .595*w, .645*w
    d = .24*h
    ps = [(r,0),(sa,0),(ns,d),(ne,d),(sb,0),(w-r,0)]
    def quad(start, ctrl, end):
        for i in range(1, steps+1):
            t = i/steps
            ps.append(((1-t)**2*start[0] + 2*(1-t)*t*ctrl[0] + t*t*end[0],
                       (1-t)**2*start[1] + 2*(1-t)*t*ctrl[1] + t*t*end[1]))
    quad((w-r,0),(w,0),(w,r))
    ps.append((w,h-r))
    quad((w,h-r),(w,h),(w-r,h))
    ps.extend([(sb,h),(ne,h-d),(ns,h-d),(sa,h),(r,h)])
    quad((r,h),(0,h),(0,h-r))
    ps.append((0,r))
    quad((0,r),(0,0),(r,0))
    ps.append((r,0))
    return [(x+px,y+py) for px,py in ps]


def format_price(value: Decimal) -> str:
    return f"S/ {value:,.2f}"


def platform_font(bold=False) -> str:
    """Mismo archivo TTF en Render y local; no se exportan archivos de fuentes."""
    import os
    paths = (["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", r"C:\Windows\Fonts\DejaVuSans-Bold.ttf", r"C:\Windows\Fonts\arialbd.ttf"]
             if bold else ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", r"C:\Windows\Fonts\DejaVuSans.ttf", r"C:\Windows\Fonts\arial.ttf"])
    return next((q for q in paths if os.path.isfile(q)),
                "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")


@lru_cache(maxsize=2)
def font_data(bold: bool):
    font = TTFont(platform_font(bold), lazy=False)
    return font, font.getBestCmap(), font.getGlyphSet(), font['hmtx'].metrics, font['head'].unitsPerEm


@lru_cache(maxsize=4096)
def glyph_bbox(bold: bool, glyph_name: str):
    glyphs = font_data(bold)[2]
    pen = BoundsPen(glyphs)
    glyphs[glyph_name].draw(pen)
    return pen.bounds


@lru_cache(maxsize=4096)
def glyph_svg(bold: bool, glyph_name: str) -> str:
    glyphs = font_data(bold)[2]
    pen = SVGPathPen(glyphs)
    glyphs[glyph_name].draw(pen)
    return pen.getCommands()


@lru_cache(maxsize=16000)
def text_shape(text: str, bold: bool):
    _, cmap, _, metrics, units = font_data(bold)
    position = 0
    bounds = None
    locations = []
    for c in text:
        glyph_name = cmap.get(ord(c))
        if glyph_name is None:
            raise ValueError(f"Carácter no admitido en etiquetas: {c!r}. Reemplázalo por una letra o símbolo común.")
        gb = glyph_bbox(bold, glyph_name)
        if gb is not None:
            q = (gb[0]+position, gb[1], gb[2]+position, gb[3])
            bounds = q if bounds is None else (min(bounds[0],q[0]),min(bounds[1],q[1]),max(bounds[2],q[2]),max(bounds[3],q[3]))
        locations.append((glyph_name,position))
        position += metrics[glyph_name][0]
    return tuple(locations), bounds or (0,0,0,0), units


def ink_size_mm(value: str, point: float, bold: bool) -> tuple[float,float]:
    _, (left,bot,right,top), units = text_shape(value,bold)
    scale = point * MM_PER_INCH / (72 * units)
    return (right-left)*scale, (top-bot)*scale


def safety_boxes(cfg: Sheet):
    """Rectángulos contenidos en los dos lóbulos, excluyendo la garganta central.
    El margen se aplica también a las esquinas redondeadas (radio incluido).
    """
    w,h,s = cfg.label_width,cfg.label_height,cfg.safe_margin
    radius = min(h*.23,3.4)
    left=(radius+s,s,.355*w-s,h-s)
    right=(.645*w+s,s,w-radius-s,h-s)
    if min(left[2]-left[0],right[2]-right[0]) < 5:
        raise ValueError("La zona de texto es demasiado estrecha: aumenta el ancho o reduce el margen interior.")
    return left,right


def fitting_line(value: str, max_mm: float, max_pt: float, min_pt: float, bold: bool) -> float | None:
    if not value:
        return max_pt
    candidate = max_pt
    while candidate >= min_pt - 0.001:
        w,_=ink_size_mm(value,candidate,bold)
        if w <= max_mm - .05:
            return round(candidate,3)
        candidate -= .1
    return None


def fitting_name(name: str, available: float, max_pt: float, minimum: float):
    """Nunca trunca ni introduce puntos suspensivos silenciosamente."""
    words = ' '.join((name or '').split()).split(' ')
    if words == ['']:
        return [], max_pt
    whole = ' '.join(words)
    one = fitting_line(whole,available,max_pt,minimum,True)
    if one is not None and one >= max_pt-.2:
        return [whole],one
    best = None
    for split in range(1,len(words)):
        pair=[' '.join(words[:split]),' '.join(words[split:])]
        pt=min(max_pt,12)
        while pt >= minimum - .001:
            if all(ink_size_mm(line,pt,True)[0] <= available-.05 for line in pair):
                choice=(round(pt,3), -abs(ink_size_mm(pair[0],pt,True)[0]-ink_size_mm(pair[1],pt,True)[0]),pair)
                if best is None or choice[:2]>best[:2]: best=choice
                break
            pt-=.1
    if best and (one is None or best[0] > one + .3): return best[2],best[0]
    if one is not None:return [whole],one
    raise ValueError('El nombre no cabe en dos líneas legibles. Usa «Nombre en etiqueta» para abreviarlo.')


def centered_layout(x: float, y: float, product: dict, cfg: Sheet):
    """Elementos tipográficos y cotas de tinta verificadas EN MM antes de exportar.
    SVG usa las curvas del TTF, por eso no depende de fuentes de la imprenta.
    """
    left,right=safety_boxes(cfg)
    p=product['price']
    price=format_price(p)
    code=product['code'].strip()
    name=product.get('labelName') or product['name']
    if not name.strip():
        raise ValueError('El nombre está vacío; escribe un nombre para la etiqueta.')
    if not code:
        raise ValueError('El código está vacío; completa el código antes de exportar.')
    pricept=fitting_line(price,left[2]-left[0],cfg.font_price,5.0,True)
    codept=fitting_line(code,right[2]-right[0],cfg.font_code,5.0,False)
    if pricept is None: raise ValueError('El precio no cabe en el espacio seguro. Aumenta ancho de etiqueta o usa un precio más corto.')
    if codept is None: raise ValueError('El código no cabe de forma legible. Utiliza un código más corto.')
    lines,namept=fitting_name(name,right[2]-right[0],cfg.font_name,5.0)
    # Para etiquetas compactas con 2 líneas usar centros verticales seguros
    ylow,yhigh=left[1],left[3]
    height=yhigh-ylow
    # Centros (se verifican contra las cotas de tinta reales)
    price_center=(ylow+yhigh)/2
    if len(lines)==1:
        offsets=[.31,.73]  # nombre y código
    else:
        offsets=[.17,.48,.83]  # nombre línea 1, 2, código
    design=[]
    def place(value,cx,cy,pt,bold,color,box,field):
        if not value:return
        iw,ih=ink_size_mm(value,pt,bold)
        x0,y0,x1,y1=box
        if cx-iw/2 < x0-.001 or cx+iw/2 > x1+.001 or cy-ih/2 < y0-.001 or cy+ih/2>y1+.001:
            raise ValueError(f'El {field} invadiría el margen de seguridad. Abrevia el contenido o aumenta la etiqueta.')
        design.append({'text':value,'x':x+cx,'y':y+cy,'point':pt,'bold':bold,'color':color,
                       'bounds':(x+cx-iw/2,y+cy-ih/2,x+cx+iw/2,y+cy+ih/2),'field':field})
    place(price,(left[0]+left[2])/2,price_center,pricept,True,'#24201b',left,'precio')
    cx=(right[0]+right[2])/2
    if len(lines)==1:
        place(lines[0],cx,ylow+offsets[0]*height,namept,True,'#24201b',right,'nombre')
        place(code,cx,ylow+offsets[1]*height,codept,False,'#575149',right,'código')
    else:
        for line,offset in zip(lines, offsets[:2]):
            place(line,cx,ylow+offset*height,namept,True,'#24201b',right,'nombre')
        place(code,cx,ylow+offsets[2]*height,codept,False,'#575149',right,'código')
    for a,b in zip(design,design[1:]):
        if a['field']=='precio' or b['field']=='precio':continue
        if a['bounds'][3]+.18 > b['bounds'][1]:
            raise ValueError('Nombre y código quedan demasiado juntos. Usa un nombre más corto o aumenta la altura.')
    return design


def validate_print(products: list[dict],cfg: Sheet):
    safety_boxes(cfg)
    issues=[]
    by_identity=set()
    for idx,p in enumerate(products,1):
        identity=(p['code'],p['name'],p.get('labelName',''),str(p['price']))
        if identity in by_identity: continue
        by_identity.add(identity)
        try:centered_layout(0,0,p,cfg)
        except ValueError as e:
            issues.append({'product':idx,'code':p['code'],'name':p['name'],'message':str(e)})
    return issues


def require_safe_print(products: list[dict], cfg: Sheet):
    issues=validate_print(products,cfg)
    if issues:
        first=issues[0]
        raise ValueError(f"Exportación bloqueada: {len(issues)} producto(s) incumplen el margen seguro. "
                         f"Fila {first['product']} ({first['code'] or first['name']}): {first['message']}")


def text_elements(x:float,y:float,product:dict,cfg:Sheet,dpi_for_measure=300)->list[dict]:
    return centered_layout(x,y,product,cfg)


def glyph_outline(t: dict) -> str:
    glyphs,bounds,units=text_shape(t['text'],t['bold'])
    mm_per_font_unit=t['point']*MM_PER_INCH/(72*units)
    cx=(bounds[0]+bounds[2])/2
    cy=(bounds[1]+bounds[3])/2
    components=[]
    for glyph,advance in glyphs:
        d=glyph_svg(t['bold'],glyph)
        if d:
            components.append(f'<path transform="translate({advance} 0)" d="{escape(d,quote=True)}"/>')
    return (f'<g fill="{t["color"]}" transform="translate({t["x"]:.5f} {t["y"]:.5f}) '
            f'scale({mm_per_font_unit:.10f} {-mm_per_font_unit:.10f}) translate({-cx:.4f} {-cy:.4f})">'
            +''.join(components)+'</g>')


def preview_svg(products:list[dict],cfg:Sheet,page:int,extra_blank:int=0) -> str:
    page_num=page_count(products,cfg,extra_blank)
    if page_num==0:page=0
    elif not 0 <= page < page_num: raise ValueError('Número de página fuera de rango.')
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{cfg.width}mm" height="{cfg.height}mm" viewBox="0 0 {cfg.width} {cfg.height}">',
           f'<rect width="{cfg.width}" height="{cfg.height}" fill="white"/>']
    for slot,p in items_on_page(products,cfg,page,extra_blank):
        x,y=label_position(cfg,slot)
        if cfg.border_print:
            parts.append(f'<path d="{label_path(x,y,cfg.label_width,cfg.label_height)}" fill="none" stroke="#d67d5e" stroke-width=".1" stroke-dasharray=".8 .5"/>')
            left,right=safety_boxes(cfg)
            for box in (left,right):
                parts.append(f'<rect x="{x+box[0]:.4f}" y="{y+box[1]:.4f}" width="{box[2]-box[0]:.4f}" height="{box[3]-box[1]:.4f}" fill="none" stroke="#84a8a1" stroke-dasharray=".5 .5" stroke-width=".08"/>')
        if p is not None:
            for t in centered_layout(x,y,p,cfg):parts.append(glyph_outline(t))
    parts.append('</svg>')
    return '\n'.join(parts)


def artwork_svg(products:list[dict],cfg:Sheet,page:int,extra_blank:int=0)->str:
    parts=['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg xmlns="http://www.w3.org/2000/svg" width="{cfg.width}mm" height="{cfg.height}mm" viewBox="0 0 {cfg.width} {cfg.height}">',
           '<title>AURA - Arte de impresión seguro, textos convertidos a curvas</title>',
           '<desc>Solo tinta interior. Sin contornos imprimibles, guías o rutas de troquel.</desc>',
           f'<rect width="{cfg.width}" height="{cfg.height}" fill="white"/>', '<g id="ARTE_PARA_IMPRIMIR">']
    for slot,p in items_on_page(products,cfg,page,extra_blank):
        if p is None:continue
        x,y=label_position(cfg,slot)
        for t in centered_layout(x,y,p,cfg):parts.append(glyph_outline(t))
    parts+=['</g>','</svg>']
    return '\n'.join(parts)+'\n'


def cut_color(raw: dict) -> str:
    value = str(raw.get("cutColor", "#FF0000")).strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError("El color del troquel debe ser un código hexadecimal como #FF0000.")
    return value.lower()

def artwork_format(raw: dict) -> str:
    fmt = str(raw.get('artworkFormat', 'png')).strip().lower()
    if fmt not in ('png', 'svg'):
        raise ValueError('Elige PNG o SVG para el diseño de impresión.')
    return fmt


def cutting_svg(products: list[dict], cfg: Sheet, page: int, stroke_color: str = '#ff0000', extra_blank: int = 0) -> str:
    paths = []
    for slot,_ in items_on_page(products,cfg,page,extra_blank):
        x,y = label_position(cfg,slot)
        paths.append(f'  <path d="{label_path(x,y,cfg.label_width,cfg.label_height)}"/>')
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{cfg.width}mm" height="{cfg.height}mm" '
            f'viewBox="0 0 {cfg.width} {cfg.height}">\n'
            f'  <title>Troquel de etiquetas – página {page+1}</title>\n'
            f'  <!-- Milímetros reales. Solo contornos de corte, sin textos ni rellenos. -->\n'
            f'  <g id="LINEAS_DE_CORTE" fill="none" stroke="{stroke_color}" stroke-width="0.1" stroke-linejoin="round">\n'
            + "\n".join(paths) + '\n  </g>\n</svg>\n')


@lru_cache(maxsize=256)
def get_font(pt: float, dpi: int, bold: bool=False):
    return ImageFont.truetype(platform_font(bold), max(6, round(pt*dpi/72)))


def artwork_png(products: list[dict], cfg: Sheet, page: int, extra_blank: int = 0) -> bytes:
    dpi = cfg.dpi
    px = dpi / MM_PER_INCH
    width,height = round(cfg.width*px),round(cfg.height*px)
    if width * height > 125_000_000:
        raise ValueError("Imagen demasiado grande. Reduce tamaño de hoja o resolución.")
    img = Image.new("RGB", (width,height), "white")
    draw = ImageDraw.Draw(img)
    for slot, product in items_on_page(products,cfg,page,extra_blank):
        if product is None:continue
        x,y=label_position(cfg,slot)
        for t in centered_layout(x,y,product,cfg):
            font=get_font(t['point'],dpi,t['bold'])
            # Centrado por tinta real para evitar desplazamientos por métricas de fuentes.
            bbox=draw.textbbox((0,0),t['text'],font=font,features=['-kern'])
            width,height=bbox[2]-bbox[0],bbox[3]-bbox[1]
            pos=(t['x']*px-(bbox[0]+bbox[2])/2,t['y']*px-(bbox[1]+bbox[3])/2)
            draw.text(pos,t['text'],font=font,fill=t['color'],features=['-kern'])
    bio=BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Software", "AURA Etiquetas")
    metadata.add_text("Dimensions", f"{cfg.width} x {cfg.height} mm; {dpi} DPI")
    img.save(bio,format="PNG",dpi=(dpi,dpi),pnginfo=metadata,optimize=False,compress_level=6)
    img.close()
    return bio.getvalue()


def build_export(payload: dict, kind: str) -> tuple[bytes, str, str]:
    if kind not in ('png', 'artwork-svg', 'svg', 'zip'):
        raise ValueError('Tipo de exportación inválido.')
    settings = payload.get('settings', {})
    cfg = sheet_config(settings)
    products = normalize_products(payload.get('products', []))
    extra_blank = blank_labels_count(settings)
    pages = page_count(products, cfg, extra_blank)
    if not pages:
        raise ValueError('No hay etiquetas con copias mayores que cero o etiquetas vacías adicionales.')
    print_format = artwork_format(settings)
    stroke_color = cut_color(settings)
    require_safe_print(products,cfg)

    def base(i):
        return f'AURA_pliego_{i+1:02d}'

    def artwork(i, fmt):
        if fmt == 'png':
            return artwork_png(products, cfg, i, extra_blank), 'png', 'image/png'
        return artwork_svg(products, cfg, i, extra_blank).encode('utf-8'), 'svg', 'image/svg+xml'

    if pages == 1:
        if kind == 'svg':
            return cutting_svg(products, cfg, 0, stroke_color, extra_blank).encode('utf-8'), base(0) + '_troquel.svg', 'image/svg+xml'
        if kind in ('png', 'artwork-svg'):
            content, ext, mime = artwork(0, 'svg' if kind == 'artwork-svg' else 'png')
            return content, base(0) + '_impresion.' + ext, mime
        # ZIP de una hoja: entregar los dos archivos separados.

    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for page in range(pages):
            if kind in ('zip', 'png', 'artwork-svg'):
                fmt = print_format if kind == 'zip' else ('png' if kind == 'png' else 'svg')
                content, ext, _ = artwork(page, fmt)
                archive.writestr(base(page) + '_impresion.' + ext, content,
                                 compress_type=zipfile.ZIP_STORED if fmt == 'png' else zipfile.ZIP_DEFLATED)
            if kind in ('zip', 'svg'):
                archive.writestr(base(page) + '_troquel.svg', cutting_svg(products, cfg, page, stroke_color, extra_blank))
        if kind == 'zip':
            instructions = (
                'AURA - ARCHIVOS PARA IMPRESION Y TROQUELADO\n'
                f'Pliego: {cfg.width} x {cfg.height} mm\n'
                f'Etiqueta: {cfg.label_width} x {cfg.label_height} mm\n'
                f'Formato de impresión: {print_format.upper()}\n'
                f'Resolución PNG (si aplica): {cfg.dpi} DPI\n'
                f'Pliegos: {pages}\n'
                f'Etiquetas vacías adicionales: {extra_blank}\n'
                f'Color del troquel: {stroke_color}\n'
                f'Margen interior de seguridad: {cfg.safe_margin} mm\n\n'
                f'Los archivos *_impresion.{print_format} contienen el ARTE para imprimir.\n'
                'Los *_troquel.svg contienen SOLO las líneas de corte en el color configurado.\n'
                'NO imprimas los troqueles como parte de la etiqueta.\n'
                'Imprime al 100%: NO uses Ajustar a pagina.\n'
                'Ambos archivos usan las mismas dimensiones y posiciones en milímetros.\n'
                'Los textos SVG de impresión ya están convertidos a curvas: no requieren tipografías externas.\n'
                'El arte NO contiene la línea de corte (ni para etiquetas vacías).\n'
                'Haz una prueba física de registro antes de cortar en volumen.\n'
            )
            archive.writestr('LEEME.txt', instructions)
    names = {
        'zip': 'AURA_pliegos_impresion_y_troquel.zip',
        'png': 'AURA_pliegos_impresion_PNG.zip',
        'artwork-svg': 'AURA_pliegos_impresion_SVG.zip',
        'svg': 'AURA_pliegos_troquel_SVG.zip',
    }
    return buf.getvalue(), names[kind], 'application/zip'


def preview_info(payload:dict)->dict:
    settings=payload.get('settings',{})
    cfg=sheet_config(settings)
    products=normalize_products(payload.get('products',[]))
    extra_blank=blank_labels_count(settings)
    pages=page_count(products,cfg,extra_blank)
    page=int_num(payload.get('page'),0)
    page=max(0,min(page,max(0,pages-1)))
    product_copies=sum(p['copies'] for p in products)
    total=product_copies+extra_blank
    remaining=(cfg.capacity-total%cfg.capacity)%cfg.capacity if total else 0
    issues=validate_print(products,cfg)
    # La vista previa omite solo textos de productos inválidos, conserva los cortes.
    failed={(p['code'],p['name'],p.get('labelName',''),str(p['price'])) for p in products
            if any(i['code']==p['code'] and i['name']==p['name'] for i in issues)}
    def safe_preview():
        if not issues:return preview_svg(products,cfg,page,extra_blank)
        # Proyectar trazos de troquel y solo textos de registros validos.
        parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{cfg.width}mm" height="{cfg.height}mm" viewBox="0 0 {cfg.width} {cfg.height}">',
               f'<rect width="{cfg.width}" height="{cfg.height}" fill="white"/>']
        for slot,p in items_on_page(products,cfg,page,extra_blank):
            x,y=label_position(cfg,slot)
            parts.append(f'<path d="{label_path(x,y,cfg.label_width,cfg.label_height)}" fill="none" stroke="#d67d5e" stroke-width=".1"/>')
            if p is not None and (p['code'],p['name'],p.get('labelName',''),str(p['price'])) not in failed:
                for t in centered_layout(x,y,p,cfg): parts.append(glyph_outline(t))
        return '\n'.join(parts+['</svg>'])
    return {'svg':safe_preview(),'page':page,'pages':pages,'copies':total,
            'productCopies':product_copies,'blankCopies':extra_blank,
            'productCount':len(products),'capacity':cfg.capacity,'cols':cfg.cols,'rows':cfg.rows,
            'width':cfg.width,'height':cfg.height,'labelWidth':cfg.label_width,'labelHeight':cfg.label_height,
            'safeMargin':cfg.safe_margin,'remainingToFillLastPage':remaining,
            'nameWarnings':len(issues),'validationErrors':issues[:50], 'validForExport':not issues}
