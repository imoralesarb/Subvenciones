# -*- coding: utf-8 -*-

"""
ingesta_bdns.py
----------------
Sincroniza convocatorias de la Base de Datos Nacional de Subvenciones
(BDNS / SNPSAP) directamente contra la tabla `subvenciones` de Supabase.

Estrategia:
  1. Recorre el buscador de convocatorias ordenado por fecha de recepción DESC.
  2. Se detiene cuando encuentra una convocatoria anterior a la ventana.
  3. Para cada convocatoria consulta el detalle.
  4. Extrae organismo, ámbito, CCAA, beneficiarios, etc.
  5. Compara con Supabase.
  6. Genera embeddings solo para registros nuevos o modificados.
  7. Sube los registros por lotes.

IMPORTANTE:
Las fechas se convierten a texto ISO (YYYY-MM-DD) antes de enviarlas
a Supabase para evitar el error:
    Object of type date is not JSON serializable
"""

import re
import time
from datetime import date, datetime, timedelta

import requests

from common import (
    generar_embedding,
    obtener_cliente_supabase,
    obtener_registros_existentes,
    subir_en_lotes,
)


# ============================================================
# CONFIGURACIÓN
# ============================================================

BDNS_BASE = "https://www.infosubvenciones.es/bdnstrans/api"

BDNS_BUSQUEDA = (
    f"{BDNS_BASE}/convocatorias/busqueda"
)

BDNS_DETALLE = (
    f"{BDNS_BASE}/convocatorias"
)

TAMANO_PAGINA = 50

DIAS_ATRAS = 11

PAUSA_ENTRE_PAGINAS_SEGUNDOS = 0.5

PAUSA_ENTRE_DETALLES_SEGUNDOS = 0.2

MAX_PAGINAS_SEGURIDAD = 100

LOTE_ENVIO_SUPABASE = 25

FUENTE = "BDNS"


# ============================================================
# CAMPOS COMPARABLES
# ============================================================

CAMPOS_COMPARABLES = (
    "titulo",
    "descripcion",
    "organismo",
    "ambito",
    "ccaa",
    "beneficiarios",
    #"tipo_beneficiario_elegible",
    "titulo_bases_reguladoras",
    "tipo_convocatoria",
    "presupuesto_total",
    "fecha_inicio_solicitud",
    "fecha_fin_solicitud",
)


# ============================================================
# OBTENER PÁGINA DE CONVOCATORIAS
# ============================================================

def obtener_pagina_convocatorias(pagina: int) -> dict:

    parametros = {
        "page": pagina,
        "pageSize": TAMANO_PAGINA,
        "order": "fechaRecepcion",
        "direccion": "desc",
        "vpd": "GE",
    }

    print(
        f"--> Consultando página {pagina}...",
        flush=True
    )

    respuesta = requests.get(
        BDNS_BUSQUEDA,
        params=parametros,
        timeout=30
    )

    print(
        f"    HTTP: {respuesta.status_code}",
        flush=True
    )

    respuesta.raise_for_status()

    return respuesta.json()


# ============================================================
# OBTENER DETALLE DE CONVOCATORIA
# ============================================================

def obtener_detalle_convocatoria(
    numero_convocatoria
) -> dict:

    parametros = {
        "numConv": numero_convocatoria,
        "vpd": "GE"
    }

    print(
        f"      -> Consultando detalle "
        f"numConv={numero_convocatoria}...",
        flush=True
    )

    try:

        respuesta = requests.get(
            BDNS_DETALLE,
            params=parametros,
            timeout=30
        )

        print(
            f"         HTTP: {respuesta.status_code}",
            flush=True
        )

        if respuesta.status_code != 200:

            print(
                f"         Error: "
                f"{respuesta.text[:300]}",
                flush=True
            )

            return {}

        datos = respuesta.json()

        if isinstance(datos, dict):

            return datos

        if isinstance(datos, list) and datos:

            return datos[0]

        return {}

    except Exception as error:

        print(
            f"         ERROR obteniendo detalle: "
            f"{error}",
            flush=True
        )

        return {}


