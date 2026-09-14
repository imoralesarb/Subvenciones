# -*- coding: utf-8 -*-
"""
funding_tenders_common.py
----------------------------
Utilidades específicas de la fuente "Funding & Tenders" (Portal SEDIA de
la Comisión Europea), compartidas por ingesta_funding_tenders.py y
limpiar_funding_tenders.py: los dos scripts necesitan hacer EXACTAMENTE
la misma consulta (mismos filtros de tipo y de estado) para que la
comparación entre "lo que hay en Supabase" y "lo que sigue vigente en la
API" sea correcta.

apiKey=SEDIA es una clave PÚBLICA documentada por la Comisión Europea
para este buscador -- no es una credencial privada, no hace falta darla
de alta en ningún sitio ni guardarla como secreto de GitHub Actions.

Basado en el script de prueba aportado (consulta + normalización ya
validadas contra la API real).
"""

import json
import time
from datetime import datetime

import requests


# ============================================================
# CONFIGURACIÓN
# ============================================================

FUENTE = "Funding & Tenders"

API_URL = (
    "https://api.tech.ec.europa.eu/"
    "search-api/prod/rest/search"
)

PARAMS_BASE = {
    "apiKey": "SEDIA",
    "text": "*",
    "pageSize": 50,
}

# Tipos de oportunidades de financiación (Calls for proposals y afines).
TIPOS_FINANCIACION = [
    "1",
    "2",
    "8"
]

# Estados que nos interesan:
#
#   31094501 -> Forthcoming            (próximamente)
#   31094502 -> Open for submission    (abierto)
#
# Cualquier otro estado (en evaluación, cerrado, adjudicado, cancelado...)
# se descarta -- no lo pide la búsqueda, así que la API ni lo devuelve.
ESTADOS_VALIDOS = [
    "31094501",
    "31094502"
]

QUERY = {
    "bool": {
        "must": [
            {
                "terms": {
                    "type": TIPOS_FINANCIACION
                }
            },
            {
                "terms": {
                    "status": ESTADOS_VALIDOS
                }
            }
        ]
    }
}

LANGUAGES = [
    "en"
]

PAUSA_ENTRE_PAGINAS_SEGUNDOS = 0.3
MAX_PAGINAS_SEGURIDAD = 200  # red de seguridad (~10.000 oportunidades)


# ============================================================
# CONSULTA PAGINADA A LA API
# ============================================================

def consultar_pagina(pagina):

    parametros = dict(PARAMS_BASE)
    parametros["pageNumber"] = pagina

    print(f"--> Consultando página {pagina}...", flush=True)

    respuesta = requests.post(
        API_URL,
        params=parametros,
        files={
            "query": (
                "query.json",
                json.dumps(QUERY),
                "application/json"
            ),
            "languages": (
                "languages.json",
                json.dumps(LANGUAGES),
                "application/json"
            )
        },
        timeout=60
    )

    print(f"    HTTP: {respuesta.status_code}", flush=True)
    respuesta.raise_for_status()

    return respuesta.json()


def obtener_todas_las_oportunidades():
    """
    Descarga TODAS las páginas de oportunidades Forthcoming/Open (la API
    solo devuelve las que cumplen QUERY, así que no hace falta filtrar
    nada más del lado de Python).
    """
    pagina = 1
    todos_resultados = []
    total_esperado = None

    while pagina <= MAX_PAGINAS_SEGURIDAD:

        datos = consultar_pagina(pagina)
        resultados = datos.get("results", [])

        if total_esperado is None:
            total_esperado = datos.get("totalResults", 0)
            print(f"    Total de oportunidades disponibles: {total_esperado}", flush=True)

        if not resultados:
            break

        todos_resultados.extend(resultados)

        if len(todos_resultados) >= total_esperado:
            break

        pagina += 1
        time.sleep(PAUSA_ENTRE_PAGINAS_SEGUNDOS)

    return todos_resultados


# ============================================================
# FUNCIONES AUXILIARES DE NORMALIZACIÓN
# ============================================================

