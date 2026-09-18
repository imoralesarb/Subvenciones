"""
search.py
---------
Funciones de acceso a datos para el buscador de subvenciones.

Sigue el mismo patrón que `app.py` del proyecto de Licitaciones de
referencia: la función RPC `buscar_subvenciones` de Supabase solo hace
búsqueda semántica (embeddings); el resto de filtros estructurados
(fuente, ámbito, CCAA, importe, beneficiarios, fechas) se aplican con
pandas en app.py, tanto sobre el resultado de la búsqueda semántica como
sobre un listado plano cuando no hay texto de búsqueda.
"""
import pandas as pd
import streamlit as st
from sentence_transformers import SentenceTransformer
from supabase import Client

COLUMNAS_LISTADO = (
    "codigo_unico, titulo, organismo, fuente_origen, ambito, ccaa, "
    "beneficiarios, titulo_bases_reguladoras, tipo_convocatoria, "
    "presupuesto_total, url_oficial, fecha_publicacion, "
    "fecha_fin_solicitud, es_novedad, es_actualizada"
)

TAMANO_LOTE_LISTADO = 1000


def buscar_semantica(
    supabase: Client,
    encoder: SentenceTransformer,
    texto: str,
    match_threshold: float = 0.2,
    match_count: int = 999999,
) -> list:
    """Búsqueda por similitud semántica vía la función RPC `buscar_subvenciones`."""
    query_con_prefijo = f"query: {texto.strip()}"
    vector_query = encoder.encode(query_con_prefijo).tolist()

    respuesta = supabase.rpc(
        "buscar_subvenciones",
        {
            "query_embedding": vector_query,
            "match_threshold": match_threshold,
            "match_count": match_count,
        },
    ).execute()
    return respuesta.data or []


def _listar_paginado(supabase: Client, solo_novedades: bool) -> list:
    """Trae toda la tabla (o solo novedades/actualizaciones) en lotes de 1000 filas."""
    resultados = []
    inicio = 0

    while True:
        consulta = supabase.table("subvenciones").select(COLUMNAS_LISTADO)
        if solo_novedades:
            consulta = consulta.or_("es_novedad.eq.true,es_actualizada.eq.true")
        else:
            consulta = consulta.order("fecha_publicacion", desc=True)

        respuesta = consulta.range(inicio, inicio + TAMANO_LOTE_LISTADO - 1).execute()
        filas = respuesta.data

        if not filas:
            break
        resultados.extend(filas)
        if len(filas) < TAMANO_LOTE_LISTADO:
            break
        inicio += TAMANO_LOTE_LISTADO

    return resultados


def listar_todas(supabase: Client) -> list:
    return _listar_paginado(supabase, solo_novedades=False)


def listar_novedades(supabase: Client) -> list:
    return _listar_paginado(supabase, solo_novedades=True)


@st.cache_data(ttl=3600, show_spinner=False)
def obtener_opciones_filtro(_supabase: Client) -> tuple:
    """
    Calcula las opciones de los desplegables de Ámbito, CCAA, Beneficiarios
    (extrayendo opciones del texto separado por comas/punto y coma) y
    Tipo de convocatoria a partir de los valores realmente presentes en la tabla.
    """
    respuesta = _supabase.table("subvenciones").select(
        "ambito, ccaa, beneficiarios, tipo_convocatoria"
    ).execute()
    filas = respuesta.data or []

    ambitos = sorted({f["ambito"] for f in filas if f.get("ambito")})
    ccaa = sorted({v for f in filas for v in (f.get("ccaa") or [])})
    
    # Extracción inteligente de opciones del campo beneficiarios separadas por comas o punto y coma
    beneficiarios_set = set()
    for f in filas:
        b_val = f.get("beneficiarios")
        if b_val and isinstance(b_val, str):
            partes = b_val.replace(";", ",").split(",")
            for p in partes:
                limpio = p.strip()
                if limpio:
                    beneficiarios_set.add(limpio)
    beneficiarios_opciones = sorted(list(beneficiarios_set))

    tipos_convocatoria = sorted({v for f in filas for v in (f.get("tipo_convocatoria") or [])})

    return ambitos, ccaa, beneficiarios_opciones, tipos_convocatoria