# ============================================================
# CONVERTIR FECHA BDNS
# ============================================================

def convertir_fecha(fecha):

    if not fecha:

        return None

    try:

        return datetime.fromisoformat(
            str(fecha).replace("Z", "")
        ).date()

    except Exception:

        try:

            return datetime.strptime(
                str(fecha)[:10],
                "%Y-%m-%d"
            ).date()

        except Exception:

            return None


# ============================================================
# CONVERTIR FECHA A TEXTO
#
# IMPORTANTE:
# Supabase recibe JSON.
# Los objetos date de Python NO son serializables.
#
# Por eso:
# date(2026, 9, 11)
# ->
# "2026-09-11"
# ============================================================

def fecha_a_texto(fecha):

    if fecha is None:

        return None

    if isinstance(
        fecha,
        (date, datetime)
    ):

        return fecha.strftime(
            "%Y-%m-%d"
        )

    return str(fecha)


# ============================================================
# EXTRAER ORGANISMO
# ============================================================

def extraer_organismo(data: dict):

    organo = data.get(
        "organo"
    ) or {}

    if not isinstance(
        organo,
        dict
    ):

        return None

    return (
        organo.get("nivel3")
        or organo.get("nivel2")
        or organo.get("nivel1")
        or organo.get("nombre")
    )


# ============================================================
# EXTRAER ÁMBITO
#
# Ejemplo:
#
# <organo>
#     <nivel1>AUTONOMICA</nivel1>
#     <nivel2>ILLES BALEARS</nivel2>
#     <nivel3>SERVICIO DE OCUPACIÓN...
#
# Resultado:
#
# ambito = "AUTONOMICA"
#
# ============================================================

def extraer_ambito(data: dict):

    organo = data.get(
        "organo"
    ) or {}

    if not isinstance(
        organo,
        dict
    ):

        return None

    return organo.get(
        "nivel1"
    )


# ============================================================
# EXTRAER CCAA
# ============================================================

def extraer_ccaa(data: dict) -> list:

    regiones = data.get(
        "regiones"
    ) or []

    if not isinstance(
        regiones,
        list
    ):

        return []

    valores = []

    for region in regiones:

        if not isinstance(
            region,
            dict
        ):

            continue

        descripcion = region.get(
            "descripcion"
        )

        if descripcion:

            valores.append(
                str(descripcion)
            )

    return list(
        dict.fromkeys(
            valores
        )
    )


# ============================================================
# EXTRAER BENEFICIARIOS
# ============================================================

def extraer_beneficiarios(
    data: dict
) -> str:

    beneficiarios = data.get(
        "tiposBeneficiarios"
    ) or []

    if not isinstance(
        beneficiarios,
        list
    ):

        return ""

    valores = []

    for beneficiario in beneficiarios:

        if not isinstance(
            beneficiario,
            dict
        ):

            continue

        descripcion = beneficiario.get(
            "descripcion"
        )

        if descripcion:

            valores.append(
                str(descripcion)
            )

    return ", ".join(
        dict.fromkeys(
            valores
        )
    )


# ============================================================
# EXTRAER TIPO DE BENEFICIARIO ELEGIBLE (campo nuevo, aditivo)
# ============================================================
# Reutiliza el mismo "tiposBeneficiarios" que ya usa extraer_beneficiarios
# (confirmado funcionando: es de donde sale el texto libre de arriba),
# pero como LISTA -- no como una única cadena unida por comas -- para
# poder guardarla en la columna array tipo_beneficiario_elegible y
# ofrecerla como filtro de selección múltiple en la interfaz.
#def extraer_tipo_beneficiario_elegible(data: dict) -> list:
#    beneficiarios = data.get("tiposBeneficiarios") or []
#    if not isinstance(beneficiarios, list):
#        return []

