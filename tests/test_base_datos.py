"""Pruebas de configuración y recuperación de la conexión PostgreSQL.

``psycopg2.connect`` se reemplaza por mocks: estas pruebas verifican los
parámetros y decisiones de recuperación sin abrir sockets ni necesitar tablas.
"""

# os permite reemplazar temporalmente el entorno leído por crear_conexion.
import os
import unittest
from unittest.mock import MagicMock, patch

import psycopg2

from base_datos import crear_conexion


# Configuración mínima, deliberadamente distinta a producción, usada por todos
# los escenarios. La contraseña es ficticia y jamás se envía a una conexión real.
ENTORNO_VALIDO = {
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "5432",
    "DB_NAME": "app_test",
    "DB_USER": "app",
    "DB_PASSWORD": "secreto-prueba",
    "DB_CONNECT_TIMEOUT": "4",
    "DB_STATEMENT_TIMEOUT_MS": "5000",
    "DB_LOCK_TIMEOUT_MS": "2000",
    "DB_CONNECT_ATTEMPTS": "3",
    "DB_RETRY_BASE_DELAY": "1",
}


class BaseDatosTests(unittest.TestCase):
    """Comprueba límites y reintentos sin abrir una base de datos real."""

    def test_rechaza_parametros_numericos_fuera_de_rango(self):
        """Una configuración imposible falla antes de abrir PostgreSQL."""
        casos = (
            ("DB_PORT", "0"),
            ("DB_CONNECT_TIMEOUT", "301"),
            ("DB_STATEMENT_TIMEOUT_MS", "0"),
            ("DB_LOCK_TIMEOUT_MS", "-1"),
            ("DB_CONNECT_ATTEMPTS", "0"),
            ("DB_RETRY_BASE_DELAY", "61"),
        )

        for variable, valor in casos:
            with self.subTest(variable=variable):
                entorno = {**ENTORNO_VALIDO, variable: valor}
                with (
                    patch.dict(os.environ, entorno, clear=True),
                    patch("base_datos.psycopg2.connect") as conectar,
                    self.assertRaisesRegex(ValueError, variable),
                ):
                    crear_conexion()

                conectar.assert_not_called()

    def test_reintenta_conexion_despues_de_error_temporal(self):
        """La segunda conexión puede recuperarse tras un fallo operativo."""
        # Esta instancia representa la conexión que retorna el segundo intento.
        conexion = MagicMock()

        # side_effect reproduce la secuencia fallo temporal -> conexión exitosa.
        with (
            patch.dict(os.environ, ENTORNO_VALIDO, clear=True),
            patch(
                "base_datos.psycopg2.connect",
                side_effect=[psycopg2.OperationalError("temporal"), conexion],
            ) as conectar,
            patch("base_datos.time.sleep") as esperar,
        ):
            resultado = crear_conexion()

        # Se devuelve exactamente la conexión exitosa y solo se espera una vez.
        self.assertIs(resultado, conexion)
        self.assertEqual(conectar.call_count, 2)
        esperar.assert_called_once_with(1)

    def test_configura_limites_de_postgresql(self):
        """La conexión recibe timeouts y keepalives para no quedar colgada."""
        conexion = MagicMock()

        # connect no se ejecuta: solo guardamos los kwargs con que fue llamado.
        with (
            patch.dict(os.environ, ENTORNO_VALIDO, clear=True),
            patch("base_datos.psycopg2.connect", return_value=conexion) as conectar,
        ):
            crear_conexion()

        # call_args permite auditar el contrato entre nuestro código y psycopg2.
        opciones = conectar.call_args.kwargs
        self.assertEqual(opciones["connect_timeout"], 4)
        self.assertIn("statement_timeout=5000", opciones["options"])
        self.assertIn("lock_timeout=2000", opciones["options"])
        self.assertEqual(opciones["application_name"], "news_collector")
        self.assertEqual(opciones["keepalives"], 1)

if __name__ == "__main__":
    unittest.main()
