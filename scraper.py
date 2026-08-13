"""Descarga fuentes XML y las convierte en noticias normalizadas.

Este módulo concentra la parte externa del sistema: HTTP y estructuras XML que
El recolector no controla esas fuentes. Por eso valida defensivamente tamaños,
URLs, fechas y campos.
No conoce PostgreSQL; devuelve listas de diccionarios para que la persistencia
pueda probarse y evolucionar de forma independiente.

Todos los parsers producen la misma forma:

    {"titulo": str, "url": str, "fecha_publicacion": datetime | None}
"""

# Biblioteca estándar para expresiones regulares, esperas, fechas, HTTP y XML.
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


# Se identifica la petición ante el servidor. Un User-Agent explícito evita que
# la solicitud parezca un navegador anónimo y facilita que un medio contacte al
# responsable si en el futuro se agrega una URL o correo del proyecto.
USER_AGENT = "NewsMonitor/1.0 (news monitoring project)"
# Es el límite por operación de red; los intentos totales pueden tardar más.
REQUEST_TIMEOUT = 10
# Tres intentos equilibran recuperación temporal y duración total del proceso.
REQUEST_ATTEMPTS = 3
# La espera se duplica entre intentos: 1 segundo y luego 2 segundos.
RETRY_BASE_DELAY = 1
# Un sitemap normal de noticias cabe holgadamente; el límite protege la RAM si
# una URL empieza a devolver HTML, un archivo o contenido sin fin por error.
MAX_XML_BYTES = 10 * 1024 * 1024
# Solo se repiten estados que suelen ser transitorios o de saturación.
RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}

# Los sitemaps usan namespaces XML; estos alias permiten buscar sus etiquetas.
NAMESPACES = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}

# Algunos medios agregan códigos técnicos al final del slug de sus URLs. La
# expresión acepta identificadores numéricos largos o patrones como ``-so35``.
SLUG_SUFFIX = re.compile(r"-[a-z]{2}\d+$|-\d{3,}$", re.IGNORECASE)


class ScraperError(RuntimeError):
    """Representa un fallo esperado y atribuible a una fuente externa.

    main.py captura esta excepción por medio, registra el problema y continúa
    con las fuentes restantes. Los errores de programación inesperados no se
    convierten silenciosamente en ScraperError.
    """


def descargar_xml(url: str) -> bytes:
    """Descarga una URL con límite de tamaño y reintentos selectivos.

    Args:
        url: Dirección HTTPS configurada para el RSS o sitemap.

    Returns:
        El cuerpo completo como bytes. Todavía no se interpreta como XML.

    Raises:
        ScraperError: Si la URL es inválida, la respuesta es vacía o demasiado
            grande, ocurre un error permanente, o se agotan los intentos.
    """
    # Se valida antes de abrir la conexión para producir un error claro.
    if not isinstance(url, str) or not url.strip():
        raise ScraperError("La URL del medio está vacía o no es texto")

    # Se construye la petición con el User-Agent.
    request = Request(url, headers={"User-Agent": USER_AGENT})

    # El mismo Request puede reutilizarse porque una descarga GET no lleva cuerpo.
    for intento in range(1, REQUEST_ATTEMPTS + 1):
        try:
            # `with` cierra la respuesta automáticamente al terminar.
            with urlopen(request, timeout=REQUEST_TIMEOUT) as respuesta:
                # Se lee un byte adicional para detectar respuestas demasiado grandes.
                contenido = respuesta.read(MAX_XML_BYTES + 1)
                # Un HTTP 200 vacío puede ser temporal, así que se vuelve a intentar.
                if not contenido:
                    ultimo_error = f"El medio devolvió una respuesta vacía: {url}"
                # Un contenido excesivo se rechaza de inmediato: repetirlo gastaría RAM.
                elif len(contenido) > MAX_XML_BYTES:
                    raise ScraperError(
                        f"La respuesta supera el límite de {MAX_XML_BYTES} bytes: {url}"
                    )
                else:
                    return contenido
        except HTTPError as error:
            # Los errores permanentes, como 404, no mejoran al repetir la petición.
            if error.code not in RETRYABLE_HTTP_STATUS:
                raise ScraperError(
                    f"El servidor respondió HTTP {error.code}: {url}"
                ) from error
            ultimo_error = f"El servidor respondió HTTP {error.code}: {url}"
        except TimeoutError:
            # No se conserva la excepción porque el mensaje controlado es más claro.
            ultimo_error = (
                f"La conexión superó {REQUEST_TIMEOUT} segundos: {url}"
            )
        except URLError as error:
            ultimo_error = f"No fue posible conectar con {url}: {error.reason}"

        # El último intento transforma la causa más reciente en un error del dominio.
        if intento == REQUEST_ATTEMPTS:
            raise ScraperError(
                f"{ultimo_error} después de {REQUEST_ATTEMPTS} intentos"
            )

        # La espera progresiva reduce la presión sobre un servidor temporalmente caído.
        time.sleep(RETRY_BASE_DELAY * (2 ** (intento - 1)))


