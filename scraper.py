"""Descarga XML y produce noticias sin depender de PostgreSQL.

Todos los parsers devuelven ``titulo``, ``url`` y ``fecha_publicacion``.
"""

import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


# Identifica el proceso ante los medios y evita solicitudes anónimas.
USER_AGENT = "NewsMonitor/1.0 (news monitoring project)"
REQUEST_TIMEOUT = 10
REQUEST_ATTEMPTS = 3
RETRY_BASE_DELAY = 1
# Evita cargar respuestas accidentales o ilimitadas en memoria.
MAX_XML_BYTES = 10 * 1024 * 1024
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
    if not isinstance(url, str) or not url.strip():
        raise ScraperError("La URL del medio está vacía o no es texto")

    request = Request(url, headers={"User-Agent": USER_AGENT})

    for intento in range(1, REQUEST_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as respuesta:
                contenido = respuesta.read(MAX_XML_BYTES + 1)
                if not contenido:
                    ultimo_error = f"El medio devolvió una respuesta vacía: {url}"
                elif len(contenido) > MAX_XML_BYTES:
                    raise ScraperError(
                        f"La respuesta supera el límite de {MAX_XML_BYTES} bytes: {url}"
                    )
                else:
                    return contenido
        except HTTPError as error:
            if error.code not in RETRYABLE_HTTP_STATUS:
                raise ScraperError(
                    f"El servidor respondió HTTP {error.code}: {url}"
                ) from error
            ultimo_error = f"El servidor respondió HTTP {error.code}: {url}"
        except TimeoutError:
            ultimo_error = (
                f"La conexión superó {REQUEST_TIMEOUT} segundos: {url}"
            )
        except URLError as error:
            ultimo_error = f"No fue posible conectar con {url}: {error.reason}"

        if intento == REQUEST_ATTEMPTS:
            raise ScraperError(
                f"{ultimo_error} después de {REQUEST_ATTEMPTS} intentos"
            )

        time.sleep(RETRY_BASE_DELAY * (2 ** (intento - 1)))


def parsear_fecha(fecha: str) -> datetime | None:
    """Convierte fechas RSS o ISO en datetime con zona horaria.

    Ejemplos:
        "Sun, 10 Aug 2026 10:00:00 +0000" → datetime con formato RFC 2822.
        "2026-08-10T10:00:00Z"             → datetime con formato ISO 8601.
    """
    if not fecha:
        return None

    try:
        resultado = parsedate_to_datetime(fecha)
        if resultado.tzinfo is None:
            resultado = resultado.replace(tzinfo=timezone.utc)
        return resultado
    except (TypeError, ValueError, OverflowError):
        pass

    try:
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
    root = ET.fromstring(xml)
    channel = root.find("channel")

    if channel is None:
        return []

    noticias = []

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
    root = ET.fromstring(xml)
    noticias = []

    for url_element in root.findall("sm:url", NAMESPACES):
        news_element = url_element.find("news:news", NAMESPACES)
        if news_element is None:
            continue

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
    root = ET.fromstring(xml)
    noticias = []

    for url_element in root.findall("sm:url", NAMESPACES):
        url = (url_element.findtext("sm:loc", namespaces=NAMESPACES) or "").strip()
        if not url:
            continue

        # Se omiten páginas de sección sin sufijo técnico; no son artículos. Esta
        # heurística prioriza precisión aunque pueda dejar fuera algún caso raro.
        slug = url.split("?", 1)[0].split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        if not SLUG_SUFFIX.search(slug):
            continue

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
    slug = url.split("?", 1)[0].split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    slug = SLUG_SUFFIX.sub("", slug)
    return slug.replace("-", " ").capitalize()


def limpiar_noticias(noticias: list[dict]) -> list[dict]:
    """Descarta noticias incompletas, URLs inválidas y URLs repetidas.

    Si una URL aparece más de una vez en el mismo feed, se conserva solamente
    la primera noticia encontrada.
    """
    noticias_limpias = []
    urls_vistas = set()

    for noticia in noticias:
        titulo = noticia.get("titulo", "").strip()
        url = noticia.get("url", "").strip()
        partes_url = urlparse(url)

        if (
            not titulo
            or partes_url.scheme not in {"http", "https"}
            or not partes_url.netloc
        ):
            continue

        if url in urls_vistas:
            continue

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
    if formato == "rss":
        return parsear_rss(xml)
    if formato == "sitemap-news":
        return parsear_sitemap_news(xml)
    if formato == "sitemap-sin-titulo":
        return parsear_sitemap_sin_titulo(xml)

    raise ValueError(f"Formato no soportado: {formato}")
