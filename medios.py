"""Configuración declarativa de las fuentes recopiladas por la aplicación.

Cada diccionario contiene exactamente cuatro datos:

- ``nombre``: identificación humana y valor guardado en PostgreSQL.
- ``url_fuente``: endpoint HTTPS que devuelve RSS o sitemap XML.
- ``formato``: nombre del parser que seleccionará ``extraer_noticias``.
- ``activo``: interruptor para omitir temporalmente una fuente sin borrarla.

main.py valida toda esta lista antes de conectarse. Mantener la configuración
sin lógica permite agregar o desactivar un medio sin tocar el scraper.
"""

# El orden también determina el orden secuencial de descarga y de los logs.
medios = [
    # RSS: el título, enlace y fecha vienen dentro de cada elemento <item>.
    {
        "nombre": "Caracol Radio",
        "url_fuente": "https://caracol.com.co/arc/outboundfeeds/rss/?outputType=xml",
        "formato": "rss",
        "activo": True,
    },
    {
        "nombre": "Semana",
        "url_fuente": "https://www.semana.com/arc/outboundfeeds/rss/?outputType=xml",
        "formato": "rss",
        "activo": True,
    },
    {
        "nombre": "El Tiempo",
        "url_fuente": "https://www.eltiempo.com/rss/eltiempo.xml",
        "formato": "rss",
        "activo": True,
    },
    # Sitemap de Google News: usa namespaces y sí incluye el título editorial.
    {
        "nombre": "El Espectador",
        "url_fuente": "https://www.elespectador.com/arc/outboundfeeds/news-sitemap/?outputType=xml",
        "formato": "sitemap-news",
        "activo": True,
    },
    {
        "nombre": "La FM",
        "url_fuente": "https://www.lafm.com.co/sitemapnews",
        "formato": "sitemap-news",
        "activo": True,
    },
    {
        "nombre": "El Heraldo",
        "url_fuente": "https://www.elheraldo.co/arc/outboundfeeds/sitemap-news/latest/",
        "formato": "sitemap-news",
        "activo": True,
    },
    {
        "nombre": "Portafolio",
        "url_fuente": "https://www.portafolio.co/sitemap-google-news.xml",
        "formato": "sitemap-news",
        "activo": True,
    },
    {
        "nombre": "La República",
        "url_fuente": "https://www.larepublica.co/sitemapnews",
        "formato": "sitemap-news",
        "activo": True,
    },
    {
        "nombre": "Noticias RCN",
        "url_fuente": "https://www.noticiasrcn.com/sitemapnews",
        "formato": "sitemap-news",
        "activo": True,
    },
    {
        "nombre": "El País",
        "url_fuente": "https://www.elpais.com.co/arc/outboundfeeds/rss/?outputType=xml",
        "formato": "rss",
        "activo": True,
    },
    {
        "nombre": "Infobae Colombia",
        "url_fuente": "https://www.infobae.com/arc/outboundfeeds/news-sitemap/category/colombia/",
        "formato": "sitemap-news",
        "activo": True,
    },
    {
        "nombre": "Blu Radio",
        "url_fuente": "https://www.bluradio.com/sitemap-latest.xml",
        "formato": "sitemap-sin-titulo",
        "activo": True,
    },
    {
        "nombre": "Noticias Caracol",
        "url_fuente": "https://www.noticiascaracol.com/sitemap-latest.xml",
        "formato": "sitemap-sin-titulo",
        "activo": True,
    },
]