def parsear_fecha(fecha: str) -> datetime | None:
    """Convierte fechas RSS o ISO en datetime con zona horaria.

    Ejemplos:
        "Sun, 10 Aug 2026 10:00:00 +0000" → datetime con formato RFC 2822.
        "2026-08-10T10:00:00Z"             → datetime con formato ISO 8601.
    """
    # None conserva la noticia sin inventar una fecha que altere los análisis.
    if not fecha:
        return None

    # RSS suele usar RFC 2822, por ejemplo: "Sun, 10 Aug 2026 10:00:00 +0000".
    try:
        resultado = parsedate_to_datetime(fecha)
        # Una fecha sin zona se interpreta como UTC para evitar datetimes ambiguos.
        if resultado.tzinfo is None:
            resultado = resultado.replace(tzinfo=timezone.utc)
        return resultado
    except (TypeError, ValueError, OverflowError):
        pass

    # Los sitemaps suelen usar ISO 8601, por ejemplo: "2026-08-10T10:00:00Z".
    try:
        # fromisoformat comprende +00:00; el reemplazo normaliza el sufijo UTC Z.
        resultado = datetime.fromisoformat(fecha.replace("Z", "+00:00"))
        if resultado.tzinfo is None:
            resultado = resultado.replace(tzinfo=timezone.utc)
        return resultado
    except (TypeError, ValueError, OverflowError):
        return None


def parsear_rss(xml: bytes) -> list[dict]:
    """Extrae noticias de un RSS estándar.

    XML esperado:
        <channel>
            <item>
                <title>Noticia de ejemplo</title>
                <link>https://medio.com/noticia</link>
                <pubDate>Sun, 10 Aug 2026 10:00:00 +0000</pubDate>
            </item>
        </channel>

    Salida:
        {"titulo": "Noticia de ejemplo", "url": "https://medio.com/noticia",
         "fecha_publicacion": datetime(...)}
    """
    # ET.fromstring procesa el documento completo y lanza ParseError si está roto.
    root = ET.fromstring(xml)
    # En RSS, las noticias están dentro de <channel>.
    channel = root.find("channel")

    # Si la respuesta no tiene estructura RSS, no hay noticias que extraer.
    if channel is None:
        return []

    noticias = []

    # Cada item se conserva inicialmente aunque falte un campo; limpiar_noticias
    # aplica la política común después de terminar la extracción estructural.
    for item in channel.findall("item"):
        noticia = {
            "titulo": (item.findtext("title") or "").strip(),
            "url": (item.findtext("link") or "").strip(),
            "fecha_publicacion": parsear_fecha(
                (item.findtext("pubDate") or "").strip()
            ),
        }
        noticias.append(noticia)

    return noticias


def parsear_sitemap_news(xml: bytes) -> list[dict]:
    """Extrae noticias de un sitemap compatible con Google News.

    XML esperado:
        <url>
            <loc>https://medio.com/noticia</loc>
            <news:news>
                <news:title>Noticia de ejemplo</news:title>
                <news:publication_date>2026-08-10T10:00:00Z</news:publication_date>
            </news:news>
        </url>

    Salida: el mismo diccionario normalizado que el parser RSS.
    """
    # El sitemap también es XML, pero organiza los datos con etiquetas distintas.
    root = ET.fromstring(xml)
    noticias = []

    # `sm:url` usa el namespace estándar de sitemaps.
    for url_element in root.findall("sm:url", NAMESPACES):
        # La metadata editorial está dentro de <news:news>.
        news_element = url_element.find("news:news", NAMESPACES)
        if news_element is None:
            # Sin metadata de Google News no es posible obtener el titular real.
            continue

        # Se normaliza la salida para que tenga las mismas claves que un RSS.
        noticias.append(
            {
                "titulo": (
                    news_element.findtext("news:title", namespaces=NAMESPACES) or ""
                ).strip(),
                "url": (
                    url_element.findtext("sm:loc", namespaces=NAMESPACES) or ""
                ).strip(),
                "fecha_publicacion": parsear_fecha(
                    (
                        news_element.findtext(
                            "news:publication_date", namespaces=NAMESPACES
                        )
                        or ""
                    ).strip()
                ),
            }
        )

    return noticias


