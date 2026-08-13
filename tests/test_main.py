"""Pruebas del coordinador y sus rutas de recuperación.

Aquí se simulan fuentes y conexiones completas para verificar decisiones de
main.py: continuar, abortar, hacer rollback o impedir una segunda instancia.
Ningún escenario descarga internet ni escribe en PostgreSQL.
"""

# tempfile permite probar flock con un archivo aislado que se elimina al final.
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg2

import configuracion
import main
import operacion
from scraper import ScraperError


# Fuentes mínimas válidas reutilizadas en los escenarios de coordinación.
MEDIO_UNO = {
    "nombre": "Medio Uno",
    "url_fuente": "https://medio-uno.test/feed.xml",
    "formato": "rss",
    "activo": True,
}
MEDIO_DOS = {
    "nombre": "Medio Dos",
    "url_fuente": "https://medio-dos.test/feed.xml",
    "formato": "rss",
    "activo": True,
}
# Forma normalizada que el scraper entrega antes de agregar el nombre del medio.
NOTICIA_VALIDA = {
    "titulo": "Noticia válida",
    "url": "https://medio.test/noticia",
    "fecha_publicacion": None,
}


class OperacionTests(unittest.TestCase):
    """Comprueba continuidad, aborto seguro y exclusión mutua."""

    def test_rechaza_configuracion_incompleta(self):
        """Una clave faltante se detecta antes de iniciar el trabajo."""
        # Falta url_fuente y formato; el mensaje debe mencionar una clave concreta.
        medio_incompleto = {"nombre": "Incompleto", "activo": True}

        # La validación falla sin requerir mocks de red o PostgreSQL.
        with self.assertRaisesRegex(ValueError, "url_fuente"):
            configuracion.validar_medios([medio_incompleto])

    def test_feed_con_todas_las_entradas_invalidas_es_error(self):
        """No se registra éxito cuando la limpieza descarta todo el feed."""
        # La conexión es un mock porque solo interesan commit, close y llamadas SQL.
        conexion = MagicMock()
        # El parser sí encontró una entrada, pero la URL y el título son inválidos.
        entrada_invalida = {"titulo": "", "url": "sin-esquema"}

        # Cada frontera externa se reemplaza; limpiar_noticias permanece real.
        with (
            patch.object(operacion, "crear_conexion", return_value=conexion),
            patch.object(operacion, "descargar_xml", return_value=b"xml"),
            patch.object(operacion, "extraer_noticias", return_value=[entrada_invalida]),
            patch.object(operacion, "registrar_error", return_value=True) as registrar,
            patch.object(operacion, "guardar_noticias") as guardar,
        ):
            resultado = operacion.procesar_medios([MEDIO_UNO])

        # Debe registrarse error y nunca intentar guardar un lote vacío como éxito.
        self.assertEqual(resultado, 1)
        registrar.assert_called_once()
        guardar.assert_not_called()
        conexion.close.assert_called_once()

    def test_continua_con_otro_medio_despues_de_error_de_red(self):
        """Un medio caído no impide guardar los medios posteriores."""
        conexion = MagicMock()

        # extraer_noticias falla para Medio Uno y funciona para Medio Dos.
        with (
            patch.object(operacion, "crear_conexion", return_value=conexion),
            patch.object(operacion, "descargar_xml", return_value=b"xml") as descargar,
            patch.object(
                operacion,
                "extraer_noticias",
                side_effect=[ScraperError("temporal"), [NOTICIA_VALIDA.copy()]],
            ),
            patch.object(operacion, "registrar_error", return_value=True),
            patch.object(operacion, "guardar_noticias", return_value=(1, 0)) as guardar,
            patch.object(operacion, "guardar_log"),
        ):
            resultado = operacion.procesar_medios([MEDIO_UNO, MEDIO_DOS])

        # El resultado global sigue siendo error, aunque el segundo medio se guarde.
        self.assertEqual(resultado, 1)
        self.assertEqual(descargar.call_count, 2)
        guardar.assert_called_once()
        conexion.commit.assert_called_once()

    def test_aborta_si_postgresql_no_permite_rollback(self):
        """Una conexión rota detiene el proceso en vez de encadenar errores."""
        conexion = MagicMock()
        # Una desconexión real suele hacer fallar tanto la consulta como rollback.
        conexion.rollback.side_effect = psycopg2.OperationalError("desconectada")

        # Se configuran dos medios para demostrar que el segundo nunca comienza.
        with (
            patch.object(operacion, "crear_conexion", return_value=conexion),
            patch.object(operacion, "descargar_xml", return_value=b"xml") as descargar,
            patch.object(
                operacion,
                "extraer_noticias",
                return_value=[NOTICIA_VALIDA.copy()],
            ),
            patch.object(
                operacion,
                "guardar_noticias",
                side_effect=psycopg2.OperationalError("desconectada"),
            ),
        ):
            resultado = operacion.procesar_medios([MEDIO_UNO, MEDIO_DOS])

        # Abortar y cerrar es más seguro que seguir usando una conexión corrupta.
        self.assertEqual(resultado, 1)
        descargar.assert_called_once()
        conexion.close.assert_called_once()

    def test_bloqueo_impide_dos_ejecuciones_simultaneas(self):
        """Una segunda instancia no puede adquirir el mismo archivo de lock."""
        # TemporaryDirectory evita dejar archivos de prueba en /tmp.
        with tempfile.TemporaryDirectory() as temporal:
            ruta = str(Path(temporal) / "app.lock")
            # El primer context manager posee el lock; el segundo usa LOCK_NB y falla.
            with (
                patch.dict(os.environ, {"APP_LOCK_FILE": ruta}),
                operacion.bloqueo_ejecucion(),
                self.assertRaises(BlockingIOError),
            ):
                with operacion.bloqueo_ejecucion():
                    pass


if __name__ == "__main__":
    unittest.main()
