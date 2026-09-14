# -*- coding: utf-8 -*-
"""
limpiar_funding_tenders.py
------------------------------
Elimina de la tabla `subvenciones` las oportunidades de Funding & Tenders
cuyo estado YA NO es "Forthcoming" (31094501) ni "Open for submission"
(31094502) -- es decir, las que han pasado a evaluación, han sido
adjudicadas, canceladas, retiradas, etc.

Estrategia: en vez de consultar el estado de cada fila una por una (un
número de peticiones proporcional al tamaño de la tabla), se reconsulta
la MISMA búsqueda que usa ingesta_funding_tenders.py (que ya solo pide
Forthcoming/Open -- ver ingest/funding_tenders_common.py) para obtener el
conjunto completo de codigo_unico que siguen vigentes AHORA MISMO, y se
borra de Supabase cualquier fila de Funding & Tenders cuyo codigo_unico
ya no esté en ese conjunto.

Relación con ingest/../.github/workflows/eliminar_caducadas.yml: ese
workflow borra, sin importar la fuente, cualquier fila cuya
fecha_fin_solicitud ya haya pasado. Este script es complementario: cubre
los casos en los que el ESTADO cambia ANTES de llegar a esa fecha (cierre
anticipado, cancelación...), que el filtro por fecha no detectaría.

Pensado para ejecutarse cada dos semanas -- ver
.github/workflows/limpiar_funding_tenders.yml.

Variables de entorno requeridas: SUPABASE_URL, SUPABASE_SERVICE_KEY.
Ejecución local: python limpiar_funding_tenders.py
"""

from common import (
    obtener_cliente_supabase,
    eliminar_por_ids,
)

from funding_tenders_common import (
    FUENTE,
    obtener_todas_las_oportunidades,
    normalizar_oportunidad,
)


# ============================================================
# CONFIGURACIÓN
# ============================================================

TAMANO_LOTE_LISTADO = 1000


# ============================================================
# CONSULTA DE LO YA GUARDADO EN SUPABASE
# ============================================================

def obtener_codigos_ft_en_supabase(supabase):
    """
    Todos los {codigo_unico: id} actualmente en Supabase con
    fuente_origen = 'Funding & Tenders' (paginado, por si la tabla crece).
    """
    codigos = {}
    inicio = 0

    while True:

        respuesta = (
            supabase.table("subvenciones")
            .select("id, codigo_unico")
            .eq("fuente_origen", FUENTE)
            .range(inicio, inicio + TAMANO_LOTE_LISTADO - 1)
            .execute()
        )

        filas = respuesta.data

        if not filas:
            break

        codigos.update({fila["codigo_unico"]: fila["id"] for fila in filas})

        if len(filas) < TAMANO_LOTE_LISTADO:
            break

        inicio += TAMANO_LOTE_LISTADO

    return codigos


# ============================================================
# EJECUCIÓN PRINCIPAL
# ============================================================

def ejecutar_limpieza():

    print("=" * 100, flush=True)
    print("LIMPIEZA DE FUNDING & TENDERS — CONVOCATORIAS YA NO VIGENTES", flush=True)
    print("=" * 100, flush=True)

    print("\nConsultando qué sigue Forthcoming/Open ahora mismo en la API...", flush=True)
    resultados_vigentes = obtener_todas_las_oportunidades()

    codigos_vigentes = {
        normalizar_oportunidad(r)["codigo_unico"]
        for r in resultados_vigentes
    }
    codigos_vigentes.discard(None)

    print(f"Oportunidades vigentes ahora mismo: {len(codigos_vigentes)}", flush=True)

    supabase = obtener_cliente_supabase()

    print("\nConsultando lo que hay en Supabase con fuente 'Funding & Tenders'...", flush=True)
    codigos_en_bd = obtener_codigos_ft_en_supabase(supabase)
    print(f"Registros en Supabase: {len(codigos_en_bd)}", flush=True)

    ids_a_borrar = [
        id_registro
        for codigo, id_registro in codigos_en_bd.items()
        if codigo not in codigos_vigentes
    ]

    if not ids_a_borrar:
        print("\n✅ No hay convocatorias que eliminar: todo lo almacenado sigue vigente.", flush=True)
        return

    print(f"\nConvocatorias a eliminar (ya no Forthcoming/Open): {len(ids_a_borrar)}", flush=True)

    eliminados = eliminar_por_ids(supabase, ids_a_borrar)

    print(f"\n✅ Limpieza completada: {eliminados} convocatorias eliminadas.", flush=True)


if __name__ == "__main__":
    ejecutar_limpieza()
