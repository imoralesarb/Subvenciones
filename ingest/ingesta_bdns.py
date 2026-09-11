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

DIAS_ATRAS = 1

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
