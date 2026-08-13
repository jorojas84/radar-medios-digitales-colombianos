"""Punto de entrada de la aplicación."""

import logging

from configuracion import validar_medios
from medios import medios
from operacion import bloqueo_ejecucion, configurar_logging, procesar_medios


def main() -> int:
    """Valida, protege y ejecuta una recopilación completa."""
    configurar_logging()

    try:
        validar_medios(medios)
    except ValueError as error:
        # La configuración es inválida antes de abrir servicios externos.
        logging.getLogger("app").error("Configuración inválida: %s", error)
        return 1

    try:
        with bloqueo_ejecucion():
            return procesar_medios(medios)
    except BlockingIOError:
        logging.getLogger("app").warning(
            "Se omite esta ejecución porque otra instancia sigue activa"
        )
        return 0
    except OSError as error:
        logging.getLogger("app").error(
            "No se pudo crear el bloqueo de ejecución: %s", error
        )
        return 1
    except Exception:
        logging.getLogger("app").exception("Error inesperado durante la ejecución")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