#    valores = []
#    for beneficiario in beneficiarios:
#        if not isinstance(beneficiario, dict):
#            continue
#        descripcion = beneficiario.get("descripcion")
#        if descripcion:
#            valores.append(str(descripcion))

#    return list(dict.fromkeys(valores))


# ============================================================
# EXTRAER TÍTULO DE BASES REGULADORAS (campo nuevo, aditivo)
# ============================================================
# Se intenta primero "basesReguladoras.titulo" -- un campo estructurado
# (título + url) que, según fuentes de terceros que documentan la API,
# existe en la respuesta de la BDNS, pero que NO se ha podido confirmar
# de forma directa en este proyecto (sin acceso de red a
# infosubvenciones.es al escribir esto). Si no aparece, se recurre a
# "descripcionBasesReguladoras", el campo que SÍ está confirmado y en
# uso (ver extraer_descripcion): es un texto más largo/descriptivo, no
# un título limpio, pero es mejor que dejar el campo vacío.
def extraer_titulo_bases_reguladoras(data: dict) -> list:
    bases = data.get("basesReguladoras")
    if isinstance(bases, dict):
        titulo = bases.get("titulo")
        if titulo:
            return [str(titulo)]

    descripcion_bases = data.get("descripcionBasesReguladoras")
    if descripcion_bases:
        return [str(descripcion_bases)]

    return []


# ============================================================
# TEXTO PLANO + EMBEDDING PRECOMPUTADO DE "BASES REGULADORAS"
# ============================================================
# Antes, la búsqueda de "Título de bases reguladoras" recalculaba el
# embedding de este campo en Python, fila a fila, EN CADA BÚSQUEDA del
# usuario (ver evaluar_bases en la versión anterior de app/app.py) --
# el cuello de botella real al combinar los dos filtros de búsqueda.
# Ahora se calcula UNA SOLA VEZ aquí, en la ingesta (igual que ya se
# hace con el embedding principal vía construir_texto_completo), y se
# consulta luego por índice HNSW desde la función RPC
# buscar_por_bases_reguladoras (ver sql/migracion_busqueda_hibrida_bases_reguladoras.sql).
#
# _limpiar_texto_bases_para_embedding quita el boilerplate legal más
# habitual (fórmulas de encabezado de la norma, "por la que se
# aprueban/establecen las bases reguladoras de/para...") ANTES de
# generar el embedding: los títulos de bases reguladoras son textos
# cortos y muy formulaicos, y ese boilerplate casi idéntico entre
# convocatorias distintas diluye la señal semántica real (la materia
# de la convocatoria) y hace que el ranking por similitud coseno salga
# poco discriminado. Si tras la limpieza no queda texto útil, se usa el
# original tal cual -- mejor un embedding con algo de ruido que uno
# vacío.
PATRONES_BOILERPLATE_BASES = (
    r"por (?:la|el) (?:que|cual) se (?:aprueban|establecen|regulan|fijan|modifican)",
    r"bases reguladoras (?:de|para|del|específicas de|específicas para)",
    r"^(?:orden|resoluci[oó]n|real decreto|decreto|ley)\s+[\w./-]+,?\s*de\s+\d{1,2}\s+de\s+\w+(?:\s+de\s+\d{4})?,?",
)


def _limpiar_texto_bases_para_embedding(texto: str) -> str:
    limpio = texto
    for patron in PATRONES_BOILERPLATE_BASES:
        limpio = re.sub(patron, " ", limpio, flags=re.IGNORECASE)
    # Tras quitar las fórmulas de arriba quedan a veces artículos/
    # preposiciones sueltos y duplicados (p. ej. "las bases reguladoras
    # de la digitalización" -> "las   la digitalización"): se colapsan
    # aquí para no ensuciar el embedding con ese ruido.
    limpio = re.sub(
        r"\b(el|la|los|las|de|del|para)\s+(el|la|los|las|de|del|para)\b",
        r"\2",
        limpio,
        flags=re.IGNORECASE,
    )
    limpio = re.sub(r"\s+", " ", limpio).strip(" ,.-")
    return limpio or texto


