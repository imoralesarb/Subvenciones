"""
ingesta_bdns.py
----------------
Sincroniza convocatorias de la Base de Datos Nacional de Subvenciones
(BDNS / SNPSAP) — fuente PRIMARIA para España: agrega subvenciones y
ayudas del Estado, las CCAA y entidades locales (desde 2016). Custodiada
por la IGAE (art. 20 LGS, RD 130/2019).

Documentación:
  - Catálogo:         https://datos.gob.es (Base de Datos Nacional de Subvenciones)
  - Portal público:    https://www.infosubvenciones.es
  - Swagger de la API: expuesto desde el propio portal de infosubvenciones.es

Diferencia con el proyecto de referencia (Licitaciones-main): allí cada
`sincronizar_<fuente>.py` escribe directamente en Supabase porque cada
portal autonómico es una fuente distinta y sin identificador único fiable
(de ahí que usen título+órgano como clave de deduplicación). La BDNS, en
cambio, SÍ da un identificador único real (`numeroConvocatoria`), así que
aquí la deduplicación es más simple y más fiable: `codigo_unico =
f"BDNS-{numero}"`.

Sobre la persistencia de un cursor entre ejecuciones: este script NO
guarda un cursor de paginación entre ejecuciones programadas, a propósito.
En GitHub Actions cada `run` parte de un checkout limpio, así que un
fichero de estado local no sobreviviría de una ejecución a la siguiente
salvo que lo comitees de vuelta al repo o uses `actions/cache`. Como la
BDNS siempre devuelve las convocatorias más recientes primero y el
`upsert` por `codigo_unico` es idempotente, basta con re-escanear cada
día las últimas ~2000 convocatorias (ver MAX_PAGINAS_POR_EJECUCION) para
que la sincronización sea correcta sin necesitar estado persistente. Si
tu volumen de convocatorias nuevas al día llegase a superar ese margen,
sube MAX_PAGINAS_POR_EJECUCION o añade paralelismo por rango de fechas.

Variables de entorno requeridas: SUPABASE_URL, SUPABASE_SERVICE_KEY.
Ejecución local:      python ingesta_bdns.py
Ejecución programada: ver .github/workflows/sincronizar_bdns.yml
"""

import time

import requests

from common import (
    generar_embedding,
    inferir_categorias,
    obtener_cliente_supabase,
    obtener_registros_existentes,
    subir_en_lotes,
)

BDNS_BASE = "https://www.infosubvenciones.es/bdnstrans/api"
BDNS_BUSQUEDA = f"{BDNS_BASE}/convocatorias/busqueda"

TAMANO_PAGINA = 50
PAUSA_ENTRE_PAGINAS_SEGUNDOS = 1.0  # ritmo de consulta responsable con la API pública
MAX_PAGINAS_POR_EJECUCION = 40      # límite de seguridad (~2000 convocatorias/ejecución)
LOTE_ENVIO_SUPABASE = 25

FUENTE = "BDNS"

# Campos que, si cambian respecto a lo ya guardado, marcan la convocatoria
# como "actualizada" y disparan un recálculo de su embedding.
CAMPOS_COMPARABLES = (
    "titulo", "descripcion", "organismo", "ambito",
    "presupuesto_total", "fecha_inicio_solicitud", "fecha_fin_solicitud",
)


# ------------------------------------------------------------------
# Acceso a la API de la BDNS
# ------------------------------------------------------------------
def obtener_pagina_convocatorias(pagina: int, tamano: int = TAMANO_PAGINA) -> dict:
    """Consulta la API pública de búsqueda de convocatorias de la BDNS (paginada)."""
    parametros = {
        "page": pagina,
        "pageSize": tamano,
        "order": "numeroConvocatoria",
        "direccion": "desc",
        "vpd": "GE",  # Vista pública por defecto (catálogo general)
    }
    respuesta = requests.get(BDNS_BUSQUEDA, params=parametros, timeout=30)
    respuesta.raise_for_status()
    return respuesta.json()


