"""
search.py
---------
Lógica de búsqueda de subvenciones. Combina búsqueda semántica (embeddings
+ pgvector) con filtros estructurados (categoría, ámbito, CCAA, importe,
plazo vigente, fuente, novedades).

Todo el filtrado ocurre dentro de Supabase/Postgres, a través de la
función `buscar_subvenciones` (ver sql/schema.sql). Esto es deliberado:
resolver los filtros en la base de datos, en vez de traer todas las filas
a un DataFrame y filtrar con pandas, es lo que mantiene la app rápida
cuando la tabla crece a decenas de miles de convocatorias.
"""
from typing import Optional

import pandas as pd
from sentence_transformers import SentenceTransformer
from supabase import Client


def buscar_subvenciones(
    supabase: Client,
    encoder: SentenceTransformer,
    texto: str = "",
    categorias: Optional[list] = None,
    ambito: Optional[str] = None,
    ccaa: Optional[list] = None,
    fuente: Optional[str] = None,
    importe_min: Optional[float] = None,
    importe_max: Optional[float] = None,
    solo_vigentes: bool = True,
    solo_novedades: bool = False,
    match_threshold: float = 0.2,
    limite: int = 300,
) -> pd.DataFrame:
    """
    Ejecuta la búsqueda contra la RPC `buscar_subvenciones` de Supabase.

    Si `texto` no está vacío, se calcula su embedding con el prefijo
    'query: ' (obligatorio con los modelos de la familia E5 para obtener
    buena calidad de recuperación) y los resultados se ordenan por
    similitud semántica. Si está vacío, se listan/filtran las
    convocatorias ordenadas por fecha de publicación.
    """
    query_embedding = None
    texto = (texto or "").strip()
    if texto:
        query_embedding = encoder.encode(f"query: {texto}").tolist()

    parametros = {
        "query_embedding": query_embedding,
        "match_threshold": match_threshold,
        "match_count": limite,
        "filtro_categorias": categorias or None,
        "filtro_ambito": ambito or None,
        "filtro_ccaa": ccaa or None,
        "filtro_fuente": fuente or None,
        "filtro_importe_min": importe_min or None,
        "filtro_importe_max": importe_max or None,
        "solo_vigentes": solo_vigentes,
        "solo_novedades": solo_novedades,
    }

    respuesta = supabase.rpc("buscar_subvenciones", parametros).execute()
    return pd.DataFrame(respuesta.data or [])


def _formatear_importe(valor) -> str:
    if pd.isna(valor):
        return "No especificado"
    texto = f"{valor:,.2f}"
    # es-ES: punto de miles, coma decimal (al revés que en-US)
    return texto.replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def _unir_lista(valor) -> str:
    if isinstance(valor, list) and valor:
        return ", ".join(valor)
    return ""


def formatear_para_tabla(df: pd.DataFrame) -> pd.DataFrame:
    """Prepara columnas legibles para mostrar en `st.dataframe`."""
    if df.empty:
        return df

    return pd.DataFrame({
        "Relevancia (%)": (df["similarity"] * 100).round(1) if "similarity" in df else 100.0,
        "Título": df["titulo"],
        "Organismo": df["organismo"].fillna("No especificado"),
        "Ámbito": df["ambito"].fillna("No especificado"),
        "CCAA": df["ccaa"].apply(_unir_lista).replace("", "Nacional / No aplica"),
        "Categorías": df["categorias"].apply(_unir_lista).replace("", "Sin clasificar"),
        "Importe": df["presupuesto_total"].apply(_formatear_importe),
        "Cierre de plazo": df["fecha_fin_solicitud"].fillna("No especificada"),
        "Fuente": df["fuente_origen"],
        "Novedad": df["es_novedad"],
        "Enlace": df["url_oficial"],
    })
