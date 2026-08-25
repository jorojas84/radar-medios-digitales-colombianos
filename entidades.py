"""Detecta entidades nombradas en los titulares de las noticias.

Este módulo no conoce PostgreSQL ni decide cuándo procesar una noticia. Recibe
titulares, usa BETO ajustado para reconocimiento de entidades y devuelve
menciones normalizadas en una estructura que luego podrá persistirse desde
otra pieza de la aplicación.
"""

import csv
import re
import unicodedata
from pathlib import Path
from typing import Any


MODELO_ENTIDADES = "mrm8488/bert-spanish-cased-finetuned-ner"
TIPOS_SOPORTADOS = {"PER", "ORG", "LOC"}
PALABRAS_CARGO = {
    "alcalde",
    "contralmirante",
    "expresidente",
    "fiscal",
    "general",
    "gobernador",
    "juez",
    "ministro",
    "papa",
    "presidente",
    "senador",
}
# Estas palabras pueden ser entidades, pero en minúscula suelen ser vocabulario
# común. No se agregan desde el catálogo sin el contexto que BETO sí observa.
PALABRAS_AMBIGUAS_CATALOGO = {"el país", "estado", "meta", "nacional"}
PALABRAS_AMBIGUAS_MINUSCULAS = {"estado", "meta", "nacional"}
# Términos que describen el titular, pero no una entidad nombrada. Se filtran
# antes de consultar el catálogo para que una fila histórica no convierta una
# palabra común en ORG, PER o LOC.
TERMINOS_GENERICOS_CATALOGO = {
    "a", "al", "algo", "alguna", "algunas", "alguno", "algunos", "ante",
    "antes", "aquí", "asi", "así", "bajo", "cada", "como", "cómo", "con",
    "contra", "cual", "cuál", "cuales", "cuáles", "cuando", "cuándo", "de",
    "del", "desde", "donde", "dónde", "dos", "el", "ella", "ellas", "ellos",
    "en", "entre", "era", "es", "esa", "esas", "ese", "eso", "esos", "esta",
    "estas", "este", "esto", "estos", "fue", "ha", "hay", "hasta", "la", "las",
    "le", "les", "lo", "los", "más", "mas", "me", "mi", "mis", "ni", "no",
    "nos", "o", "para", "pero", "por", "que", "qué", "quien", "quién", "se",
    "según", "sin", "si", "sí", "sobre", "su", "sus", "también", "tambien",
    "ser", "te", "tres", "tu", "tus", "un", "una", "uno", "unos", "y", "ya", "yo",
    # Fechas, formatos y vocabulario descriptivo que BETO suele etiquetar.
    "abril", "agosto", "año", "años", "día", "dias", "días", "enero", "febrero",
    "hoy", "julio", "junio", "marzo", "mayo", "mes", "meses", "noche", "noviembre",
    "octubre", "semana", "septiembre", "video", "videos", "foto", "fotos",
    "imagen", "imágenes", "noticia", "noticias", "alerta", "activos", "así",
    "clínica", "clinica", "colombiano", "colombiana", "colombianos", "coronel", "hombre", "mujer",
    "muerto", "murió", "murio", "nuevo", "nueva", "último", "última", "ultimo",
    "ultima", "presidente", "ministro", "fiscal", "gobernador", "senador", "juez",
    "seguridad", "salud", "ayuda", "humo", "niño", "niña", "cáncer", "carro",
    "bus", "puerta", "marca", "minuto", "carrera", "casa", "calle", "camino",
    "gobierno", "nacional", "nacionales", "país", "pais", "sector", "lista", "vivienda", "contratación", "contratacion",
    "auditoría", "auditoria", "energía", "energia", "festival", "fútbol", "futbol",
    "mundial", "liga", "especial", "zona", "estructura", "red", "vamos", "real",
    "rival", "líder", "lider", "influencer", "exfiscal", "dt", "infantil",
}
# BETO puede incluir delimitadores de una mención (comillas, emojis o guiones)
# dentro del span. Se recortan solo en los extremos; la puntuación interna de
# un nombre oficial como ``Monsters, Inc.`` se conserva.
# Doce titulares por lote mantienen acotado el consumo de memoria de BETO.
BATCH_SIZE = 12
RUTA_CATALOGO_CORRECCIONES = Path(__file__).with_name("catalogo_correcciones.csv")
RUTA_CATALOGO_ALIASES = Path(__file__).with_name("catalogo_aliases.csv")
RUTA_CATALOGO_ENTIDADES = Path(__file__).with_name("catalogo_entidades.csv")