def construir_texto_y_embedding_bases_reguladoras(titulo_bases_reguladoras: list) -> tuple:
    """
    Devuelve (texto_plano, embedding) para la columna array
    `titulo_bases_reguladoras`. texto_plano alimenta la búsqueda léxica
    (pg_trgm) y NO se limpia de boilerplate (para la coincidencia
    léxica el texto completo, tal cual aparece, es lo correcto);
    embedding sí se genera sobre el texto ya limpiado.
    Devuelve (None, None) si la lista viene vacía -- no hay nada que
    guardar ni que embeder.
    """
    if not titulo_bases_reguladoras:
        return None, None

    texto_plano = " ".join(titulo_bases_reguladoras).strip()
    if not texto_plano:
        return None, None

    texto_para_embedding = _limpiar_texto_bases_para_embedding(texto_plano)
    embedding = generar_embedding(texto_para_embedding)
    return texto_plano, embedding


# ============================================================
# EXTRAER TIPO DE CONVOCATORIA (campo nuevo, aditivo)
# ============================================================
# AVISO: a diferencia de las dos funciones anteriores, el nombre exacto
# del campo JSON de la API de la BDNS para "tipo de convocatoria"
# (concurrencia competitiva / asignación directa / no publicable, según
# la propia documentación de la BDNS) NO se ha podido confirmar sin
# poder consultar la API en vivo -- "instrumentos" es un campo
# DISTINTO ("instrumentos de ayuda": subvención, préstamo, garantía...,
# según la documentación oficial de la BDNS), así que no se usa aquí
# para no mezclar dos conceptos distintos.
#
# Se prueban varios nombres de campo plausibles, de forma defensiva; si
# tras una ejecución real ves que `tipo_convocatoria` sale siempre
# vacío, revisa una respuesta real de
# GET /bdnstrans/api/convocatorias?numConv=<uno cualquiera>&vpd=GE
# y ajusta CANDIDATOS_CAMPO_TIPO_CONVOCATORIA con el nombre real.
CANDIDATOS_CAMPO_TIPO_CONVOCATORIA = (
    "tipoConvocatoria",
    "procedimientoConcesion",
    "tipoProcedimiento",
)


def extraer_tipo_convocatoria(data: dict) -> list:
    for campo in CANDIDATOS_CAMPO_TIPO_CONVOCATORIA:
        valor = data.get(campo)
        if not valor:
            continue
        if isinstance(valor, dict):
            valor = valor.get("descripcion") or valor.get("nombre")
        if valor:
            return [str(valor)]
    return []


# ============================================================
# EXTRAER DESCRIPCIÓN
# ============================================================

def extraer_descripcion(
    data: dict
):

    partes = []

    finalidad = data.get(
        "descripcionFinalidad"
    )

    bases = data.get(
        "descripcionBasesReguladoras"
    )

    if finalidad:

        partes.append(
            f"Finalidad: {finalidad}"
        )

    if bases:

        partes.append(
            f"Bases reguladoras: {bases}"
        )

    if not partes:

        return None

    return "\n".join(
        partes
    )


# ============================================================
# CONSTRUIR TEXTO COMPLETO
# ============================================================

