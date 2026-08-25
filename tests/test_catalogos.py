"""Valida la estructura y las contradicciones críticas de los catálogos."""

import csv
import unittest
import unicodedata
from collections import defaultdict

import entidades


def normalizar_sin_acentos(texto: str) -> str:
    """Normaliza igual que la búsqueda tolerante a tildes del detector."""
    normalizado = entidades.normalizar_entidad(texto)
    return "".join(
        caracter
        for caracter in unicodedata.normalize("NFKD", normalizado)
        if not unicodedata.combining(caracter)
    )


def leer_csv(ruta):
    """Lee un catálogo completo conservando sus nombres de columnas."""
    with ruta.open(encoding="utf-8", newline="") as archivo:
        lector = csv.DictReader(archivo)
        return lector.fieldnames, list(lector)


class CatalogosTests(unittest.TestCase):
    """Evita que una edición manual introduzca filas ambiguas o ignoradas."""

    def test_catalogo_entidades_no_tiene_filas_ignoradas_ni_duplicadas(self):
        encabezados, filas = leer_csv(entidades.RUTA_CATALOGO_ENTIDADES)

        self.assertEqual(
            encabezados,
            ["texto_normalizado", "nombre_visible", "tipo", "activo"],
        )
        activas = [fila for fila in filas if fila["activo"].casefold() == "true"]
        claves = [entidades.normalizar_entidad(fila["texto_normalizado"]) for fila in activas]

        self.assertEqual(len(claves), len(set(claves)))
        self.assertEqual(len(activas), len(entidades.CATALOGO_ENTIDADES))
        self.assertTrue(all(fila["tipo"] in entidades.TIPOS_SOPORTADOS for fila in activas))

    def test_catalogo_no_confunde_nombres_compuestos_al_quitar_tildes(self):
        _, filas = leer_csv(entidades.RUTA_CATALOGO_ENTIDADES)
        grupos = defaultdict(set)

        for fila in filas:
            if fila["activo"].casefold() != "true":
                continue
            texto = fila["texto_normalizado"]
            if len(texto.split()) >= 2:
                grupos[normalizar_sin_acentos(texto)].add(fila["tipo"])

        conflictos = {texto: tipos for texto, tipos in grupos.items() if len(tipos) > 1}
        self.assertEqual(conflictos, {})

    def test_aliases_aprobados_coinciden_con_el_tipo_principal(self):
        _, filas_entidades = leer_csv(entidades.RUTA_CATALOGO_ENTIDADES)
        encabezados, filas_aliases = leer_csv(entidades.RUTA_CATALOGO_ALIASES)
        tipos_principales = {
            entidades.normalizar_entidad(fila["texto_normalizado"]): fila["tipo"]
            for fila in filas_entidades
            if fila["activo"].casefold() == "true"
        }

        self.assertEqual(
            encabezados,
            ["alias", "entidad_canonica", "tipo", "estado"],
        )
        aprobados = [
            fila for fila in filas_aliases if fila["estado"].casefold() == "aprobado"
        ]
        conflictos = {
            fila["alias"]: (
                tipos_principales[entidades.normalizar_entidad(fila["alias"])],
                fila["tipo"],
            )
            for fila in aprobados
            if entidades.normalizar_entidad(fila["alias"]) in tipos_principales
            and tipos_principales[entidades.normalizar_entidad(fila["alias"])] != fila["tipo"]
        }

        self.assertEqual(conflictos, {})
        self.assertEqual(len(aprobados), len(entidades.CATALOGO_ALIASES))

    def test_correcciones_aprobadas_no_se_ignoran_silenciosamente(self):
        encabezados, filas = leer_csv(entidades.RUTA_CATALOGO_CORRECCIONES)
        aprobadas = {
            entidades.normalizar_entidad(fila["texto_normalizado"]): fila["tipo_correcto"]
            for fila in filas
            if fila["estado"].casefold() == "aprobado"
        }

        self.assertEqual(
            encabezados,
            ["texto_normalizado", "tipo_correcto", "estado"],
        )
        self.assertTrue(all(tipo in entidades.TIPOS_SOPORTADOS for tipo in aprobadas.values()))
        self.assertEqual(aprobadas, entidades.CATALOGO_CORRECCIONES)

if __name__ == "__main__":
    unittest.main()