def es_termino_generico(texto: str) -> bool:
    """Indica si un texto no representa una entidad nombrada."""
    tokens = normalizar_entidad(texto).split()
    if not tokens:
        return True
    if len(tokens) == 1:
        return tokens[0] in TERMINOS_GENERICOS_CATALOGO
    # Se descartan frases compuestas formadas únicamente por palabras
    # funcionales, fechas o descriptores genéricos (por ejemplo, ``de la``).
    if all(token in TERMINOS_GENERICOS_CATALOGO for token in tokens):
        return True
    # Un fragmento que termina en una preposición/artículo suele ser una
    # detección incompleta, no el nombre oficial de una entidad.
    if tokens[-1] in {"a", "al", "de", "del", "la", "las", "el", "los", "y"}:
        return True
    return False


def cargar_catalogo_correcciones() -> tuple[dict[str, str], set[str]]:
    """Lee correcciones de tipo y spans exactos que deben descartarse.

    El CSV mantiene las correcciones fuera del código para poder actualizar el
    catálogo sin tocar el detector ni volver a entrenar el modelo.
    """
    with RUTA_CATALOGO_CORRECCIONES.open(encoding="utf-8", newline="") as archivo:
        filas = csv.DictReader(archivo)
        catalogo = {}
        descartados = set()
        for fila in filas:
            estado = (fila.get("estado") or "aprobado").casefold()
            texto = normalizar_entidad(fila["texto_normalizado"])
            if estado == "descartado":
                descartados.add(texto)
                continue
            if estado != "aprobado":
                continue
            if es_termino_generico(texto):
                continue
            tipo = fila["tipo_correcto"]
            if not tipo:
                continue
            if tipo not in TIPOS_SOPORTADOS:
                raise ValueError(f"Tipo no soportado en el catálogo: {tipo}")
            catalogo[texto] = tipo
        return catalogo, descartados


