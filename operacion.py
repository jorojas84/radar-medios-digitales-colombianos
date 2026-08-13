"""Servicios operativos que permiten ejecutar la aplicación de forma autónoma.

Aquí viven los logs, el bloqueo contra ejecuciones simultáneas, el registro de
errores y la coordinación de la recopilación. ``main.py`` solo prepara estas
piezas y actúa como punto de entrada.
"""

import fcntl
import logging
import os
import sys
import tempfile
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from xml.etree.ElementTree import ParseError

import psycopg2

from base_datos import crear_conexion, guardar_log, guardar_noticias
from scraper import ScraperError, descargar_xml, extraer_noticias, limpiar_noticias


logger = logging.getLogger("app")


def configurar_logging() -> None:
    """Configura consola y un archivo rotativo para cada ejecución."""
    if logger.handlers:
        return

    logger.setLevel(logging.INFO)
    logger.propagate = False
    formato = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formato)
    logger.addHandler(consola)

    ruta_predeterminada = Path(__file__).with_name("logs") / "app.log"
    ruta_log = Path(os.getenv("APP_LOG_FILE", str(ruta_predeterminada)))

    try:
        ruta_log.parent.mkdir(parents=True, exist_ok=True)
        archivo = RotatingFileHandler(
            ruta_log,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError as error:
        logger.warning("No se pudo abrir el archivo de log %s: %s", ruta_log, error)
        return

    archivo.setFormatter(formato)
    logger.addHandler(archivo)


@contextmanager
def bloqueo_ejecucion():
    """Impide que dos procesos de la aplicación se ejecuten simultáneamente."""
    ruta_predeterminada = Path(tempfile.gettempdir()) / "app.lock"
    ruta = Path(os.getenv("APP_LOCK_FILE", str(ruta_predeterminada)))
    archivo = ruta.open("a+", encoding="utf-8")

    try:
        fcntl.flock(archivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        archivo.seek(0)
        archivo.truncate()
        archivo.write(str(os.getpid()))
        archivo.flush()
        yield
    finally:
        try:
            fcntl.flock(archivo.fileno(), fcntl.LOCK_UN)
        finally:
            archivo.close()


def registrar_error(conexion, medio: str, error: Exception) -> bool:
    """Revierte el lote y devuelve si logró guardar su log de error."""
    try:
        conexion.rollback()
    except psycopg2.Error as error_rollback:
        logger.error(
            "No se pudo revertir la transacción de %s: %s",
            medio,
            error_rollback,
        )
        return False

    try:
        guardar_log(
            conexion,
            medio=medio,
            noticias_nuevas=0,
            noticias_vistas=0,
            status="failed",
            error_msg=str(error)[:2000],
        )
        conexion.commit()
        return True
    except psycopg2.Error as error_log:
        logger.error("No se pudo guardar el log de %s: %s", medio, error_log)
        try:
            conexion.rollback()
        except psycopg2.Error as error_rollback:
            logger.error(
                "La conexión tampoco permitió el rollback final: %s",
                error_rollback,
            )
        return False


def procesar_medios(medios: list[dict]) -> int:
    """Procesa las fuentes y devuelve un código útil para el scheduler."""
    errores = 0

    try:
        conexion = crear_conexion()
    except (psycopg2.Error, ValueError) as error:
        logger.error("Error al conectar con PostgreSQL: %s", error)
        return 1

    try:
        for medio in medios:
            if not medio["activo"]:
                continue

            nombre = medio["nombre"]
            logger.info("Descargando %s...", nombre)

            try:
                xml = descargar_xml(medio["url_fuente"])
                noticias = extraer_noticias(xml, medio["formato"])
                extraidas = len(noticias)

                if not noticias:
                    raise ScraperError("El parser no encontró noticias en el feed")

                noticias = limpiar_noticias(noticias)
                if not noticias:
                    raise ScraperError(
                        f"Las {extraidas} entradas extraídas resultaron inválidas"
                    )

                for noticia in noticias:
                    noticia["medio"] = nombre

            except (ScraperError, ParseError, ValueError) as error:
                logger.error("Error de recopilación en %s: %s", nombre, error)
                errores += 1
                if not registrar_error(conexion, nombre, error):
                    logger.critical(
                        "Se aborta la ejecución porque PostgreSQL no permite "
                        "registrar el fallo"
                    )
                    return 1
                continue

            try:
                nuevas, ya_existentes = guardar_noticias(conexion, noticias)
                guardar_log(
                    conexion,
                    medio=nombre,
                    noticias_nuevas=nuevas,
                    noticias_vistas=ya_existentes,
                )
                conexion.commit()
            except psycopg2.Error as error:
                logger.error("Error de base de datos en %s: %s", nombre, error)
                errores += 1
                if not registrar_error(conexion, nombre, error):
                    logger.critical(
                        "Se aborta la ejecución porque la conexión quedó inutilizable"
                    )
                    return 1
                continue

            logger.info(
                "%s OK: %s extraídas · %s procesadas · %s nuevas · "
                "%s ya existían",
                nombre,
                extraidas,
                len(noticias),
                nuevas,
                ya_existentes,
            )

        if errores:
            logger.error("Finalizado con errores en %s medio(s)", errores)
            return 1

        logger.info("Todos los medios se recopilaron correctamente")
        return 0
    finally:
        conexion.close()