# ------------------------------------------------------------------
# Normalización al esquema de la tabla `subvenciones`
# ------------------------------------------------------------------
def normalizar_convocatoria(item_bdns: dict) -> dict:
    """
    Traduce el formato de la BDNS al esquema de `subvenciones` (ver
    sql/schema.sql). Ajusta los nombres de campo exactos según la
    respuesta vigente del Swagger de la BDNS si el organismo cambia el
    contrato de su API — esta función es, a propósito, el único sitio
    donde haría falta tocar algo si eso ocurre.
    """
    numero = item_bdns.get("numeroConvocatoria")
    ambito_raw = (item_bdns.get("ambito") or "").upper()
    titulo = (item_bdns.get("titulo") or item_bdns.get("descripcion") or "").strip()
    descripcion = item_bdns.get("descripcion") or ""

    ccaa = [
        c.get("descripcion")
        for c in (item_bdns.get("comunidadesAutonomas") or [])
        if c.get("descripcion")
    ]

    # La heurística por palabras clave es la base; si la respuesta de la
    # BDNS trae además una clasificación propia (sectores/instrumentos/
    # finalidad), se añade como categorías adicionales. Estos campos no
    # siempre vienen rellenos, así que todo el acceso es defensivo (.get).
    categorias = set(inferir_categorias(titulo, descripcion))
    for campo in ("sectores", "instrumentos"):
        for elemento in item_bdns.get(campo) or []:
            nombre = (elemento or {}).get("descripcion")
            if nombre:
                categorias.add(nombre.strip())
    finalidad = (item_bdns.get("finalidad") or {}).get("descripcion")
    if finalidad:
        categorias.add(finalidad.strip())

    return {
        "codigo_unico": f"BDNS-{numero}",
        "codigo_bdns": str(numero) if numero else None,
        "titulo": titulo,
        "descripcion": descripcion,
        "fuente_origen": FUENTE,
        "url_oficial": f"https://www.infosubvenciones.es/bdnstrans/GE/es/convocatoria/{numero}",
        "organismo": (item_bdns.get("organo") or {}).get("nombre"),
        "ambito": "Nacional" if ambito_raw == "N" else "Autonómico",
        "ccaa": ccaa,
        "categorias": sorted(categorias),
        "fecha_publicacion": item_bdns.get("fechaRecepcion"),
        "fecha_inicio_solicitud": item_bdns.get("fechaInicioSolicitud"),
        "fecha_fin_solicitud": item_bdns.get("fechaFinSolicitud"),
        "presupuesto_total": item_bdns.get("presupuestoTotal"),
        # Estos campos rara vez vienen estructurados en la fuente y
        # requerirían una capa adicional de extracción sobre las bases
        # reguladoras (PDF). Se dejan a None (= "sin restricción conocida")
        # salvo que se complete aparte.
        "beneficiarios": None,
        "empleados_min": None,
        "empleados_max": None,
        "antiguedad_min_anios": None,
        "antiguedad_max_anios": None,
    }


# ------------------------------------------------------------------
# Decidir qué subir (nuevas / cambiadas) sin recalcular embeddings de más
# ------------------------------------------------------------------
def preparar_lote_para_subir(convocatorias_normalizadas: list, registros_existentes: dict) -> list:
    """
    Para cada convocatoria, decide si es nueva, si ha cambiado, o si no
    hace falta tocarla — y solo genera el embedding cuando realmente hace
    falta, para no malgastar CPU en cada ejecución programada.
    """
    a_subir = []

    for datos in convocatorias_normalizadas:
        existente = registros_existentes.get(datos["codigo_unico"])

        texto_completo = (
            f"Título: {datos['titulo']}. Organismo: {datos['organismo'] or 'No especificado'}. "
            f"Ámbito: {datos['ambito']}. "
            f"CCAA: {', '.join(datos['ccaa']) or 'Nacional / todo el territorio'}. "
            f"Categorías: {', '.join(datos['categorias']) or 'Sin clasificar'}. "
            f"Presupuesto: {datos['presupuesto_total'] or 'No especificado'} EUR."
        )

        if existente is None:
            datos["texto_completo"] = texto_completo
            datos["embedding"] = generar_embedding(texto_completo)
            datos["es_novedad"] = True
            datos["es_actualizada"] = False
            a_subir.append(datos)
            continue

        ha_cambiado = any(
            str(existente.get(campo)) != str(datos.get(campo)) for campo in CAMPOS_COMPARABLES
        )
        if not ha_cambiado:
            continue  # nada que actualizar: nos ahorramos una escritura y un embedding

        datos["texto_completo"] = texto_completo
        datos["embedding"] = generar_embedding(texto_completo)
        datos["es_novedad"] = False
        datos["es_actualizada"] = True
        a_subir.append(datos)

    return a_subir


# ------------------------------------------------------------------
# Ejecución principal
# ------------------------------------------------------------------
def ejecutar_sincronizacion():
    supabase = obtener_cliente_supabase()

    pagina = 0
    convocatorias_normalizadas = []

    print(f"Iniciando sincronización BDNS (hasta {MAX_PAGINAS_POR_EJECUCION} páginas)...")

    while pagina < MAX_PAGINAS_POR_EJECUCION:
        datos = obtener_pagina_convocatorias(pagina)
        contenido = datos.get("content", [])

        if not contenido:
            break  # no hay más páginas con resultados

        convocatorias_normalizadas.extend(normalizar_convocatoria(item) for item in contenido)

        pagina += 1
        time.sleep(PAUSA_ENTRE_PAGINAS_SEGUNDOS)  # ritmo de consulta responsable

    if not convocatorias_normalizadas:
        print("No se han encontrado convocatorias.")
        return

    print("Comparando con lo ya existente en Supabase...")
    registros_existentes = obtener_registros_existentes(
        supabase,
        columnas=("id", "codigo_unico") + CAMPOS_COMPARABLES,
        codigos_unicos=[d["codigo_unico"] for d in convocatorias_normalizadas],
    )

    lote_final = preparar_lote_para_subir(convocatorias_normalizadas, registros_existentes)

    if not lote_final:
        print("No hay convocatorias nuevas ni cambios que sincronizar.")
        return

    subidas = subir_en_lotes(supabase, lote_final, tamano_lote=LOTE_ENVIO_SUPABASE)
    print(f"Sincronización BDNS completada: {subidas}/{len(lote_final)} registros subidos.")


if __name__ == "__main__":
    ejecutar_sincronizacion()
