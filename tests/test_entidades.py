"""Pruebas del contrato de detección sin cargar BETO ni PyTorch."""

import unittest
from unittest.mock import MagicMock, patch

from entidades import (
    BATCH_SIZE,
    MODELO_ENTIDADES,
    cargar_modelo,
    detectar_entidades,
    normalizar_entidad,
)


class EntidadesTests(unittest.TestCase):
    """Comprueba el formato y las decisiones propias de la aplicación."""

    def test_detecta_solamente_tipos_soportados(self):
        """Se conservan personas, organizaciones y lugares con sus posiciones."""
        modelo = MagicMock()
        modelo.return_value = [
            [
                {"entity_group": "PER", "start": 0, "end": 5},
                {"entity_group": "ORG", "start": 19, "end": 28},
                {"entity_group": "LOC", "start": 32, "end": 38},
                {"entity_group": "MISC", "start": 50, "end": 55},
            ]
        ]
        titulares = ["Petro se reúne con Ecopetrol en Bogotá durante la COP16"]

        resultado = detectar_entidades(modelo, titulares)

        self.assertEqual(
            resultado,
            [
                [
                    {
                        "texto": "Petro",
                        "texto_normalizado": "petro",
                        "entidad_canonica": "Gustavo Petro",
                        "fuentes_deteccion": ["beto", "catalogo_aliases"],
                        "tipo": "PER",
                        "inicio": 0,
                        "fin": 5,
                    },
                    {
                        "texto": "Ecopetrol",
                        "texto_normalizado": "ecopetrol",
                        "entidad_canonica": "Ecopetrol",
                        "fuentes_deteccion": ["beto"],
                        "tipo": "ORG",
                        "inicio": 19,
                        "fin": 28,
                    },
                    {
                        "texto": "Bogotá",
                        "texto_normalizado": "bogotá",
                        "entidad_canonica": "Bogotá",
                        "fuentes_deteccion": ["beto"],
                        "tipo": "LOC",
                        "inicio": 32,
                        "fin": 38,
                    },
                ]
            ],
        )
        modelo.assert_called_once_with(
            titulares,
            batch_size=BATCH_SIZE,
        )

    def test_descarta_detecciones_solo_de_puntuacion(self):
        """Un signo etiquetado por BETO no se guarda como entidad."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "LOC", "start": 0, "end": 1}]]

        self.assertEqual(detectar_entidades(modelo, [";"]), [[]])

    def test_recorta_comillas_del_span_sin_perder_offsets(self):
        """Las comillas del titular no se convierten en parte de la entidad."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "PER", "start": 0, "end": 11}]]

        resultado = detectar_entidades(modelo, ['"El Tuerto" fue capturado'])[0]

        self.assertEqual(resultado[0]["texto"], "El Tuerto")
        self.assertEqual((resultado[0]["inicio"], resultado[0]["fin"]), (1, 10))

    def test_descarta_un_cargo_sin_nombre(self):
        """Un cargo aislado no contamina el ranking de personas."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "PER", "start": 0, "end": 10}]]

        self.assertEqual(detectar_entidades(modelo, ["Presidente"]), [[]])

    def test_conserva_un_resultado_por_cada_titular(self):
        """Un titular sin entidades mantiene su posición dentro del lote."""
        modelo = MagicMock()
        modelo.return_value = [
            [],
            [{"entity_group": "LOC", "start": 13, "end": 17}],
        ]

        resultado = detectar_entidades(
            modelo,
            ["Sube el dólar", "Concierto en Cali"],
        )

        self.assertEqual(resultado[0], [])
        self.assertEqual(resultado[1][0]["texto"], "Cali")
        self.assertEqual(resultado[1][0]["texto_normalizado"], "cali")

    def test_rechaza_un_resultado_por_titular_incorrecto(self):
        """Una respuesta desalineada no debe asociar entidades a otra noticia."""
        modelo = MagicMock()
        modelo.return_value = [[]]

        with self.assertRaisesRegex(RuntimeError, "cantidad distinta"):
            detectar_entidades(modelo, ["Uno", "Dos"])

    def test_rechaza_una_entidad_sin_posiciones(self):
        """Sin offsets no se puede guardar ni auditar una mención."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "PER"}]]

        with self.assertRaisesRegex(RuntimeError, "posiciones"):
            detectar_entidades(modelo, ["Petro"])

    def test_lote_vacio_no_ejecuta_el_modelo(self):
        """Una entrada vacía produce una salida vacía sin trabajo innecesario."""
        modelo = MagicMock()

        resultado = detectar_entidades(modelo, [])

        self.assertEqual(resultado, [])
        modelo.assert_not_called()

    def test_normaliza_espacios_sin_cambiar_el_texto_original(self):
        """La clave de agrupación conserva los acentos y colapsa espacios."""
        self.assertEqual(normalizar_entidad("  Bogotá   D.C.  "), "bogotá d.c.")

    def test_aplica_catalogo_para_corregir_tipo(self):
        """Una regla del catálogo cambia Colombia de ORG a LOC."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "ORG", "start": 0, "end": 8}]]

        resultado = detectar_entidades(modelo, ["Colombia avanza"])

        self.assertEqual(resultado[0][0]["tipo"], "LOC")

    def test_catalogo_no_cambia_tipo_ya_correcto(self):
        """Una regla no altera una entidad que BETO ya clasificó bien."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "LOC", "start": 0, "end": 8}]]

        resultado = detectar_entidades(modelo, ["Colombia avanza"])

        self.assertEqual(resultado[0][0]["tipo"], "LOC")

    def test_catalogo_no_recorta_una_entidad_larga_de_beto(self):
        """Un candidato corto no reemplaza un span completo ya detectado."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "ORG", "start": 0, "end": 18}]]

        resultado = detectar_entidades(modelo, ["seleccion colombia gana"])

        self.assertEqual(resultado[0][0]["texto"], "seleccion colombia")
        self.assertEqual(resultado[0][0]["entidad_canonica"], "Selección Colombia")

    def test_resuelve_alias_seguro_a_entidad_canonica(self):
        """El apellido se agrupa con el nombre completo conocido."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "PER", "start": 0, "end": 15}]]

        resultado = detectar_entidades(modelo, ["De la Espriella habló"])

        self.assertEqual(
            resultado[0][0]["entidad_canonica"],
            "Abelardo de la Espriella",
        )

    def test_recupera_alias_dentro_de_un_span_largo_de_beto(self):
        """Un descriptor amplio no oculta una persona con alias aprobado."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "ORG", "start": 0, "end": 24}]]

        resultado = detectar_entidades(
            modelo,
            ["Gobierno de la Espriella anunció cambios"],
        )[0]

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["texto"], "de la Espriella")
        self.assertEqual(
            resultado[0]["entidad_canonica"],
            "Abelardo de la Espriella",
        )
        self.assertEqual(resultado[0]["tipo"], "PER")

    def test_normaliza_canonica_de_entidad_catalogada(self):
        """Una entidad catalogada conserva una sola capitalización visible."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "PER", "start": 0, "end": 8}]]

        resultado = detectar_entidades(modelo, ["Abelardo habló"])

        self.assertEqual(
            resultado[0][0]["entidad_canonica"],
            "Abelardo de la Espriella",
        )

    def test_conserva_capitalizacion_de_beto_sin_catalogo(self):
        """Una entidad nueva conserva su escritura visible y no queda en minúsculas."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "PER", "start": 0, "end": 14}]]

        resultado = detectar_entidades(modelo, ["Aitana Bonmatí ganó"])

        self.assertEqual(resultado[0][0]["texto_normalizado"], "aitana bonmatí")
        self.assertEqual(resultado[0][0]["entidad_canonica"], "Aitana Bonmatí")

    def test_descarta_termino_generico_aunque_beto_lo_etiquete(self):
        """Palabras comunes no entran al ranking como entidades."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "PER", "start": 0, "end": 5}]]

        self.assertEqual(detectar_entidades(modelo, ["Video nuevo"]), [[]])

    def test_descarta_regla_del_csv_aunque_venga_de_beto(self):
        """Un span descartado en correcciones no entra desde BETO."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "PER", "start": 0, "end": 5}]]

        self.assertEqual(detectar_entidades(modelo, ["Joven capturado"]), [[]])

    def test_un_descarte_no_bloquea_un_nombre_compuesto(self):
        """La exclusión de una palabra aislada no elimina nombres completos."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "PER", "start": 0, "end": 11}]]

        resultado = detectar_entidades(modelo, ["Joven Pérez habló"])[0]

        self.assertEqual(resultado[0]["texto"], "Joven Pérez")
        self.assertEqual(resultado[0]["tipo"], "PER")

    def test_completa_una_entidad_omitida_por_beto_con_catalogo(self):
        """La segunda pasada recupera una frase catalogada y no ambigua."""
        modelo = MagicMock()
        modelo.return_value = [[]]

        resultado = detectar_entidades(modelo, ["Estados Unidos anuncia ayuda"])

        self.assertEqual(resultado[0][0]["texto"], "Estados Unidos")
        self.assertEqual(resultado[0][0]["tipo"], "LOC")
        self.assertEqual(
            resultado[0][0]["fuentes_deteccion"],
            ["catalogo_entidades"],
        )

    def test_conserva_general_electric_aunque_empiece_por_cargo(self):
        """El nombre de la empresa no se confunde con un rango militar."""
        modelo = MagicMock()
        modelo.return_value = [[]]

        resultado = detectar_entidades(modelo, ["General Electric anunció cambios"])

        self.assertEqual(resultado[0][0]["texto"], "General Electric")
        self.assertEqual(resultado[0][0]["tipo"], "ORG")

    def test_catalogo_encuentra_entidad_aunque_falte_el_acento(self):
        """La búsqueda tolera tildes omitidas sin alterar el texto original."""
        modelo = MagicMock()
        modelo.return_value = [[]]

        resultado = detectar_entidades(modelo, ["Alerta en Bogota"])[0]
        bogota = next(entidad for entidad in resultado if entidad["texto"] == "Bogota")

        self.assertEqual(bogota["entidad_canonica"], "Bogotá")
        self.assertEqual(bogota["tipo"], "LOC")
        self.assertEqual(
            bogota["fuentes_deteccion"],
            ["catalogo_entidades", "catalogo_aliases"],
        )
        self.assertEqual((bogota["inicio"], bogota["fin"]), (10, 16))

    def test_catalogo_no_confunde_palabras_por_quitar_tildes(self):
        """La palabra común ``cortes`` no se convierte en el apellido Cortés."""
        modelo = MagicMock()
        modelo.side_effect = [[[]], [[]], [[]]]

        self.assertEqual(detectar_entidades(modelo, ["La empresa anunció cortes"]), [[]])
        resultado = detectar_entidades(modelo, ["La Fiscalía marcó diferencias"])[0]
        self.assertFalse(any(entidad["texto"] == "marcó" for entidad in resultado))

        apellido = detectar_entidades(modelo, ["Cortés respondió"])[0]
        self.assertEqual(apellido[0]["texto"], "Cortés")
        self.assertEqual(apellido[0]["tipo"], "PER")

    def test_alias_recupera_ee_uu_fragmentado_por_beto(self):
        """El alias completo reemplaza el fragmento ``uu`` devuelto por BETO."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "LOC", "start": 3, "end": 5}]]

        resultado = detectar_entidades(modelo, ["ee uu anuncia medidas"])[0]

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["texto"], "ee uu")
        self.assertEqual(resultado[0]["entidad_canonica"], "Estados Unidos")
        self.assertEqual(resultado[0]["tipo"], "LOC")
        self.assertEqual(
            resultado[0]["fuentes_deteccion"],
            ["catalogo_entidades", "catalogo_aliases"],
        )

    def test_corrige_pinalito_de_persona_a_lugar(self):
        """La regla explícita evita contar Piñalito como una persona."""
        modelo = MagicMock()
        modelo.return_value = [[{"entity_group": "PER", "start": 0, "end": 8}]]

        resultado = detectar_entidades(modelo, ["Piñalito estrena acueducto"])[0]

        self.assertEqual(resultado[0]["tipo"], "LOC")
        self.assertEqual(
            resultado[0]["fuentes_deteccion"],
            ["beto", "catalogo_correcciones"],
        )

    def test_corrige_volcan_purace_recuperado_por_catalogo(self):
        """Un accidente histórico de BETO no convierte el volcán en empresa."""
        modelo = MagicMock()
        modelo.return_value = [[]]

        resultado = detectar_entidades(modelo, ["Alerta en el volcan purace"])[0]
        purace = next(
            entidad for entidad in resultado if entidad["texto"] == "volcan purace"
        )

        self.assertEqual(purace["entidad_canonica"], "Volcán Puracé")
        self.assertEqual(purace["tipo"], "LOC")

    def test_descarta_termino_ambiguo_solo_cuando_esta_en_minusculas(self):
        """Se elimina la palabra común ``meta`` pero se conserva el departamento."""
        modelo = MagicMock()
        modelo.return_value = [
            [{"entity_group": "LOC", "start": 0, "end": 4}],
            [{"entity_group": "LOC", "start": 0, "end": 4}],
        ]

        resultado = detectar_entidades(modelo, ["meta cumplida", "Meta avanza"])

        self.assertEqual(resultado[0], [])
        self.assertEqual(resultado[1][0]["texto"], "Meta")
        self.assertEqual(resultado[1][0]["tipo"], "LOC")

    def test_carga_el_modelo_configurado(self):
        """La carga usa BETO, el tokenizador fast y ejecución en CPU."""
        transformers_simulado = MagicMock()
        tokenizer = MagicMock()
        modelo = object()
        pipeline = object()
        transformers_simulado.AutoTokenizer.from_pretrained.return_value = tokenizer
        transformers_simulado.AutoModelForTokenClassification.from_pretrained.return_value = modelo
        transformers_simulado.pipeline.return_value = pipeline

        with patch.dict("sys.modules", {"transformers": transformers_simulado}):
            resultado = cargar_modelo()

        self.assertIs(resultado, pipeline)
        transformers_simulado.AutoTokenizer.from_pretrained.assert_called_once_with(
            MODELO_ENTIDADES,
            use_fast=True,
        )
        transformers_simulado.AutoModelForTokenClassification.from_pretrained.assert_called_once_with(
            MODELO_ENTIDADES,
        )
        transformers_simulado.pipeline.assert_called_once_with(
            "token-classification",
            model=modelo,
            tokenizer=tokenizer,
            aggregation_strategy="first",
            device=-1,
        )

    def test_explica_cuando_faltan_dependencias(self):
        """Un entorno incompleto recibe un error claro de instalación."""
        with (
            patch.dict("sys.modules", {"transformers": None}),
            self.assertRaisesRegex(RuntimeError, "torch y transformers"),
        ):
            cargar_modelo()


if __name__ == "__main__":
    unittest.main()
