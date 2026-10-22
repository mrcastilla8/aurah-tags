# AURA Etiquetas · versión 1.5 privada para Render

Generador de pliegos para joyería con importación de Excel, edición de productos y precios, tamaños A3/A4/Carta/personalizados y elección del arte de impresión en PNG o SVG vectorial, más un SVG de corte separado, todo a tamaño real. El diseño de la etiqueta mantiene las alas redondeadas y el estrechamiento central de la referencia. Tamaño inicial **68 × 12 mm**.

## Publicar en Render: guía rápida

**1. Crear una contraseña y su hash.** Necesitas Python 3.10+ en tu PC. Desde la carpeta del proyecto ejecuta:

```sh
python crear_credenciales.py
```

En Windows también puedes usar `py -3 crear_credenciales.py`. Escribe **una contraseña única de 14 caracteres o más**. El script devuelve `AURA_PASSWORD_HASH` y `AURA_SESSION_SECRET`; no revela la contraseña ni guarda datos en disco.

**2. Crear un repositorio privado en GitHub.** Sube el *contenido* de esta carpeta (los archivos `Dockerfile`, `render.yaml`, `app.py`, etc. en la raíz del repositorio). No subas contraseña, secretos ni hojas Excel reales. Es preferible que el repositorio sea **Private**.

