"""Ejecuta el análisis de entidades sobre noticias ya guardadas."""

import logging

import psycopg2

from base_datos import (
    crear_conexion,
    guardar_entidades,
    obtener_noticias_pendientes,
)
from entidades import (
    CATALOGO_ALIASES,
    CATALOGO_BUSQUEDA,
    CATALOGO_CORRECCIONES,
    cargar_modelo,
    detectar_entidades,
)
from operacion import configurar_logging


TAMANO_LOTE_BD = 250
logger = logging.getLogger("app")


def procesar_pendientes(limite: int = TAMANO_LOTE_BD) -> int:
    """Procesa todos los titulares pendientes y devuelve cuántos analizó."""
    conexion = crear_conexion()
    procesadas = 0
    resumen_ner: dict[str, int] = {}

    try:
        # El modelo se carga una sola vez; cargarlo dentro del ciclo sería costoso.
        modelo = cargar_modelo()
        logger.info(
            "Catálogos cargados: correcciones=%s · alias=%s · "
            "entidades_catalogadas=%s",
            len(CATALOGO_CORRECCIONES),
            len(CATALOGO_ALIASES),
            len(CATALOGO_BUSQUEDA),
        )
        while True:
            # El lote queda bloqueado en PostgreSQL hasta el commit o rollback.
            noticias = obtener_noticias_pendientes(conexion, limite)
            if not noticias:
                break

            # Se separan los textos porque el detector trabaja con titulares,
            # mientras que la persistencia necesita conservar también cada id.
            titulares = [noticia["titulo"] for noticia in noticias]
            entidades_por_titular = detectar_entidades(
                modelo,
                titulares,
                resumen=resumen_ner,
            )

            if len(entidades_por_titular) != len(noticias):
                raise RuntimeError(
                    "El detector devolvió una cantidad distinta de titulares"
                )

            # zip vuelve a unir cada resultado con la noticia que le corresponde.
            resultados = [
                (noticia["id"], entidades)
                for noticia, entidades in zip(
                    noticias, entidades_por_titular, strict=True
                )
            ]
            guardar_entidades(conexion, resultados)
            # El commit libera los bloqueos y confirma entidades más la bandera.
            conexion.commit()
            procesadas += len(noticias)
            # Durante la ejecución solo se informa el avance. Las estadísticas
            # de BETO, reglas y catálogo se muestran una vez en el resumen final.
            logger.info(
                "Lote completado: %s noticia(s) · acumulado=%s",
                len(noticias),
                procesadas,
            )

        logger.info(
            "Resumen NER: noticias=%s · BETO=%s · alias=%s · "
            "tipos_corregidos=%s · catálogo=%s · "
            "puntuación_descartada=%s · cargos_descartados=%s · "
            "ambigüedades_descartadas=%s · "
            "genericos_descartados=%s · "
            "entidades_guardadas=%s",
            procesadas,
            resumen_ner.get("beto", 0),
            resumen_ner.get("aliases", 0),
            resumen_ner.get("tipos_corregidos", 0),
            resumen_ner.get("catalogo", 0),
            resumen_ner.get("puntuacion_descartada", 0),
            resumen_ner.get("cargos_descartados", 0),
            resumen_ner.get("ambiguedades_descartadas", 0),
            resumen_ner.get("genericos_descartados", 0),
            resumen_ner.get("entidades_guardadas", 0),
        )
        return procesadas
    finally:
        try:
            conexion.rollback()
        except psycopg2.Error as error:
            logger.warning("No se pudo revertir la transacción final: %s", error)
        conexion.close()


def main() -> int:
    """Ejecuta el worker y devuelve un código apropiado para cron o systemd."""
    configurar_logging()
    try:
        procesar_pendientes()
    except Exception:
        logger.exception("El procesamiento de entidades terminó con error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
