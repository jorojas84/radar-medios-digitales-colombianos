"""Pruebas unitarias de descarga, parsing y limpieza de noticias.

Todos los XML viven dentro del archivo y las llamadas HTTP se reemplazan con
mocks. Así las pruebas son rápidas, reproducibles y no dependen de que un medio
real esté disponible en ese momento.
"""

# unittest forma parte de Python; mock permite sustituir únicamente la frontera HTTP.
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from medios import medios
from scraper import (
    ScraperError,
    descargar_xml,
    extraer_noticias,
    limpiar_noticias,
    parsear_fecha,
)


class ScraperTests(unittest.TestCase):
    """Comprueba contratos observables del scraper sin servicios externos."""

    def test_parsea_rss(self):
        """Se comprueba la extracción de un RSS estándar."""
        # Arrange: un documento mínimo con los tres campos que consume el recolector.
        xml = b"""
        <rss><channel><item>
            <title>Noticia RSS</title>
            <link>https://medio.com/noticia-rss</link>
            <pubDate>Mon, 10 Aug 2026 10:00:00 +0000</pubDate>
        </item></channel></rss>
        """

        # Act: el selector debe enviar el documento al parser RSS.
        noticias = extraer_noticias(xml, "rss")

        # Assert: la salida cumple el formato común usado por main.py.
        self.assertEqual(len(noticias), 1)
        self.assertEqual(noticias[0]["titulo"], "Noticia RSS")
        self.assertEqual(noticias[0]["url"], "https://medio.com/noticia-rss")
        self.assertIsNotNone(noticias[0]["fecha_publicacion"])

    def test_parsea_sitemap_news(self):
        """Se comprueba la extracción de un sitemap de Google News."""
        # Los xmlns son esenciales: sin ellos las búsquedas sm: y news: no coinciden.
        xml = b"""
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
                xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
            <url>
                <loc>https://medio.com/noticia-news</loc>
                <news:news>
                    <news:title>Noticia News</news:title>
                    <news:publication_date>2026-08-10T10:00:00Z</news:publication_date>
                </news:news>
            </url>
        </urlset>
        """

        noticias = extraer_noticias(xml, "sitemap-news")

        # El parser debe ocultar las diferencias XML detrás de las mismas claves.
        self.assertEqual(len(noticias), 1)
        self.assertEqual(noticias[0]["titulo"], "Noticia News")
        self.assertEqual(noticias[0]["url"], "https://medio.com/noticia-news")

    def test_parsea_sitemap_sin_titulo(self):
        """Se comprueba la creación de un título a partir del slug."""
        # Este formato aporta URL y lastmod, pero no un título editorial.
        xml = b"""
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url>
                <loc>https://medio.com/petro-anuncia-medida-583899</loc>
                <lastmod>2026-08-10T10:00:00Z</lastmod>
            </url>
        </urlset>
        """

        noticias = extraer_noticias(xml, "sitemap-sin-titulo")

        # El sufijo numérico se elimina antes de convertir guiones en espacios.
        self.assertEqual(len(noticias), 1)
        self.assertEqual(noticias[0]["titulo"], "Petro anuncia medida")

    def test_parsea_fechas_rss_e_iso(self):
        """Se comprueba que ambos formatos produzcan fechas con zona horaria."""
        # Las cadenas expresan el mismo instante usando dos estándares distintos.
        fecha_rss = parsear_fecha("Mon, 10 Aug 2026 10:00:00 +0000")
        fecha_iso = parsear_fecha("2026-08-10T10:00:00Z")

        # PostgreSQL recibe datetimes conscientes de zona y no horas ambiguas.
        self.assertIsNotNone(fecha_rss.tzinfo)
        self.assertIsNotNone(fecha_iso.tzinfo)
        self.assertEqual(fecha_rss, fecha_iso)

    def test_limpia_noticias_invalidas_y_duplicadas(self):
        """Se conservan solamente noticias completas con URLs únicas."""
        # La primera entrada es válida; las demás ejercitan cada regla de descarte.
        noticias = [
            {
                "titulo": "Noticia válida",
                "url": "https://medio.com/noticia",
                "fecha_publicacion": None,
            },
            {
                "titulo": "Noticia repetida",
                "url": "https://medio.com/noticia",
                "fecha_publicacion": None,
            },
            {"titulo": "", "url": "https://medio.com/sin-titulo"},
            {"titulo": "URL inválida", "url": "sin-esquema.com/noticia"},
        ]

        resultado = limpiar_noticias(noticias)

        # Se conserva la primera aparición de la URL, manteniendo el orden del feed.
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["titulo"], "Noticia válida")

    def test_reintenta_descarga_despues_de_error_temporal(self):
        """Se recupera una descarga cuando el segundo intento funciona."""
        # MagicMock simula el context manager retornado por urlopen.
        respuesta = MagicMock()
        respuesta.__enter__.return_value.read.return_value = b"<rss></rss>"

        # El primer elemento de side_effect falla y el segundo devuelve XML.
        with (
            patch(
                "scraper.urlopen",
                side_effect=[URLError("fallo temporal"), respuesta],
            ) as abrir,
            patch("scraper.time.sleep") as esperar,
        ):
            contenido = descargar_xml("https://medio.com/feed.xml")

        # Una espera y dos aperturas demuestran que ocurrió exactamente un reintento.
        self.assertEqual(contenido, b"<rss></rss>")
        self.assertEqual(abrir.call_count, 2)
        esperar.assert_called_once()

    def test_reintenta_respuesta_vacia(self):
        """Una respuesta vacía temporal no descarta inmediatamente el medio."""
        # Ambas respuestas son HTTP exitosas; solo cambia el contenido leído.
        vacia = MagicMock()
        vacia.__enter__.return_value.read.return_value = b""
        correcta = MagicMock()
        correcta.__enter__.return_value.read.return_value = b"<rss></rss>"

        with (
            patch("scraper.urlopen", side_effect=[vacia, correcta]) as abrir,
            patch("scraper.time.sleep") as esperar,
        ):
            contenido = descargar_xml("https://medio.com/feed.xml")

        # El segundo cuerpo debe entregarse al caller después de una espera.
        self.assertEqual(contenido, b"<rss></rss>")
        self.assertEqual(abrir.call_count, 2)
        esperar.assert_called_once()

    def test_no_reintenta_error_http_permanente(self):
        """Un HTTP 404 falla inmediatamente porque repetirlo no ayuda."""
        # HTTPError incluye URL y status, igual que la excepción real de urllib.
        error = HTTPError(
            "https://medio.com/feed.xml",
            404,
            "No encontrado",
            hdrs=None,
            fp=None,
        )

        # sleep está mockeado para comprobar que nunca se intentó esperar.
        with (
            patch("scraper.urlopen", side_effect=error) as abrir,
            patch("scraper.time.sleep") as esperar,
            self.assertRaises(ScraperError),
        ):
            descargar_xml("https://medio.com/feed.xml")

        abrir.assert_called_once()
        esperar.assert_not_called()

    def test_rechaza_respuesta_demasiado_grande(self):
        """Se protege la memoria ante una respuesta que supera el límite."""
        # El límite se reduce a 8 bytes para no crear 10 MiB dentro de la prueba.
        respuesta = MagicMock()
        respuesta.__enter__.return_value.read.return_value = b"x" * 9

        # Nueve bytes representan exactamente límite + 1, condición de rechazo.
        with (
            patch("scraper.MAX_XML_BYTES", 8),
            patch("scraper.urlopen", return_value=respuesta),
            self.assertRaisesRegex(ScraperError, "supera el límite"),
        ):
            descargar_xml("https://medio.com/feed.xml")

    def test_configuracion_de_medios(self):
        """Se comprueba que los medios tengan datos completos y nombres únicos."""
        # Los nombres también son identificadores humanos en los logs de PostgreSQL.
        nombres = [medio["nombre"] for medio in medios]

        # Este test ofrece una verificación rápida aunque main valida en producción.
        self.assertEqual(len(nombres), len(set(nombres)))
        for medio in medios:
            self.assertTrue(medio["url_fuente"].startswith("https://"))
            self.assertIn(
                medio["formato"],
                {"rss", "sitemap-news", "sitemap-sin-titulo"},
            )
            self.assertIsInstance(medio["activo"], bool)


if __name__ == "__main__":
    unittest.main()