def construir_texto_completo(
    datos: dict
) -> str:

    partes = [
        f"Título: {datos['titulo']}"
    ]

    if datos.get(
        "descripcion"
    ):

        partes.append(
            datos["descripcion"]
        )

    if datos.get(
        "organismo"
    ):

        partes.append(
            f"Organismo: "
            f"{datos['organismo']}"
        )

    if datos.get(
        "ambito"
    ):

        partes.append(
            f"Ámbito: "
            f"{datos['ambito']}"
        )

    if datos.get(
        "ccaa"
    ):

        partes.append(
            f"CCAA: "
            f"{', '.join(datos['ccaa'])}"
        )

    if datos.get(
        "beneficiarios"
    ):

        partes.append(
            f"Beneficiarios: "
            f"{datos['beneficiarios']}"
        )

    if datos.get(
        "presupuesto_total"
    ) is not None:

        partes.append(
            f"Presupuesto: "
            f"{datos['presupuesto_total']}"
        )

    if datos.get(
        "fecha_inicio_solicitud"
    ):

        partes.append(
            f"Inicio solicitud: "
            f"{datos['fecha_inicio_solicitud']}"
        )

    if datos.get(
        "fecha_fin_solicitud"
    ):

        partes.append(
            f"Fin solicitud: "
            f"{datos['fecha_fin_solicitud']}"
        )

    return "\n".join(
        partes
    )


# ============================================================
# CONSTRUIR REGISTRO
# ============================================================

def construir_registro(
    item: dict,
    detalle: dict
) -> dict:

    # El detalle tiene prioridad
    data = {
        **item,
        **detalle
    }

    # --------------------------------------------------------
    # CÓDIGO BDNS
    # --------------------------------------------------------

    codigo_bdns = (
        data.get(
            "codigoBDNS"
        )
        or data.get(
            "numeroConvocatoria"
        )
    )

    numero_convocatoria = (
        data.get(
            "numeroConvocatoria"
        )
    )

    # --------------------------------------------------------
    # TÍTULO
    # --------------------------------------------------------

    titulo = (
        data.get(
            "descripcion"
        )
        or "Sin título"
    )

    # --------------------------------------------------------
    # FECHAS
    #
    # AQUÍ ESTÁ EL CAMBIO IMPORTANTE:
    #
    # Antes:
    #     "fecha_publicacion": date(...)
    #
    # Ahora:
    #     "fecha_publicacion": "2026-09-11"
    #
    # Así Supabase puede serializarlo como JSON.
    # --------------------------------------------------------

    fecha_publicacion = fecha_a_texto(
        convertir_fecha(
            data.get(
                "fechaRecepcion"
            )
        )
    )

    fecha_inicio = fecha_a_texto(
        convertir_fecha(
            data.get(
                "fechaInicioSolicitud"
            )
        )
    )

    fecha_fin = fecha_a_texto(
        convertir_fecha(
            data.get(
                "fechaFinSolicitud"
            )
        )
    )

    # --------------------------------------------------------
    # ORGANISMO
    # --------------------------------------------------------

    organismo = extraer_organismo(
        data
    )

    # --------------------------------------------------------
    # ÁMBITO
    # --------------------------------------------------------

    ambito = extraer_ambito(
        data
    )

    # --------------------------------------------------------
    # CCAA
    # --------------------------------------------------------

    ccaa = extraer_ccaa(
        data
    )

    # --------------------------------------------------------
    # BENEFICIARIOS
    # --------------------------------------------------------

    beneficiarios = extraer_beneficiarios(
        data
    )

    # --------------------------------------------------------
    # CAMPOS NUEVOS (aditivos, ver funciones extraer_* de arriba)
    # --------------------------------------------------------

    #tipo_beneficiario_elegible = extraer_tipo_beneficiario_elegible(
    #    data
    #)

    titulo_bases_reguladoras = extraer_titulo_bases_reguladoras(
        data
    )

    # Texto plano + embedding precomputado (ver docstring de la función):
    # esto es lo que sustituye el recalculo en Python de la version
    # anterior de app/app.py.
    titulo_bases_reguladoras_texto, embedding_bases_reguladoras = (
        construir_texto_y_embedding_bases_reguladoras(
            titulo_bases_reguladoras
        )
    )

    tipo_convocatoria = extraer_tipo_convocatoria(
        data
    )

    # --------------------------------------------------------
    # DESCRIPCIÓN
    # --------------------------------------------------------

    descripcion = extraer_descripcion(
        data
    )

    # --------------------------------------------------------
    # URL
    # --------------------------------------------------------

    url_oficial = None

    if numero_convocatoria:

        url_oficial = (
            "https://www.infosubvenciones.es/"
            "bdnstrans/GE/es/convocatoria/"
            f"{numero_convocatoria}"
        )

    # --------------------------------------------------------
    # REGISTRO
    # --------------------------------------------------------

    return {

        "codigo_unico":
            (
                f"BDNS-{codigo_bdns}"
                if codigo_bdns
                else None
            ),

        "codigo_bdns":
            (
                str(codigo_bdns)
                if codigo_bdns
                else None
            ),

        "fuente_origen":
            FUENTE,

        "titulo":
            titulo,

        "descripcion":
            descripcion,

        "organismo":
            organismo,

        "ambito":
            ambito,

        "ccaa":
            ccaa,

        "url_oficial":
            url_oficial,

        "url_boe":
            None,

        "fecha_publicacion":
            fecha_publicacion,

        "fecha_inicio_solicitud":
            fecha_inicio,

        "fecha_fin_solicitud":
            fecha_fin,

        "presupuesto_total":
            data.get(
                "presupuestoTotal"
            ),

        "beneficiarios":
            beneficiarios,

       # "tipo_beneficiario_elegible":
       #     tipo_beneficiario_elegible,

        "titulo_bases_reguladoras":
            titulo_bases_reguladoras,

        "titulo_bases_reguladoras_texto":
            titulo_bases_reguladoras_texto,

        "embedding_bases_reguladoras":
            embedding_bases_reguladoras,

        "tipo_convocatoria":
            tipo_convocatoria,
    }


