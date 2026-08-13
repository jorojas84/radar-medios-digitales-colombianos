"""Aísla la configuración y las operaciones PostgreSQL de la aplicación.

El resto del proyecto trabaja con diccionarios de noticias y no necesita saber
cómo se construye el SQL. Este módulo centraliza:

- La lectura segura de variables de entorno.
- Los timeouts, keepalives y reintentos de conexión.
- El upsert individual y por lotes.
- El registro del resultado de cada medio.

Ninguna función de escritura ejecuta ``commit``. main.py conserva esa decisión
para confirmar en una misma transacción las noticias y su log.
"""

# Biblioteca estándar para configuración, logs, esperas y rutas portables.
import logging
import os
import time
from pathlib import Path

# psycopg2 implementa el protocolo PostgreSQL y execute_values agrupa INSERTs.
import psycopg2
from dotenv import load_dotenv
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import execute_values


# Se carga siempre el .env ubicado junto a este archivo. load_dotenv no
# sobrescribe variables ya definidas por systemd o por la terminal, de modo que
# producción puede reemplazar la configuración local sin editar el código.
load_dotenv(Path(__file__).with_name(".env"))
# Compartir el logger integra estos mensajes en la cronología de main.py.
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
    # Estas cuatro variables no tienen un valor seguro que se pueda inventar.
    variables_requeridas = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    faltantes = [nombre for nombre in variables_requeridas if not os.getenv(nombre)]

    if faltantes:
        raise ValueError(f"Faltan variables de entorno: {', '.join(faltantes)}")

    # Los rangos evitan puertos imposibles, esperas infinitas y reintentos nulos.
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
    # El primer intento ocurre inmediatamente; las esperas aparecen solo tras fallar.
    for intento in range(1, intentos + 1):
        try:
            return psycopg2.connect(**opciones_conexion)
        except psycopg2.OperationalError:
            # En el último intento se conserva la excepción original para diagnóstico.
            if intento == intentos:
                raise

            # Espera exponencial: 1, 2, 4... según el valor base configurado.
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
    # Evitar un INSERT vacío simplifica el SQL y produce un resultado predecible.
    if not noticias:
        return 0, 0

    # Las tuplas respetan exactamente el orden de columnas declarado en el INSERT.
    filas = [
        (
            noticia["titulo"],
            noticia["url"],
            noticia["medio"],
            noticia["fecha_publicacion"],
        )
        for noticia in noticias
    ]

    # Si una sola fila provoca error, PostgreSQL invalida el lote y main hace rollback.
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
                )
            -- Se devuelve una bandera por fila para construir los conteos del log.
            RETURNING (xmax = 0) AS es_nueva
            """,
            filas,
            fetch=True,
        )

    # Cada resultado contiene una tupla de una posición: (es_nueva,).
    nuevas = sum(1 for resultado in resultados if resultado[0])
    ya_existentes = len(resultados) - nuevas
    return nuevas, ya_existentes


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