def parsear_sitemap_sin_titulo(xml: bytes) -> list[dict]:
    """Extrae URLs de un sitemap y genera un título provisional desde su slug.

    XML esperado:
        <url>
            <loc>https://medio.com/petro-anuncia-medida-583899</loc>
            <lastmod>2026-08-10T10:00:00Z</lastmod>
        </url>

    La URL no trae un titular real, así que la salida usa un título aproximado:
        "Petro anuncia medida"
    """
    # Este formato es un último recurso: lastmod no siempre equivale a publicación.
    root = ET.fromstring(xml)
    noticias = []

    # Este formato trae URL y fecha, pero no trae un título editorial.
    for url_element in root.findall("sm:url", NAMESPACES):
        url = (url_element.findtext("sm:loc", namespaces=NAMESPACES) or "").strip()
        if not url:
            # Una entrada sin URL no puede guardarse ni identificarse.
            continue

        # Se omiten páginas de sección sin sufijo técnico; no son artículos. Esta
        # heurística prioriza precisión aunque pueda dejar fuera algún caso raro.
        slug = url.split("?", 1)[0].split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        if not SLUG_SUFFIX.search(slug):
            continue

        # El título será una aproximación construida desde el final de la URL.
        noticias.append(
            {
                "titulo": titulo_desde_slug(url),
                "url": url,
                "fecha_publicacion": parsear_fecha(
                    (
                        url_element.findtext("sm:lastmod", namespaces=NAMESPACES)
                        or ""
                    ).strip()
                ),
            }
        )

    return noticias


def titulo_desde_slug(url: str) -> str:
    """Convierte el final de una URL en un título de respaldo.

    Ejemplo:
        "https://medio.com/petro-anuncia-medida-583899"
        → "Petro anuncia medida"
    """
    # Se eliminan parámetros, fragmentos y todo lo anterior al último `/`.
    slug = url.split("?", 1)[0].split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    # Se quitan identificadores técnicos como `-583899` o `-so35`.
    slug = SLUG_SUFFIX.sub("", slug)
    # Se reemplazan guiones por espacios. capitalize crea un respaldo legible,
    # pero no intenta reconstruir siglas o mayúsculas editoriales inexistentes.
    return slug.replace("-", " ").capitalize()


def limpiar_noticias(noticias: list[dict]) -> list[dict]:
    """Descarta noticias incompletas, URLs inválidas y URLs repetidas.

    Si una URL aparece más de una vez en el mismo feed, se conserva solamente
    la primera noticia encontrada.
    """
    # La lista conserva el orden original del feed para resultados predecibles.
    noticias_limpias = []
    # El set se usa solo durante este feed; PostgreSQL deduplica entre ejecuciones.
    urls_vistas = set()

    for noticia in noticias:
        # get evita KeyError cuando una entrada XML está incompleta.
        titulo = noticia.get("titulo", "").strip()
        url = noticia.get("url", "").strip()
        partes_url = urlparse(url)

        # Se exige un título y una URL web completa antes de guardar la noticia.
        if (
            not titulo
            or partes_url.scheme not in {"http", "https"}
            or not partes_url.netloc
        ):
            continue

        # Se evita enviar dos veces la misma URL en una sola operación SQL.
        if url in urls_vistas:
            continue

        # La URL se registra antes de agregar la noticia para bloquear repeticiones.
        urls_vistas.add(url)
        noticias_limpias.append(
            {
                "titulo": titulo,
                "url": url,
                "fecha_publicacion": noticia.get("fecha_publicacion"),
            }
        )

    return noticias_limpias


def extraer_noticias(xml: bytes, formato: str) -> list[dict]:
    """Usa el parser adecuado y devuelve columnas compatibles con `noticias`.

    Selección:
        "rss"                → parsear_rss()
        "sitemap-news"       → parsear_sitemap_news()
        "sitemap-sin-titulo" → parsear_sitemap_sin_titulo()
    """
    # Esta función es el único decisor de formatos. Mantenerlo centralizado evita
    # condicionales repetidos en main.py y hace explícito qué formatos existen.
    if formato == "rss":
        return parsear_rss(xml)
    if formato == "sitemap-news":
        return parsear_sitemap_news(xml)
    if formato == "sitemap-sin-titulo":
        return parsear_sitemap_sin_titulo(xml)

    # Fallar explícitamente evita interpretar un XML con el parser equivocado.
    raise ValueError(f"Formato no soportado: {formato}")
