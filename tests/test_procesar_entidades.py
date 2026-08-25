"""Pruebas del worker sin abrir PostgreSQL ni cargar el modelo real."""

import unittest
from unittest.mock import MagicMock, patch

import psycopg2

import procesar_entidades


class ProcesarEntidadesTests(unittest.TestCase):
    """Comprueba commits por lote y rollback ante fallos del detector."""

    def test_procesa_lotes_hasta_que_no_queden_pendientes(self):
        """Cada lote se guarda y confirma antes de solicitar el siguiente."""
        conexion = MagicMock()
        modelo = object()
        noticias = [{"id": "noticia-1", "titulo": "Petro visita Bogotá"}]
        entidades = [[
            {
                "texto": "Petro",
                "texto_normalizado": "petro",
                "tipo": "PER",
                "inicio": 0,
                "fin": 5,
            }
        ]]

        with (
            patch.object(procesar_entidades, "crear_conexion", return_value=conexion),
            patch.object(procesar_entidades, "cargar_modelo", return_value=modelo),
            patch.object(
                procesar_entidades,
                "obtener_noticias_pendientes",
                side_effect=[noticias, []],
            ) as obtener,
            patch.object(
                procesar_entidades,
                "detectar_entidades",
                return_value=entidades,
            ),
            patch.object(procesar_entidades, "guardar_entidades") as guardar,
        ):
            resultado = procesar_entidades.procesar_pendientes(limite=1)

        self.assertEqual(resultado, 1)
        self.assertEqual(obtener.call_count, 2)
        guardar.assert_called_once_with(
            conexion,
            [("noticia-1", entidades[0])],
        )
        conexion.commit.assert_called_once()
        conexion.close.assert_called_once()

    def test_revierte_y_cierra_si_falla_el_detector(self):
        """Un fallo deja el lote sin marcar para poder reintentarlo."""
        conexion = MagicMock()
        noticias = [{"id": "noticia-1", "titulo": "Titular"}]

        with (
            patch.object(procesar_entidades, "crear_conexion", return_value=conexion),
            patch.object(procesar_entidades, "cargar_modelo", return_value=object()),
            patch.object(
                procesar_entidades,
                "obtener_noticias_pendientes",
                return_value=noticias,
            ),
            patch.object(
                procesar_entidades,
                "detectar_entidades",
                side_effect=RuntimeError("modelo falló"),
            ),
            self.assertRaisesRegex(RuntimeError, "modelo falló"),
        ):
            procesar_entidades.procesar_pendientes()

        conexion.rollback.assert_called_once()
        conexion.commit.assert_not_called()
        conexion.close.assert_called_once()

    def test_un_rollback_roto_no_oculta_el_error_original(self):
        """La limpieza informa su fallo sin reemplazar la causa principal."""
        conexion = MagicMock()
        conexion.rollback.side_effect = psycopg2.OperationalError("sin conexión")

        with (
            patch.object(procesar_entidades, "crear_conexion", return_value=conexion),
            patch.object(
                procesar_entidades,
                "cargar_modelo",
                side_effect=RuntimeError("modelo falló"),
            ),
            self.assertRaisesRegex(RuntimeError, "modelo falló"),
        ):
            procesar_entidades.procesar_pendientes()

        conexion.close.assert_called_once()

    def test_main_devuelve_cero_aunque_procese_noticias(self):
        """La cantidad procesada no se usa como código de salida."""
        with (
            patch.object(procesar_entidades, "configurar_logging"),
            patch.object(procesar_entidades, "procesar_pendientes", return_value=250),
        ):
            resultado = procesar_entidades.main()

        self.assertEqual(resultado, 0)

    def test_main_registra_el_error_y_devuelve_uno(self):
        """Un fallo operativo queda registrado para el scheduler."""
        with (
            patch.object(procesar_entidades, "configurar_logging"),
            patch.object(
                procesar_entidades,
                "procesar_pendientes",
                side_effect=RuntimeError("falló"),
            ),
            patch.object(procesar_entidades.logger, "exception") as registrar,
        ):
            resultado = procesar_entidades.main()

        self.assertEqual(resultado, 1)
        registrar.assert_called_once()


if __name__ == "__main__":
    unittest.main()
