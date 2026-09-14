# -*- coding: utf-8 -*-
"""
ingesta_funding_tenders.py
----------------------------
Sincroniza oportunidades del Portal de Financiación y Licitaciones de la
UE (Funding & Tenders / SEDIA) directamente contra la tabla
`subvenciones` de Supabase.

Solo se piden (y por tanto solo se sincronizan) oportunidades en estado:

  - 31094501 -> Forthcoming         (próximamente)
  - 31094502 -> Open for submission (abierto)

El filtro de tipo/estado vive en ingest/funding_tenders_common.py y lo
comparte con limpiar_funding_tenders.py, para que ambos scripts miren
siempre exactamente la misma definición de "sigue vigente".

Variables de entorno requeridas: SUPABASE_URL, SUPABASE_SERVICE_KEY.
Ejecución local:      python ingesta_funding_tenders.py
Ejecución programada: ver .github/workflows/sincronizar_funding_tenders.yml
"""

from common import (
    generar_embedding,
    obtener_cliente_supabase,
    obtener_registros_existentes,
    subir_en_lotes,
)

from funding_tenders_common import (
    obtener_todas_las_oportunidades,
    normalizar_oportunidad,
)


# ============================================================
# CONFIGURACIÓN
# ============================================================

LOTE_ENVIO_SUPABASE = 25

# Campos que, si cambian respecto a lo ya guardado, marcan la
# oportunidad como "actualizada" (p. ej. Forthcoming -> Open) y
# disparan un recálculo de su embedding.
CAMPOS_COMPARABLES = (
    "titulo",
    "descripcion",
    "organismo",
    "estado_ft",
    "programa",
    "periodo",
    "lugar",
    "fecha_inicio_solicitud",
    "fecha_fin_solicitud",
)


# ============================================================
# TEXTO PARA EL EMBEDDING
# ============================================================

def construir_texto_completo(datos):

    partes = [
        f"Título: {datos['titulo']}"
    ]

    if datos.get("descripcion"):
        partes.append(datos["descripcion"])

    if datos.get("organismo"):
        partes.append(f"Organismo: {datos['organismo']}")

    partes.append("Ámbito: Europeo")

    if datos.get("programa"):
        partes.append(f"Programa: {datos['programa']}")

    if datos.get("periodo"):
        partes.append(f"Periodo: {datos['periodo']}")

    if datos.get("lugar"):
        partes.append(f"Lugar: {datos['lugar']}")

    if datos.get("fecha_inicio_solicitud"):
        partes.append(f"Inicio: {datos['fecha_inicio_solicitud']}")

    if datos.get("fecha_fin_solicitud"):
        partes.append(f"Cierre: {datos['fecha_fin_solicitud']}")

    return "\n".join(partes)


# ============================================================
# DEDUPLICACIÓN DENTRO DEL MISMO LOTE
# ============================================================

def deduplicar_por_codigo(normalizados):
    """
    Un mismo codigo_unico no puede aparecer dos veces dentro de un mismo
    upsert (Postgres/PostgREST lo rechaza: "ON CONFLICT DO UPDATE command
    cannot affect row a second time"). No debería ocurrir con esta API,
    pero se deja esta red de seguridad tal y como la traía el script de
    prueba aportado (ver su función `deduplicar`).
    """
    vistos = {}

    for datos in normalizados:

        if not datos["codigo_unico"]:
            continue

        vistos[datos["codigo_unico"]] = datos

    return list(vistos.values())


# ============================================================
# DECIDIR QUÉ SUBIR (NUEVAS / CAMBIADAS)
# ============================================================

def preparar_lote_para_subir(normalizados, registros_existentes):

    a_subir = []

    for datos in normalizados:

        existente = registros_existentes.get(datos["codigo_unico"])
        texto_completo = construir_texto_completo(datos)

        if existente is None:

            datos["texto_completo"] = texto_completo
            datos["embedding"] = generar_embedding(texto_completo)
            datos["es_novedad"] = True
            datos["es_actualizada"] = False

            a_subir.append(datos)
            continue

        ha_cambiado = any(
            str(existente.get(campo)) != str(datos.get(campo))
            for campo in CAMPOS_COMPARABLES
        )

        if not ha_cambiado:
            continue  # nada que actualizar: nos ahorramos una escritura y un embedding

        datos["texto_completo"] = texto_completo
        datos["embedding"] = generar_embedding(texto_completo)
        datos["es_novedad"] = False
        datos["es_actualizada"] = True

        a_subir.append(datos)

    return a_subir


# ============================================================
# EJECUCIÓN PRINCIPAL
# ============================================================

def ejecutar_sincronizacion():

    print("=" * 100, flush=True)
    print("SINCRONIZACIÓN DE SUBVENCIONES — FUNDING & TENDERS (UE)", flush=True)
    print("=" * 100, flush=True)

    resultados = obtener_todas_las_oportunidades()
    print(f"\nOportunidades Forthcoming/Open descargadas: {len(resultados)}", flush=True)

    if not resultados:
        print("No hay oportunidades que sincronizar.", flush=True)
        return

    normalizados = [normalizar_oportunidad(r) for r in resultados]
    normalizados = deduplicar_por_codigo(normalizados)

    supabase = obtener_cliente_supabase()

    print("\nComparando con lo ya existente en Supabase...", flush=True)
    registros_existentes = obtener_registros_existentes(
        supabase,
        columnas=("id", "codigo_unico") + CAMPOS_COMPARABLES,
        codigos_unicos=[d["codigo_unico"] for d in normalizados],
    )

    lote_final = preparar_lote_para_subir(normalizados, registros_existentes)

    if not lote_final:
        print("No hay oportunidades nuevas ni cambios que sincronizar.", flush=True)
        return

    subidas = subir_en_lotes(supabase, lote_final, tamano_lote=LOTE_ENVIO_SUPABASE)

    print(
        f"\nSincronización Funding & Tenders completada: "
        f"{subidas}/{len(lote_final)} registros subidos.",
        flush=True,
    )


if __name__ == "__main__":
    ejecutar_sincronizacion()