# ============================================================
# PREPARAR REGISTROS PARA SUBIR
# ============================================================

def preparar_lote_para_subir(
    normalizados: list,
    registros_existentes: dict
) -> list:

    a_subir = []

    for datos in normalizados:

        existente = registros_existentes.get(
            datos["codigo_unico"]
        )

        texto_completo = (
            construir_texto_completo(
                datos
            )
        )

        # ----------------------------------------------------
        # NUEVO
        # ----------------------------------------------------

        if existente is None:

            datos["texto_completo"] = (
                texto_completo
            )

            datos["embedding"] = (
                generar_embedding(
                    texto_completo
                )
            )

            datos["es_novedad"] = True

            datos["es_actualizada"] = False

            a_subir.append(
                datos
            )

            continue

        # ----------------------------------------------------
        # COMPROBAR CAMBIOS
        # ----------------------------------------------------

        ha_cambiado = any(
            str(
                existente.get(campo)
            )
            !=
            str(
                datos.get(campo)
            )
            for campo in CAMPOS_COMPARABLES
        )

        if not ha_cambiado:

            continue

        # ----------------------------------------------------
        # ACTUALIZADO
        # ----------------------------------------------------

        datos["texto_completo"] = (
            texto_completo
        )

        datos["embedding"] = (
            generar_embedding(
                texto_completo
            )
        )

        datos["es_novedad"] = False

        datos["es_actualizada"] = True

        a_subir.append(
            datos
        )

    return a_subir


# ============================================================
# EJECUCIÓN PRINCIPAL
# ============================================================

