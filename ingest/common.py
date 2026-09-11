"""
common.py
---------
Utilidades compartidas por los scripts de ingesta de subvenciones
(ingesta_bdns.py, ingesta_boe.py). Centraliza:

  - La conexión a Supabase con la Service Role Key. Estos scripts corren
    en GitHub Actions, nunca en el navegador, así que necesitan permisos
    de escritura que la Row Level Security no concede a la clave anónima
    (ver sql/schema.sql, sección 5, y app/db.py, que sí usa la clave
    anónima porque solo lee).
  - La carga en caché del modelo de embeddings (multilingual-e5-small,
    384 dimensiones — debe coincidir con `vector(384)` en schema.sql y
    con app/config.py).
  - Un clasificador heurístico de categorías por palabras clave: ni la
    API de la BDNS ni el sumario del BOE traen de forma fiable una
    taxonomía temática homogénea, así que esto es un punto de partida
    razonable y transparente. Se puede sustituir por una llamada a un
    LLM o por un mapeo manual más fino más adelante.
  - La consulta de lo ya existente en Supabase (por codigo_unico) y el
    envío en lotes con reintentos.
"""
import os
import time
import unicodedata
from functools import lru_cache

from sentence_transformers import SentenceTransformer
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
def _obtener_encoder() -> SentenceTransformer:
    print(f"Cargando modelo de embeddings '{NOMBRE_MODELO_EMBEDDING}'...")
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
# Clasificador heurístico de categorías por palabras clave
# ------------------------------------------------------------------
# IMPORTANTE: las claves de este diccionario deben coincidir EXACTAMENTE
# con `CATEGORIAS_BASE` en app/config.py, o el filtro de categoría de la
# app dejará de encontrar coincidencias.
CATEGORIAS_PALABRAS_CLAVE = {
    "Digitalización": ["digital", "tecnolog", "ciberseguridad", "software", "kit digital"],
    "I+D+i": ["investigaci", "innovaci", "i+d", "desarrollo tecnológico", "patente"],
    "Emprendimiento": ["emprend", "startup", "autonomo", "creacion de empresa"],
    "Internacionalización": ["internacionaliza", "exportaci", "comercio exterior"],
    "Empleo y formación": ["empleo", "formacion", "contrataci", "fp dual", "cualificaci"],
    "Igualdad y conciliación": ["igualdad", "conciliaci", "violencia de genero", "mujer"],
    "Cultura": ["cultura", "cultural", "artes escenicas", "patrimonio", "audiovisual", "musica"],
    "Deporte": ["deport", "federacion deportiva", "club deportivo"],
    "Medio ambiente y sostenibilidad": [
        "medio ambiente", "sostenib", "climatic", "economia circular", "residuos", "biodiversidad",
    ],
    "Energía": ["energ", "renovable", "fotovoltaic", "eficiencia energetica"],
    "Turismo": ["turis", "hostel"],
    "Comercio": ["comercio", "comercio minorista", "mercado municipal"],
    "Industria": ["industri", "manufactur", "fabricaci"],
    "Agricultura y pesca": ["agricult", "ganader", "pesca", "agroalimentari", "rural"],
    "Vivienda": ["vivienda", "rehabilitaci", "alquiler"],
    "Educación": ["educaci", "escolar", "universitari", "beca"],
    "Servicios sociales": [
        "servicios sociales", "dependencia", "discapacidad", "exclusion social", "mayores",
    ],
    "Movilidad y transporte": ["movilidad", "transporte", "vehiculo electrico"],
    "Juventud": ["juventud", "jovenes"],
}


def _normalizar(texto: str) -> str:
    texto = texto.lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def inferir_categorias(titulo: str, descripcion: str = "") -> list:
    """
    Heurística simple por coincidencia de palabras clave. No sustituye una
    taxonomía oficial, pero da filtros de categoría útiles desde el primer
    día. Si no reconoce ninguna, devuelve una lista vacía — la convocatoria
    sigue siendo localizable por búsqueda semántica o de texto libre.
    """
    texto_normalizado = _normalizar(f"{titulo} {descripcion}")
    return [
        categoria
        for categoria, palabras in CATEGORIAS_PALABRAS_CLAVE.items()
        if any(_normalizar(palabra) in texto_normalizado for palabra in palabras)
    ]


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
                print(f"Progreso: {subidos}/{total} subvenciones sincronizadas...")
                break
            except Exception as error:
                print(f"⚠️ Intento {intento}/{max_intentos} fallido para el lote {numero_lote}: {error}")
                if intento < max_intentos:
                    time.sleep(2 * intento)
                else:
                    print(f"❌ Error definitivo subiendo el lote {numero_lote}.")

    return subidos
