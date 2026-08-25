"""Configuración y operaciones PostgreSQL de la aplicación.

Las escrituras no hacen ``commit``; el coordinador controla cada transacción.
"""

import logging
import os
import time
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import execute_values


# Las variables del sistema prevalecen sobre el archivo local.
load_dotenv(Path(__file__).with_name(".env"))
logger = logging.getLogger("app")


def _leer_entero_configuracion(
    nombre: str,
    valor_predeterminado: int,
    minimo: int,
    maximo: int,
) -> int:
    """Lee un entero del entorno y exige que esté dentro de un rango seguro.

    Centralizar esta validación evita que un valor como ``DB_CONNECT_ATTEMPTS=0``
    desactive silenciosamente un bucle de conexión o que un timeout inválido
    deje el proceso mal configurado.
    """
    valor_texto = os.getenv(nombre, str(valor_predeterminado))

    try:
        valor = int(valor_texto)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{nombre} debe ser un número entero") from error

    if not minimo <= valor <= maximo:
        raise ValueError(
            f"{nombre} debe estar entre {minimo} y {maximo}; recibido: {valor}"
        )

    return valor


def crear_conexion() -> PgConnection:
    """Abre PostgreSQL con límites de tiempo y reintentos controlados.

    Returns:
        Una conexión lista para crear cursores y transacciones.

    Raises:
        ValueError: Si faltan credenciales o una opción numérica es inválida.
        psycopg2.OperationalError: Si todos los intentos de conexión fallan.

    Los reintentos cubren fallos breves del túnel o del servidor. Los timeouts
    evitan que un proceso automatizado permanezca bloqueado indefinidamente.
    """
    variables_requeridas = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    faltantes = [nombre for nombre in variables_requeridas if not os.getenv(nombre)]

    if faltantes:
        raise ValueError(f"Faltan variables de entorno: {', '.join(faltantes)}")

    puerto = _leer_entero_configuracion("DB_PORT", 5432, 1, 65535)
    connect_timeout = _leer_entero_configuracion(
        "DB_CONNECT_TIMEOUT", 10, 1, 300
    )
    statement_timeout = _leer_entero_configuracion(
        "DB_STATEMENT_TIMEOUT_MS", 60000, 1, 3_600_000
    )
    lock_timeout = _leer_entero_configuracion(
        "DB_LOCK_TIMEOUT_MS", 10000, 1, 3_600_000
    )
    intentos = _leer_entero_configuracion("DB_CONNECT_ATTEMPTS", 3, 1, 10)
    espera_base = _leer_entero_configuracion(
        "DB_RETRY_BASE_DELAY", 1, 1, 60
    )

    opciones_conexion = {
        "host": os.getenv("DB_HOST"),
        "dbname": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "port": puerto,
        "connect_timeout": connect_timeout,
        "application_name": "news_collector",
        "options": (
            f"-c statement_timeout={statement_timeout} "
            f"-c lock_timeout={lock_timeout}"
        ),
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }
    for intento in range(1, intentos + 1):
        try:
            return psycopg2.connect(**opciones_conexion)
        except psycopg2.OperationalError:
            if intento == intentos:
                raise

            espera = espera_base * (2 ** (intento - 1))
            logger.warning(
                "PostgreSQL no respondió; reintento %s de %s en %s segundos",
                intento + 1,
                intentos,
                espera,
            )
            time.sleep(espera)


def guardar_noticias(conn: PgConnection, noticias: list[dict]) -> tuple[int, int]:
    """Inserta un lote y devuelve ``(nuevas, ya_existentes)``.

    ``execute_values`` construye un único INSERT con múltiples filas. Esto reduce
    viajes entre Python y PostgreSQL y mantiene atómico el lote de cada medio.
    El commit permanece bajo control de main.py.
    """
    if not noticias:
        return 0, 0

    filas = [
        (
            noticia["titulo"],
            noticia["url"],
            noticia["medio"],
            noticia["fecha_publicacion"],
        )
        for noticia in noticias
    ]

    with conn.cursor() as cursor:
        resultados = execute_values(
            cursor,
            """
            INSERT INTO noticias (titulo, url, medio, fecha_publicacion)
            VALUES %s
            -- La URL única hace que repetir una ejecución sea idempotente.
            ON CONFLICT (url) DO UPDATE
            SET titulo_anterior = CASE
                    -- Se conserva solamente el título inmediatamente anterior.
                    WHEN noticias.titulo IS DISTINCT FROM EXCLUDED.titulo
                    THEN noticias.titulo
                    ELSE noticias.titulo_anterior
                END,
                titulo = EXCLUDED.titulo,
                -- NULL nunca borra una fecha válida que ya estaba almacenada.
                fecha_publicacion = COALESCE(
                    EXCLUDED.fecha_publicacion,
                    noticias.fecha_publicacion
                ),
                -- Un título nuevo invalida las entidades del título anterior.
                entidades_detectadas = CASE
                    WHEN noticias.titulo IS DISTINCT FROM EXCLUDED.titulo
                    THEN FALSE
                    ELSE noticias.entidades_detectadas
                END
            -- Se devuelve una bandera por fila para construir los conteos del log.
            RETURNING (xmax = 0) AS es_nueva
            """,
            filas,
            fetch=True,
        )

        # Una noticia pendiente no debe conservar entidades de un título anterior.
        cursor.execute(
            """
            DELETE FROM entidades
            USING noticias
            WHERE entidades.noticia_id = noticias.id
              AND noticias.url = ANY(%s)
              AND noticias.entidades_detectadas IS FALSE
            """,
            ([noticia["url"] for noticia in noticias],),
        )

    nuevas = sum(1 for resultado in resultados if resultado[0])
    ya_existentes = len(resultados) - nuevas
    return nuevas, ya_existentes