def ejecutar_sincronizacion():

    hoy = date.today()

    desde = (
        hoy
        -
        timedelta(
            days=DIAS_ATRAS
        )
    )

    print(
        "=" * 100,
        flush=True
    )

    print(
        "SINCRONIZACIÓN DE SUBVENCIONES — BDNS",
        flush=True
    )

    print(
        "=" * 100,
        flush=True
    )

    print(
        f"Ventana de búsqueda: "
        f"{desde} .. {hoy}",
        flush=True
    )

    print(
        "=" * 100,
        flush=True
    )

    # ========================================================
    # BUSCAR CANDIDATAS
    # ========================================================

    candidatos = []

    pagina = 0

    detener = False

    while (
        pagina < MAX_PAGINAS_SEGURIDAD
        and not detener
    ):

        datos = obtener_pagina_convocatorias(
            pagina
        )

        contenido = datos.get(
            "content",
            []
        )

        if not contenido:

            print(
                "    No hay más resultados.",
                flush=True
            )

            break

        for item in contenido:

            fecha_recepcion = (
                convertir_fecha(
                    item.get(
                        "fechaRecepcion"
                    )
                )
            )

            if fecha_recepcion is None:

                continue

            # ------------------------------------------------
            # Como viene ordenado DESC,
            # al bajar de la fecha límite paramos.
            # ------------------------------------------------

            if fecha_recepcion < desde:

                print(
                    f"    Llegamos a "
                    f"{fecha_recepcion}, "
                    f"anterior a {desde}. "
                    f"Fin del escaneo.",
                    flush=True
                )

                detener = True

                break

            if (
                desde
                <= fecha_recepcion
                <= hoy
            ):

                candidatos.append(
                    item
                )

        pagina += 1

        time.sleep(
            PAUSA_ENTRE_PAGINAS_SEGUNDOS
        )

    print(
        f"\nConvocatorias candidatas "
        f"en la ventana: "
        f"{len(candidatos)}",
        flush=True
    )

    if not candidatos:

        print(
            "No se han encontrado "
            "convocatorias nuevas "
            "en la ventana.",
            flush=True
        )

        return

    # ========================================================
    # OBTENER DETALLES
    # ========================================================

    normalizados = []

    for item in candidatos:

        numero_convocatoria = (
            item.get(
                "numeroConvocatoria"
            )
        )

        titulo_preview = (
            item.get(
                "descripcion"
            )
            or "Sin título"
        )

        print(
            flush=True
        )

        print(
            "-" * 100,
            flush=True
        )

        print(
            "SUBVENCIÓN ENCONTRADA",
            flush=True
        )

        print(
            f"Fecha publicación: "
            f"{item.get('fechaRecepcion')}",
            flush=True
        )

        print(
            f"Nº convocatoria:   "
            f"{numero_convocatoria}",
            flush=True
        )

        print(
            f"Título:            "
            f"{titulo_preview}",
            flush=True
        )

        detalle = {}

        if numero_convocatoria:

            detalle = (
                obtener_detalle_convocatoria(
                    numero_convocatoria
                )
            )

            time.sleep(
                PAUSA_ENTRE_DETALLES_SEGUNDOS
            )

        registro = construir_registro(
            item,
            detalle
        )

        if registro[
            "codigo_unico"
        ]:

            normalizados.append(
                registro
            )

        else:

            print(
                "    ⚠️ Sin "
                "codigoBDNS/numeroConvocatoria: "
                "se descarta.",
                flush=True
            )

    # ========================================================
    # CONECTAR SUPABASE
    # ========================================================

    supabase = (
        obtener_cliente_supabase()
    )

    print(
        flush=True
    )

    print(
        "Comparando con lo ya existente "
        "en Supabase...",
        flush=True
    )

    registros_existentes = (
        obtener_registros_existentes(
            supabase,
            columnas=(
                "id",
                "codigo_unico"
            )
            + CAMPOS_COMPARABLES,
            codigos_unicos=[
                d["codigo_unico"]
                for d in normalizados
            ],
        )
    )

    # ========================================================
    # PREPARAR CAMBIOS
    # ========================================================

    lote_final = (
        preparar_lote_para_subir(
            normalizados,
            registros_existentes
        )
    )

    if not lote_final:

        print(
            "No hay convocatorias nuevas "
            "ni cambios que sincronizar.",
            flush=True
        )

        return

    # ========================================================
    # SUBIR A SUPABASE
    # ========================================================

    subidas = (
        subir_en_lotes(
            supabase,
            lote_final,
            tamano_lote=LOTE_ENVIO_SUPABASE
        )
    )

    print(
        f"\nSincronización BDNS completada: "
        f"{subidas}/{len(lote_final)} "
        f"registros subidos.",
        flush=True
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    ejecutar_sincronizacion()