def cargar_catalogo_aliases() -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Lee una vez los alias y prepara sus dos usos en el detector."""
    aliases = {}
    candidatos = []
    with RUTA_CATALOGO_ALIASES.open(encoding="utf-8", newline="") as archivo:
        for fila in csv.DictReader(archivo):
            if (fila.get("estado") or "aprobado").casefold() != "aprobado":
                continue
            if fila["tipo"] not in TIPOS_SOPORTADOS:
                continue

            alias = normalizar_entidad(fila["alias"])
            if alias in TERMINOS_DESCARTADOS:
                continue
            aliases[alias] = fila["entidad_canonica"]
            if alias in PALABRAS_AMBIGUAS_CATALOGO:
                continue
            candidatos.append(
                {
                    "texto_normalizado": alias,
                    "nombre_visible": fila["entidad_canonica"],
                    "tipo": fila["tipo"],
                    "fuente": "catalogo_aliases",
                }
            )
    return aliases, candidatos


def cargar_catalogo_entidades() -> list[dict[str, Any]]:
    """Lee las entidades activas que se buscarán en el titular completo."""
    candidatos = {}
    with RUTA_CATALOGO_ENTIDADES.open(
        encoding="utf-8", newline=""
    ) as archivo:
        for fila in csv.DictReader(archivo):
            if (fila.get("activo") or "true").casefold() != "true":
                continue

            texto = normalizar_entidad(fila["texto_normalizado"])
            if not any(caracter.isalnum() for caracter in texto):
                continue
            if (
                texto in TERMINOS_DESCARTADOS
                or texto in PALABRAS_AMBIGUAS_CATALOGO
                or es_termino_generico(texto)
            ):
                continue
            tipo = fila.get("tipo")
            if tipo not in TIPOS_SOPORTADOS:
                continue
            if (
                len(texto.split()) >= 2
                and texto.split()[0] in PALABRAS_CARGO
                and texto != "general electric"
            ):
                continue

            existente = candidatos.get(texto)
            if existente and existente["tipo"] != tipo:
                raise ValueError(
                    f"Entidad con tipos contradictorios en el catálogo: {texto}"
                )
            candidatos[texto] = {
                "texto_normalizado": texto,
                "nombre_visible": fila["nombre_visible"],
                "tipo": tipo,
                "fuente": "catalogo_entidades",
            }

    return list(candidatos.values())


def cargar_modelo() -> Any:
    """Carga BETO con agregación de subpalabras y ejecución en CPU.

    ``pipeline`` transforma las etiquetas BIO del modelo (por ejemplo,
    ``B-PER`` + ``I-PER``) en una mención completa con ``start`` y ``end``.
    La estrategia ``first`` agrupa las subpalabras de un mismo término, algo
    importante para acrónimos como ``ANLA``. El tokenizador rápido es necesario
    para recuperar esos offsets.

    El import se mantiene dentro de la función para que las pruebas unitarias
    del contrato no necesiten instalar ni descargar PyTorch o el modelo.
    """
    try:
        from transformers import (
            AutoModelForTokenClassification,
            AutoTokenizer,
            pipeline,
        )
    except ImportError as error:
        raise RuntimeError(
            "Faltan las dependencias de BETO: instala torch y transformers"
        ) from error

    try:
        # El tokenizador fast conserva los offsets de caracteres del titular.
        tokenizer = AutoTokenizer.from_pretrained(
            MODELO_ENTIDADES,
            use_fast=True,
        )
        # BETO usa la longitud máxima estándar de BERT para titulares cortos.
        tokenizer.model_max_length = 512
        modelo = AutoModelForTokenClassification.from_pretrained(
            MODELO_ENTIDADES,
        )

        # device=-1 fuerza CPU y evita que el worker dependa de una GPU.
        return pipeline(
            "token-classification",
            model=modelo,
            tokenizer=tokenizer,
            aggregation_strategy="first",
            device=-1,
        )
    except OSError as error:
        raise RuntimeError(
            f"No se pudo descargar o cargar el modelo BETO: {MODELO_ENTIDADES}"
        ) from error


def convertir_deteccion_beto(
    titular: str,
    deteccion: dict,
    conteos: dict[str, int],
) -> dict | None:
    """Valida una detección de BETO y la convierte al formato persistible."""
    tipo = deteccion.get("entity_group")
    if tipo not in TIPOS_SOPORTADOS:
        return None

    inicio = deteccion.get("start")
    fin = deteccion.get("end")
    if inicio is None or fin is None:
        raise RuntimeError(
            "El tokenizador de BETO no devolvió posiciones de entidad"
        )

    inicio, fin = recortar_puntuacion_externa(titular, inicio, fin)
    texto = titular[inicio:fin]
    if not texto.strip():
        return None
    if not any(caracter.isalnum() for caracter in texto):
        conteos["puntuacion_descartada"] += 1
        return None

    conteos["beto"] += 1
    texto_normalizado = normalizar_entidad(texto)
    if (
        texto_normalizado in TERMINOS_DESCARTADOS
        or es_termino_generico(texto_normalizado)
    ):
        conteos["genericos_descartados"] += 1
        return None
    if texto_normalizado in PALABRAS_CARGO:
        conteos["cargos_descartados"] += 1
        return None
    if texto_normalizado == "el país" or (
        texto_normalizado in PALABRAS_AMBIGUAS_MINUSCULAS
        and texto.strip().islower()
    ):
        conteos["ambiguedades_descartadas"] += 1
        return None

    entidad = crear_entidad(titular, inicio, fin, tipo, "beto")
    if "catalogo_aliases" in entidad["fuentes_deteccion"]:
        conteos["aliases"] += 1
    if entidad["tipo"] != tipo:
        conteos["tipos_corregidos"] += 1
    return entidad


def detectar_entidades(
    modelo: Any,
    titulares: list[str],
    resumen: dict[str, int] | None = None,
) -> list[list[dict]]:
    """Detecta personas, organizaciones y lugares en varios titulares.

    La salida conserva una lista por cada titular de entrada. Las posiciones
    ``inicio`` y ``fin`` son índices de caracteres y permiten ubicar exactamente
    cada mención dentro del texto original.
    """
    if not titulares:
        return []

    # La pipeline procesa la lista en lotes y devuelve una lista de detecciones
    # por titular, manteniendo el mismo orden de entrada.
    detecciones_por_titular = modelo(
        titulares,
        batch_size=BATCH_SIZE,
    )

    if len(detecciones_por_titular) != len(titulares):
        raise RuntimeError(
            "BETO devolvió una cantidad distinta de titulares"
        )

    resultados = []
    conteos = {
        "beto": 0,
        "aliases": 0,
        "tipos_corregidos": 0,
        "catalogo": 0,
        "puntuacion_descartada": 0,
        "cargos_descartados": 0,
        "ambiguedades_descartadas": 0,
        "genericos_descartados": 0,
    }

    for titular, detecciones in zip(
        titulares,
        detecciones_por_titular,
        strict=True,
    ):
        entidades = []

        for deteccion in detecciones:
            entidad = convertir_deteccion_beto(titular, deteccion, conteos)
            if entidad is not None:
                entidades.append(entidad)

        entidades_antes_catalogo = {
            (
                entidad["inicio"],
                entidad["fin"],
                entidad["texto_normalizado"],
                entidad["tipo"],
            )
            for entidad in entidades
        }
        entidades = completar_entidades_con_catalogo(titular, entidades)
        entidades_despues_catalogo = {
            (
                entidad["inicio"],
                entidad["fin"],
                entidad["texto_normalizado"],
                entidad["tipo"],
            )
            for entidad in entidades
        }
        conteos["catalogo"] += len(
            entidades_despues_catalogo - entidades_antes_catalogo
        )
        resultados.append(entidades)

    if resumen is not None:
        conteos["titulares"] = len(titulares)
        conteos["entidades_guardadas"] = sum(map(len, resultados))
        for nombre, valor in conteos.items():
            resumen[nombre] = resumen.get(nombre, 0) + valor

    return resultados


def resolver_entidad_canonica(
    texto_normalizado: str,
    nombre_visible: str | None = None,
) -> str:
    """Devuelve el nombre aprobado o conserva el texto visible del titular."""
    return CATALOGO_CANONICOS.get(
        texto_normalizado,
        nombre_visible or texto_normalizado,
    )


def crear_entidad(
    titular: str,
    inicio: int,
    fin: int,
    tipo: str,
    fuente: str,
    nombre_visible: str | None = None,
) -> dict:
    """Construye una entidad y aplica alias y correcciones en un solo lugar."""
    texto = titular[inicio:fin]
    texto_normalizado = normalizar_entidad(texto)
    fuentes = [fuente]

    if texto_normalizado in CATALOGO_ALIASES:
        fuentes.append("catalogo_aliases")

    tipo_corregido = CATALOGO_CORRECCIONES.get(texto_normalizado, tipo)
    if tipo_corregido != tipo:
        fuentes.append("catalogo_correcciones")

    return {
        "texto": texto,
        "texto_normalizado": texto_normalizado,
        "entidad_canonica": resolver_entidad_canonica(
            texto_normalizado,
            nombre_visible or texto,
        ),
        "tipo": tipo_corregido,
        "inicio": inicio,
        "fin": fin,
        "fuentes_deteccion": fuentes,
    }


def buscar_entidades_catalogo(
    titular: str,
    entidades_beto: list[dict],
) -> list[dict]:
    """Busca candidatos catalogados sin recortar spans completos de BETO."""
    titular_exacto, posiciones_exactas = normalizar_busqueda_con_posiciones(titular)
    titular_sin_acentos, posiciones_sin_acentos = normalizar_busqueda_con_posiciones(
        titular,
        quitar_acentos=True,
    )
    palabras_titular = set(re.findall(r"\w+", titular_exacto))
    palabras_titular.update(re.findall(r"\w+", titular_sin_acentos))
    candidatos = [
        candidato
        for palabra in palabras_titular
        for candidato in CATALOGO_BUSQUEDA_POR_PRIMERA_PALABRA.get(palabra, ())
    ]
    # El índice reduce candidatos, pero se conserva el orden global original:
    # así una coincidencia larga sigue ganando frente a una corta solapada.
    candidatos.sort(key=lambda candidato: candidato["_orden_busqueda"])

    coincidencias = []
    for candidato in candidatos:
        if candidato["_quitar_acentos"]:
            titular_busqueda = titular_sin_acentos
            posiciones = posiciones_sin_acentos
        else:
            titular_busqueda = titular_exacto
            posiciones = posiciones_exactas

        for coincidencia in candidato["_patron_busqueda"].finditer(titular_busqueda):
            inicio = posiciones[coincidencia.start()]
            fin = posiciones[coincidencia.end() - 1] + 1
            entidad = crear_entidad(
                titular,
                inicio,
                fin,
                candidato["tipo"],
                candidato["fuente"],
                candidato["nombre_visible"],
            )
            # Si BETO ya devolvió exactamente el mismo span y tipo, se
            # conserva su resultado (incluidos sus alias y formato actual).
            if any(
                existente["inicio"] == inicio
                and existente["fin"] == fin
                and existente["tipo"] == entidad["tipo"]
                for existente in entidades_beto
            ):
                continue
            # Un alias aprobado sí puede recuperar una persona dentro de un
            # span demasiado amplio de BETO, como ``Gobierno de la Espriella``.
            # Los candidatos históricos normales siguen sin partir spans largos.
            if any(
                existente["inicio"] <= inicio
                and fin <= existente["fin"]
                and (
                    existente["inicio"], existente["fin"]
                ) != (inicio, fin)
                for existente in entidades_beto
            ) and candidato["fuente"] != "catalogo_aliases":
                continue
            coincidencias.append(entidad)
    return coincidencias


def completar_entidades_con_catalogo(
    titular: str,
    entidades_beto: list[dict],
) -> list[dict]:
    """Combina BETO y catálogo, priorizando los spans más específicos."""
    coincidencias = buscar_entidades_catalogo(titular, entidades_beto)

    # La lista ya viene ordenada por longitud del catálogo; se conservan
    # coincidencias no solapadas para no guardar ``de la Espriella`` dentro de
    # ``Abelardo de la Espriella`` como una segunda entidad.
    seleccionadas = []
    for entidad in coincidencias:
        if any(
            entidad["inicio"] < otra["fin"]
            and otra["inicio"] < entidad["fin"]
            for otra in seleccionadas
        ):
            continue
        seleccionadas.append(entidad)

    # BETO se conserva cuando no hay una coincidencia catalogada que cubra su
    # posición; si la hay, gana el span del catálogo por ser más específico.
    resultado = seleccionadas[:]
    for entidad in entidades_beto:
        if any(
            entidad["inicio"] < otra["fin"]
            and otra["inicio"] < entidad["fin"]
            for otra in seleccionadas
        ):
            continue
        resultado.append(entidad)

    return sorted(resultado, key=lambda entidad: (entidad["inicio"], entidad["fin"]))


def normalizar_busqueda_con_posiciones(
    texto: str,
    quitar_acentos: bool = False,
) -> tuple[str, list[int]]:
    """Normaliza la búsqueda y conserva los índices del texto original."""
    caracteres = []
    posiciones = []
    for indice, caracter in enumerate(texto):
        forma = "NFKD" if quitar_acentos else "NFKC"
        caracter_normalizado = unicodedata.normalize(forma, caracter.casefold())
        for parte in caracter_normalizado:
            if quitar_acentos and unicodedata.combining(parte):
                continue
            caracteres.append(parte)
            posiciones.append(indice)
    return "".join(caracteres), posiciones


def preparar_indice_catalogo(
    candidatos: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Prepara patrones una vez y los agrupa por primera palabra."""
    indice: dict[str, list[dict[str, Any]]] = {}
    for orden, candidato in enumerate(candidatos):
        # Las entidades de una palabra exigen coincidencia exacta. Las frases
        # mantienen tolerancia de tildes para titulares reconstruidos de slugs.
        quitar_acentos = len(candidato["texto_normalizado"].split()) >= 2
        texto_busqueda, _ = normalizar_busqueda_con_posiciones(
            candidato["texto_normalizado"],
            quitar_acentos=quitar_acentos,
        )
        partes = texto_busqueda.split()
        if not partes:
            continue
        primer_token = re.search(r"\w+", partes[0])
        if primer_token is None:
            continue
        candidato_preparado = candidato.copy()
        candidato_preparado["_orden_busqueda"] = orden
        candidato_preparado["_quitar_acentos"] = quitar_acentos
        candidato_preparado["_patron_busqueda"] = re.compile(
            r"(?<!\w)"
            + r"\s+".join(re.escape(parte) for parte in partes)
            + r"(?!\w)"
        )
        indice.setdefault(primer_token.group(0), []).append(candidato_preparado)
    return indice


