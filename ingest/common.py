"""
common.py
---------
Utilidades compartidas por TODOS los scripts de ingesta/mantenimiento de
subvenciones (ingesta_bdns.py, ingesta_boe.py, ingesta_funding_tenders.py,
limpiar_funding_tenders.py). Centraliza:

  - La conexión a Supabase con la Service Role Key. Estos scripts corren
    en GitHub Actions, nunca en el navegador, así que necesitan permisos
    de escritura/borrado que la Row Level Security no concede a la clave
    anónima (ver sql/schema.sql, sección 5, y app/db.py, que sí usa la
    clave anónima porque solo lee).
  - La carga en caché del modelo de embeddings (multilingual-e5-small,
    384 dimensiones — debe coincidir con `vector(384)` en schema.sql).
  - La consulta de lo ya existente en Supabase (por codigo_unico), el
    envío en lotes con reintentos, y el borrado en lotes por id (usado
    por limpiar_funding_tenders.py).

Nota: la versión anterior de este fichero incluía un clasificador
heurístico de categorías (`inferir_categorias`) que ya no se usa en
ningún script de ingesta -- el campo `categorias` se eliminó de la tabla
`subvenciones` (ver sql/schema.sql) y `app/config.py` ya no tiene la
constante `CATEGORIAS_BASE` con la que ese código tenía que coincidir.
Se ha retirado por ser código muerto que podía llevar a confusión; el
histórico sigue disponible en git si hace falta recuperarlo.
"""
from __future__ import annotations

import os
import time
from functools import lru_cache

from supabase import Client, create_client

# ------------------------------------------------------------------
# Conexión a Supabase (Service Role Key: bypassa la RLS, uso solo backend)
# ------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")


def obtener_cliente_supabase() -> Client:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError(
            "Faltan las variables de entorno SUPABASE_URL o SUPABASE_SERVICE_KEY."
        )
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ------------------------------------------------------------------
# Modelo de embeddings (el mismo que usa la app de Streamlit)
# ------------------------------------------------------------------
NOMBRE_MODELO_EMBEDDING = "intfloat/multilingual-e5-small"  # 384 dimensiones


@lru_cache(maxsize=1)
def _obtener_encoder() -> "SentenceTransformer":
    # Importación perezosa (no a nivel de módulo): así, scripts que usan
    # common.py pero nunca generan embeddings (p. ej.
    # limpiar_funding_tenders.py, que solo lee/borra) no necesitan tener
    # instalada esta dependencia pesada (arrastra PyTorch) solo para
    # conectarse a Supabase.
    from sentence_transformers import SentenceTransformer

    print(f"Cargando modelo de embeddings '{NOMBRE_MODELO_EMBEDDING}'...", flush=True)
    return SentenceTransformer(NOMBRE_MODELO_EMBEDDING, device="cpu")


def generar_embedding(texto_completo: str) -> list:
    """
    El prefijo 'passage: ' es obligatorio con los modelos E5 (así fueron
    entrenados; sin él la calidad de la búsqueda semántica empeora
    notablemente). La app de Streamlit usa el prefijo 'query: ' al
    codificar las búsquedas del usuario — ver app/search.py.
    """
    encoder = _obtener_encoder()
    return encoder.encode(f"passage: {texto_completo}").tolist()


# ------------------------------------------------------------------
# Consulta de lo ya existente en Supabase (deduplicación por lote)
# ------------------------------------------------------------------
def obtener_registros_existentes(supabase: Client, columnas: tuple, codigos_unicos: list) -> dict:
    """
    Consulta únicamente los registros cuyo `codigo_unico` coincide con el
    lote que se acaba de descargar de la fuente (BDNS o BOE), en vez de
    traer toda la tabla `subvenciones` a memoria — así el script sigue
    siendo rápido y ligero aunque el histórico crezca a decenas de miles
    de filas. Devuelve un diccionario {codigo_unico: fila}.
    """
    registros: dict = {}
    if not codigos_unicos:
        return registros

    tamano_lote_in = 300  # margen prudente para no exceder límites de la URL/PostgREST
    codigos = list(dict.fromkeys(codigos_unicos))  # dedupe conservando el orden

    for i in range(0, len(codigos), tamano_lote_in):
        trozo = codigos[i : i + tamano_lote_in]
        respuesta = (
            supabase.table("subvenciones")
            .select(", ".join(columnas))
            .in_("codigo_unico", trozo)
            .execute()
        )
        registros.update({fila["codigo_unico"]: fila for fila in respuesta.data})

    return registros


# ------------------------------------------------------------------
# Envío a Supabase en lotes, con reintentos
# ------------------------------------------------------------------
def subir_en_lotes(supabase: Client, registros: list, tamano_lote: int = 25, max_intentos: int = 3) -> int:
    """
    Hace upsert por lotes contra la tabla `subvenciones`, usando
    `codigo_unico` como clave de conflicto (ver sql/schema.sql). Reintenta
    con backoff simple ante errores transitorios de red/Supabase.
    """
    total = len(registros)
    subidos = 0

    for i in range(0, total, tamano_lote):
        lote = registros[i : i + tamano_lote]
        numero_lote = i // tamano_lote + 1

        for intento in range(1, max_intentos + 1):
            try:
                supabase.table("subvenciones").upsert(lote, on_conflict="codigo_unico").execute()
                subidos += len(lote)
                print(f"Progreso: {subidos}/{total} subvenciones sincronizadas...", flush=True)
                break
            except Exception as error:
                print(f"⚠️ Intento {intento}/{max_intentos} fallido para el lote {numero_lote}: {error}", flush=True)
                if intento < max_intentos:
                    time.sleep(2 * intento)
                else:
                    print(f"❌ Error definitivo subiendo el lote {numero_lote}.", flush=True)

    return subidos


# ------------------------------------------------------------------
# Borrado en lotes por id (usado por limpiar_funding_tenders.py, y
# reutilizable por futuros scripts de limpieza de otras fuentes)
# ------------------------------------------------------------------
def eliminar_por_ids(supabase: Client, ids: list, tamano_lote: int = 200) -> int:
    """
    Borra de `subvenciones` las filas cuyo `id` está en la lista dada,
    en lotes (para no construir una URL/petición enorme de golpe).
    """
    ids = list(ids)
    eliminados = 0

    for i in range(0, len(ids), tamano_lote):
        trozo = ids[i : i + tamano_lote]
        try:
            supabase.table("subvenciones").delete().in_("id", trozo).execute()
            eliminados += len(trozo)
            print(f"🗑️ Eliminados {eliminados}/{len(ids)}...", flush=True)
        except Exception as error:
            print(f"⚠️ Error eliminando un lote de {len(trozo)} registros: {error}", flush=True)

    return eliminados
