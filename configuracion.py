"""Validación de la configuración declarativa de las fuentes de noticias.

Este módulo comprueba la lista definida en ``medios.py`` antes de que el
recopilador abra conexiones o realice peticiones externas.
"""

from urllib.parse import urlparse


# Estos son los únicos nombres que extraer_noticias() sabe interpretar.
FORMATOS_SOPORTADOS = {"rss", "sitemap-news", "sitemap-sin-titulo"}


def validar_medios(configuracion: list[dict]) -> None:
    """Valida todas las fuentes antes de iniciar la recopilación.

    Raises:
        ValueError: Si falta una clave, hay nombres repetidos, una URL no es
            HTTPS, el formato no existe o no queda ningún medio activo.
    """
    if not isinstance(configuracion, list) or not configuracion:
        raise ValueError("La configuración de medios debe ser una lista no vacía")

    nombres = set()
    activos = 0

    for posicion, medio in enumerate(configuracion, start=1):
        if not isinstance(medio, dict):
            raise ValueError(f"El medio #{posicion} no es un diccionario")

        faltantes = {
            clave
            for clave in ("nombre", "url_fuente", "formato", "activo")
            if clave not in medio
        }
        if faltantes:
            raise ValueError(
                f"El medio #{posicion} no tiene: {', '.join(sorted(faltantes))}"
            )

        nombre = medio["nombre"]
        if not isinstance(nombre, str) or not nombre.strip():
            raise ValueError(f"El medio #{posicion} tiene un nombre inválido")
        if nombre in nombres:
            raise ValueError(f"El nombre del medio está repetido: {nombre}")
        nombres.add(nombre)

        if not isinstance(medio["activo"], bool):
            raise ValueError(f"El campo activo de {nombre} debe ser booleano")
        if medio["activo"]:
            activos += 1

        url = medio["url_fuente"]
        partes = urlparse(url) if isinstance(url, str) else None
        if partes is None or partes.scheme != "https" or not partes.netloc:
            raise ValueError(f"La URL de {nombre} debe ser HTTPS y completa")

        if medio["formato"] not in FORMATOS_SOPORTADOS:
            raise ValueError(
                f"Formato no soportado en {nombre}: {medio['formato']}"
            )

    if activos == 0:
        raise ValueError("No hay medios activos para recopilar")