def normalizar_entidad(texto: str) -> str:
    """Unifica espacios y mayúsculas sin borrar la mención original.

    Esta normalización sirve para agrupar ``Bogotá`` repetido en titulares. No
    intenta resolver alias como ``Petro`` y ``Gustavo Petro``; eso pertenece a
    una etapa posterior de vinculación de entidades.
    """
    texto_unicode = unicodedata.normalize("NFKC", texto)
    # casefold agrupa mayúsculas y minúsculas de forma más completa que lower.
    # split/join convierte cualquier secuencia de espacios en un solo espacio.
    return " ".join(texto_unicode.casefold().split())


def recortar_puntuacion_externa(
    titular: str,
    inicio: int,
    fin: int,
) -> tuple[int, int]:
    """Ajusta offsets para excluir comillas y signos que rodean la entidad."""
    while inicio < fin and (
        not titular[inicio].isalnum()
        or unicodedata.combining(titular[inicio])
    ):
        inicio += 1
    while fin > inicio and (
        not titular[fin - 1].isalnum()
        or unicodedata.combining(titular[fin - 1])
    ):
        fin -= 1
    return inicio, fin


# Se carga después de definir la normalización que usa el lector del CSV.
CATALOGO_CORRECCIONES, TERMINOS_DESCARTADOS = cargar_catalogo_correcciones()
CATALOGO_ALIASES, ALIASES_BUSQUEDA = cargar_catalogo_aliases()
CATALOGO_ENTIDADES = cargar_catalogo_entidades()
CATALOGO_CANONICOS = dict(CATALOGO_ALIASES)
for _candidato in CATALOGO_ENTIDADES:
    CATALOGO_CANONICOS.setdefault(
        _candidato["texto_normalizado"],
        _candidato["nombre_visible"],
    )
CATALOGO_BUSQUEDA = sorted(
    CATALOGO_ENTIDADES + ALIASES_BUSQUEDA,
    key=lambda dato: (
        len(dato["texto_normalizado"].split()),
        len(dato["texto_normalizado"]),
    ),
    reverse=True,
)
CATALOGO_BUSQUEDA_POR_PRIMERA_PALABRA = preparar_indice_catalogo(
    CATALOGO_BUSQUEDA
)
