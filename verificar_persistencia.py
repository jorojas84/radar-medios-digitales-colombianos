"""Comprueba la escritura real contra una base PostgreSQL
"""

import os
import sys
import uuid
from datetime import datetime, timezone

from base_datos import crear_conexion, guardar_log, guardar_noticias


def main() -> int:
    """Inserta, relee, actualiza y limpia una noticia de prueba."""
    nombre_base = os.getenv("DB_NAME", "")
    if "test" not in nombre_base.lower():
        print(
            "Abortado: DB_NAME debe contener 'test' para evitar usar producción."
        )
        return 1

    identificador = uuid.uuid4().hex
    url = f"https://news-collector.test/integracion/{identificador}"
    medio = f"__news_collector_integracion_{identificador}"
    conexion = None

    try:
        conexion = crear_conexion()
        noticias = [
            {
                "titulo": "Título de integración inicial",
                "url": url,
                "medio": medio,
                "fecha_publicacion": datetime(
                    2026, 8, 13, 12, 0, tzinfo=timezone.utc
                ),
            }
        ]
        nuevas, vistas = guardar_noticias(conexion, noticias)
        guardar_log(conexion, medio, nuevas, vistas)
        conexion.commit()
        conexion.close()
        conexion = None

        conexion = crear_conexion()
        with conexion.cursor() as cursor:
            cursor.execute(
                """
                SELECT titulo, titulo_anterior, fecha_publicacion
                FROM noticias
                WHERE url = %s
                """,
                (url,),
            )
            noticia_guardada = cursor.fetchone()

            cursor.execute(
                """
                SELECT noticias_nuevas, noticias_vistas, noticias_total
                FROM scrape_logs
                WHERE medio = %s
                ORDER BY ejecutado_en DESC
                LIMIT 1
                """,
                (medio,),
            )
            log_guardado = cursor.fetchone()

        if noticia_guardada is None or log_guardado is None:
            raise RuntimeError("La noticia o el log no se pudieron releer")
        if noticia_guardada[0] != "Título de integración inicial":
            raise RuntimeError("El título inicial no se guardó correctamente")
        if log_guardado != (1, 0, 1):
            raise RuntimeError(f"Conteos inesperados en scrape_logs: {log_guardado}")

        # Segunda escritura: comprueba URL única, título anterior y conservación
        # de una fecha válida cuando el nuevo valor llega como NULL.
        actualizadas, repetidas = guardar_noticias(
            conexion,
            [
                {
                    "titulo": "Título de integración actualizado",
                    "url": url,
                    "medio": medio,
                    "fecha_publicacion": None,
                }
            ],
        )
        conexion.commit()

        if (actualizadas, repetidas) != (0, 1):
            raise RuntimeError(
                f"Conteos inesperados al repetir URL: {(actualizadas, repetidas)}"
            )

        with conexion.cursor() as cursor:
            cursor.execute(
                """
                SELECT titulo, titulo_anterior, fecha_publicacion
                FROM noticias
                WHERE url = %s
                """,
                (url,),
            )
            noticia_actualizada = cursor.fetchone()

        if noticia_actualizada is None:
            raise RuntimeError("La noticia desapareció después del upsert")
        if noticia_actualizada[0] != "Título de integración actualizado":
            raise RuntimeError("El título actualizado no se guardó")
        if noticia_actualizada[1] != "Título de integración inicial":
            raise RuntimeError("No se conservó titulo_anterior")
        if noticia_actualizada[2] is None:
            raise RuntimeError("NULL borró fecha_publicacion")

        print("Persistencia PostgreSQL verificada: inserción, lectura y upsert OK")
        return 0
    finally:
        if conexion is not None:
            try:
                conexion.rollback()
                with conexion.cursor() as cursor:
                    cursor.execute("DELETE FROM noticias WHERE url = %s", (url,))
                    cursor.execute(
                        "DELETE FROM scrape_logs WHERE medio = %s", (medio,)
                    )
                conexion.commit()
            finally:
                conexion.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Persistencia no verificada: {error}", file=sys.stderr)
        raise SystemExit(1)