def obtener_noticias_pendientes(
    conn: PgConnection,
    limite: int = 100,
) -> list[dict]:
    """Obtiene titulares todavía no procesados y bloquea ese lote.

    ``SKIP LOCKED`` permite que dos workers no tomen la misma noticia. El
    bloqueo dura hasta el commit que guarda sus entidades.
    """
    if limite < 1:
        raise ValueError("El límite debe ser mayor que cero")

    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, titulo
            FROM noticias
            WHERE titulo IS NOT NULL
              AND btrim(titulo) <> ''
              AND entidades_detectadas IS NOT TRUE
            ORDER BY descubierto_en, id
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (limite,),
        )
        return [
            {"id": fila[0], "titulo": fila[1]}
            for fila in cursor.fetchall()
        ]


def guardar_entidades(
    conn: PgConnection,
    resultados: list[tuple[object, list[dict]]],
) -> None:
    """Reemplaza las menciones de un lote y marca sus noticias como detectadas.

    La función no confirma la transacción. Así, una falla al insertar una
    mención revierte tanto las entidades como las marcas del lote completo.
    """
    if not resultados:
        return

    ids = [noticia_id for noticia_id, _ in resultados]

    with conn.cursor() as cursor:
        # Se eliminan las menciones anteriores para que el análisis sea reemplazable
        # si se vuelve a procesar un titular después de cambiar el modelo.
        cursor.execute(
            "DELETE FROM entidades WHERE noticia_id = ANY(%s::uuid[])",
            (ids,),
        )

        # Una noticia puede no tener entidades; en ese caso no se ejecuta INSERT,
        # pero sí se marca como procesada más abajo.
        filas = [
            (
                noticia_id,
                entidad["texto"],
                entidad["texto_normalizado"],
                entidad.get("entidad_canonica", entidad["texto_normalizado"]),
                entidad.get("fuentes_deteccion", []),
                entidad["tipo"],
                entidad["inicio"],
                entidad["fin"],
            )
            for noticia_id, entidades in resultados
            for entidad in entidades
        ]

        if filas:
            # execute_values inserta todas las menciones del lote en una operación.
            execute_values(
                cursor,
                """
                INSERT INTO entidades (
                    noticia_id,
                    texto,
                    texto_normalizado,
                    entidad_canonica,
                    fuentes_deteccion,
                    tipo,
                    inicio,
                    fin
                )
                VALUES %s
                """,
                filas,
            )

        # La marca se actualiza en la misma transacción que el INSERT. Si algo falla,
        # el rollback conserva la noticia como pendiente para reintentarlo.
        cursor.execute(
            """
            UPDATE noticias
            SET entidades_detectadas = TRUE
            WHERE id = ANY(%s::uuid[])
            """,
            (ids,),
        )


def guardar_log(
    conn: PgConnection,
    medio: str,
    noticias_nuevas: int,
    noticias_vistas: int,
    status: str = "ok",
    error_msg: str | None = None,
) -> None:
    """Inserta el resultado de un medio en ``scrape_logs``.

    ``status`` vale ``ok`` por defecto; registrar_error lo cambia a ``error`` y
    proporciona ``error_msg``. La columna ``noticias_total`` no se envía porque
    PostgreSQL la calcula como nuevas más vistas.
    """
    # Igual que las noticias, el log pertenece a la transacción controlada por main.
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO scrape_logs (
                medio,
                noticias_nuevas,
                noticias_vistas,
                status,
                error_msg
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                medio,
                noticias_nuevas,
                noticias_vistas,
                status,
                error_msg,
            ),
        )