def primer_valor(metadata, campo):

    valor = metadata.get(campo)

    if isinstance(valor, list):

        if valor:
            return valor[0]

        return None

    return valor


def normalizar_fecha(valor):
    """
    Siempre devuelve texto ISO "YYYY-MM-DD" (o None) -- NUNCA un objeto
    date/datetime de Python. Enviar un objeto date directamente a
    Supabase provoca "Object of type date is not JSON serializable"
    (el mismo problema que ya resolvió `fecha_a_texto` en
    ingesta_bdns.py para la fuente BDNS).
    """

    if not valor:
        return None

    # Si viene como lista
    if isinstance(valor, list):

        if not valor:
            return None

        valor = valor[0]

    valor = str(valor).strip()

    # --------------------------------------------------------
    # Intento ISO
    # --------------------------------------------------------

    try:

        # Ejemplo:
        # 2027-10-07T00:00:00.000+0000

        dt = datetime.strptime(
            valor,
            "%Y-%m-%dT%H:%M:%S.%f%z"
        )

        return dt.strftime(
            "%Y-%m-%d"
        )

    except Exception:
        pass

    # --------------------------------------------------------
    # Si simplemente empieza por YYYY-MM-DD
    # --------------------------------------------------------

    if len(valor) >= 10:

        parte = valor[:10]

        try:

            datetime.strptime(
                parte,
                "%Y-%m-%d"
            )

            return parte

        except Exception:
            pass

    return valor


def generar_codigo_unico(resultado):

    referencia = (
        resultado.get("reference")
    )

    if referencia:

        return f"FT-{referencia}"

    # Fallback por si alguna oportunidad no tiene "reference"
    identificador = (
        resultado.get("id")
    )

    if identificador:

        return f"FT-{identificador}"

    return None


# ============================================================
# NORMALIZACIÓN AL ESQUEMA DE LA TABLA `subvenciones`
# ============================================================

def normalizar_oportunidad(resultado):

    metadata = resultado.get(
        "metadata",
        {}
    )

    referencia = resultado.get("reference")

    titulo = primer_valor(metadata, "title")
    descripcion = primer_valor(metadata, "description")
    call_identifier = primer_valor(metadata, "callIdentifier")
    programa = primer_valor(metadata, "frameworkProgramme")
    periodo = primer_valor(metadata, "programmePeriod")
    estado = primer_valor(metadata, "status")

    fecha_inicio = normalizar_fecha(primer_valor(metadata, "startDate"))
    deadline = normalizar_fecha(primer_valor(metadata, "deadlineDate"))
    closing_date = normalizar_fecha(primer_valor(metadata, "closingDate"))

    lugar = primer_valor(metadata, "placesOfDeliveryOrPerformance")
    organismo = primer_valor(metadata, "caName")

    url = resultado.get("url")

    # closingDate manda si existe; si no, deadlineDate (igual que en el
    # script de prueba aportado).
    fecha_cierre = closing_date if closing_date else deadline

    return {

        "codigo_unico":
            generar_codigo_unico(resultado),

        "codigo_bdns":
            None,

        "referencia_ft":
            referencia or call_identifier,

        "titulo":
            titulo or "Sin título",

        "descripcion":
            descripcion,

        "fuente_origen":
            FUENTE,

        "organismo":
            organismo,

        "ambito":
            "Europeo",

        "ccaa":
            [],

        "lugar":
            lugar,

        "programa":
            programa,

        "periodo":
            periodo,

        "estado_ft":
            estado,

        "url_oficial":
            url,

        "url_boe":
            None,

        # No hay un equivalente exacto a "fecha de publicación" en la
        # respuesta de Funding & Tenders: usamos startDate, que es
        # también la fecha más razonable para "fecha_inicio_solicitud".
        "fecha_publicacion":
            fecha_inicio,

        "fecha_inicio_solicitud":
            fecha_inicio,

        "fecha_fin_solicitud":
            fecha_cierre,

        # No viene en los metadatos consultados por esta búsqueda.
        "presupuesto_total":
            None,

        "beneficiarios":
            None,
    }