**3. Crear un Blueprint en Render.** En [Render Dashboard](https://dashboard.render.com/) ve a **New → Blueprint**, conecta GitHub, selecciona tu repositorio y autoriza la creación. Render leerá `render.yaml` para crear un servicio web Docker con plan Free (si sigue disponible en tu cuenta).

**4. Configurar contraseña.** Render te solicitará la variable `AURA_PASSWORD_HASH`, porque en `render.yaml` aparece `sync: false`. Pega **únicamente el valor** del hash obtenido en el paso 1 (empieza por `pbkdf2_sha256$`). No incluyas `AURA_PASSWORD_HASH =` como parte del valor. `AURA_SESSION_SECRET` se genera automáticamente en Render. La clave secreta mostrada por el script es una alternativa si configuras Render manualmente, y no hace falta pegarla en un Blueprint.

**5. Deploy.** Render construirá la imagen con `Dockerfile` y arrancará la aplicación. Abre la URL `https://aura-etiquetas-privado-....onrender.com`: debe aparecer la **pantalla de acceso privado**. Usa la contraseña que elegiste, no el hash. Puedes renombrar el servicio en el archivo `render.yaml` antes del primer deploy.

> Si creas un **Web Service** manualmente en lugar de un Blueprint: elige **Docker**; crea las variables `AURA_MODE=render`, `AURA_PASSWORD_HASH=<hash>` y `AURA_SESSION_SECRET=<valor aleatorio de 40+ caracteres>`; usa `/health` como ruta de health check. El servicio escucha automáticamente en `0.0.0.0:$PORT` (o `10000`).

### Configuración en Render

| Variable | Valor | Descripción |
| --- | --- | --- |
| `AURA_MODE` | `render` | Habilita la autenticación y escucha el puerto de Render. Obligatoria. |
| `AURA_PASSWORD_HASH` | `pbkdf2_sha256$...` | Hash generado en tu computador. Es secreto: no subirlo al repositorio. |
| `AURA_SESSION_SECRET` | Generada por Render | Secreto para autenticar los identificadores de sesiones con HMAC-SHA256; las sesiones son tokens aleatorios en RAM. |
| `PORT` | Automático (normalmente `10000`) | Puerto de servicio. |

### Seguridad y acceso exclusivo

- **Solo una cuenta**, sin registro de usuarios ni mecanismo de recuperación de contraseña por correo.
- El servidor solo muestra la interfaz y archivos de exportación después del inicio de sesión.
- La contraseña se compara con un hash **PBKDF2-SHA256 con sal**, nunca se guarda en el código.
- Cookies de sesión aleatorias con `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/`; duran hasta 12 horas, se invalidan al cerrar sesión o reiniciar Render.
- Endpoints protegidos con token CSRF, cabeceras de seguridad y límite de 8 intentos de inicio de sesión por 15 minutos y dirección IP conectada.
- La ruta **`/health` es pública** solo para la comprobación de servicio y responde sin inventario ni secretos.
- Solo el propietario de la contraseña puede iniciar sesión; la URL de Render sigue siendo pública, pero el contenido está protegido a nivel de aplicación. **No equivale a restringir el acceso a una IP o red privada**.
- Los datos importados se procesan temporalmente en la memoria del servicio; no hay base de datos ni archivo de inventario persistente. Al publicar en la nube, **tu Excel sí se envía al servidor Render para procesarlo**, a diferencia del modo local.
- Se aceptan Excel de hasta **15 MB**. En Render las descargas se limitan a **12 pliegos por operación** para evitar consumo excesivo de recursos en instancias pequeñas. Divide trabajos mayores en lotes. El modo local no tiene este límite adicional.
- Render Free puede suspender la app por inactividad y tener recursos limitados: al reactivarse deberás volver a iniciar sesión. Una hoja A3 a 600 DPI requiere memoria significativa.

**Si pierdes tu contraseña:** ejecuta de nuevo `crear_credenciales.py`, actualiza el hash en Render → servicio → Environment y guarda los cambios. Reinicia el servicio para limpiar sesiones anteriores si es necesario.

**Precauciones recomendadas:** contraseña única y larga; activar 2FA en GitHub y Render; no compartir el enlace y nunca pegar claves en el código, commits ni capturas de pantalla. Para seguridad adicional, configura un firewall o proxy de acceso por identidad (p. ej., Cloudflare Access); no se incluye en esta versión.

## Inicio local (opcional)

En tu computadora sigue disponible el funcionamiento anterior: ejecuta `iniciar_windows.bat` o `iniciar_mac_linux.sh`. Por defecto `AURA_MODE=local` escucha **únicamente en 127.0.0.1**, sin contraseña y sin publicar el puerto. Si detecta que el proceso se ejecuta dentro de Render con modo local, se niega a iniciar.

No uses `AURA_MODE=local` en un servidor expuesto a internet.

## Uso y salida de archivos

1. Importa `.xlsx`, `.xlsm`, `.xls` o `.csv` o utiliza «Cargar ejemplo».
2. Asocia las columnas código, nombre, precio y copias.
3. Edita productos de forma individual o aplica multiplicadores y cantidades masivos. El nombre se distribuye automáticamente en una o dos líneas, siempre íntegro. Para nombres que no caben, el sistema bloquea la exportación hasta que definas un **Nombre en etiqueta** corto; no se modifica el original del Excel.
4. Define hoja, orientación, dimensiones, separación, márgenes y DPI (150/300/600). Ajusta el **margen interior de seguridad** si la imprenta te proporciona una tolerancia oficial (predeterminado: 2 mm).
5. En **Formato de la imagen de impresión** escoge `PNG` (raster, 150/300/600 DPI) o `SVG` (diseño vectorial escalable con textos, precios y códigos).
6. Usa **Descargar [PNG/SVG] de impresión + SVG de corte** para recibir el ZIP con ambos archivos de cada pliego, incluso si solo hay una hoja. O pulsa **Solo PNG/SVG de impresión** o **Solo SVG de corte**. Si una descarga individual ocupa varias páginas, se entrega como ZIP.
7. Los archivos `*_impresion.svg` contienen el **arte con textos e imágenes vectoriales**; los archivos `*_troquel.svg` tienen únicamente **trayectorias cerradas de corte**, sin textos ni relleno. Ambos usan exactamente los mismos milímetros, papel y posiciones. **No confundas el SVG de impresión con el SVG de troquelado.**
8. Si exportas SVG de impresión, **nombres, códigos y precios están convertidos a curvas vectoriales**, sin requerir fuentes externas. Estos textos no serán editables en la imprenta. El arte nunca incluye el contorno del corte; la vista previa sí muestra guías de corte y margen interior.
9. Imprime al **100 %** y verifica con un pliego de prueba el registro con la troqueladora: la aplicación no añade marcas de registro específicas de cada máquina.

## Ejecutar pruebas

```sh
python -m unittest discover -s tests -p 'test_*.py'
```

Pruebas del servidor privado: `python -m unittest tests.test_security` (o usar discover). La integración opcional con Chromium/Playwright puede requerir instalaciones adicionales no incluidas en el Docker de producción.

## Archivos

- `app.py`: HTTP, control de acceso, importación, vista previa y descargas.
- `engine.py`: Excel, geometría, dibujo, PNG/SVG.
- `static/`: vista web, inicio de sesión.
- `crear_credenciales.py`: generador local de hash y secreto.
- `render.yaml` y `Dockerfile`: publicación en Render.
- `tests/`: pruebas automatizadas.
- `ejemplos/productos_ejemplo.csv`: inventario ficticio.

## Funcionalidades conservadas (v1.2–v1.4)

- Selector para elegir **PNG o SVG del arte de impresión** sin afectar el formato del troquel.
- Ruta privada `/api/export/artwork-svg` para descargar exclusivamente arte SVG; `/api/export/svg` sigue reservada al troquel.
- ZIP combinado con `_impresion.png` **o** `_impresion.svg` y `_troquel.svg` en cada página.
- Nombres largos con hasta dos líneas; ajuste seguro automático y bloqueo preventivo si algún contenido no cabe sin cortes.
- Pruebas automatizadas para SVG vectorial, medidas, alineación de contornos, nombres, exportación combinada y protección HTTP.

Para actualizar en Render desde una versión anterior, reemplaza los archivos del repositorio privado por los de esta entrega y realiza un commit/push. Render iniciará el despliegue del nuevo código automáticamente si activaste el auto-deploy; **mantén las variables `AURA_MODE`, `AURA_PASSWORD_HASH` y `AURA_SESSION_SECRET` existentes**. No debes crear otro servicio ni compartir tu contraseña.

- Selector para elegir el color del borde de troquelado en el SVG de corte.

- Campo para agregar **etiquetas vacías adicionales**, útil para completar un pliego con stickers en blanco.


## v1.5 · Corrección para imprenta — lectura obligatoria

- Zona de seguridad **2 mm predeterminados** desde el contorno, configurable de **1,5 a 4 mm**. Se aplican las esquinas redondeadas y la reducción central. El software reserva dos rectángulos protegidos, uno para precio y otro para nombre y código.
- Textos: ajuste por palabras hasta dos líneas y reducción progresiva con mínimo 5 pt. **Nunca se eliminan caracteres ni se usan elipsis**: si un nombre, precio o código no cabe, la exportación se bloquea, se indica la fila y se solicita abreviación en `Nombre en etiqueta`. Para un precio o código extenso, usa una etiqueta más ancha o acorta el código.
- **TROQUEL LIBRE:** la impresión (PNG/SVG) contiene exclusivamente textos. Ya no incluye bordes de etiqueta ni contornos de corte (tampoco en stickers vacíos). El contorno aparece como guía naranja y las áreas seguras en verde **solo en vista previa**.
- El texto del SVG de impresión está **convertido a curvas**, no depende de tipografías externas ni puede crecer por sustitución de fuentes. No es texto editable.
- El SVG de corte contiene rutas cerradas sin relleno, grosor 0,1 mm, y color seleccionado. Se conservan los mismos tamaños físicos y posiciones del pliego en cada formato.
- La app no puede compensar desviaciones físicas de impresión/corte: pide a la imprenta la tolerancia y verifica el registro con un pliego de prueba al 100 %, sin ajustar a página.
