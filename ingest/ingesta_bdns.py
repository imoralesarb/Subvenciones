"""
ingesta_bdns.py
----------------
Sincroniza convocatorias de la Base de Datos Nacional de Subvenciones
(BDNS / SNPSAP) — fuente PRIMARIA para España — directamente contra la
tabla `subvenciones` de Supabase (ver sql/schema.sql).

Estrategia (validada manualmente contra la API real de la BDNS):
  1. Recorre el buscador de convocatorias (`/convocatorias/busqueda`)
     ordenado por `fechaRecepcion` DESC (las más recientes primero).
  2. Se detiene en cuanto encuentra una convocatoria con `fechaRecepcion`
     anterior a la ventana [hoy - DIAS_ATRAS, hoy] — no hace falta seguir
     paginando porque el orden es descendente por fecha.
  3. Para cada convocatoria dentro de la ventana, consulta su DETALLE
     (`/convocatorias?numConv=...`): el buscador NO trae estructurados
     campos como `organo.nivel1/2/3`, `regiones`, `tiposBeneficiarios`,
     `descripcionFinalidad` o `descripcionBasesReguladoras` — solo el
     detalle los da.
  4. NO se comprueba el estado de la convocatoria (abierta / cerrada /
     resuelta / anulada): se sincroniza tal cual la devuelve la BDNS, sin
     filtrar por ese campo. El filtro de "plazo vigente" en la app se basa
     únicamente en `fecha_fin_solicitud`.

Documentación:
  - Catálogo:         https://datos.gob.es (Base de Datos Nacional de Subvenciones)
  - Portal público:    https://www.infosubvenciones.es

Variables de entorno requeridas: SUPABASE_URL, SUPABASE_SERVICE_KEY.
Ejecución local:      python ingesta_bdns.py
Ejecución programada: ver .github/workflows/sincronizar_bdns.yml
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

BDNS_BASE = "https://www.infosubvenciones.es/bdnstrans/api"
BDNS_BUSQUEDA = f"{BDNS_BASE}/convocatorias/busqueda"
BDNS_DETALLE = f"{BDNS_BASE}/convocatorias"

TAMANO_PAGINA = 50
DIAS_ATRAS = 1                        # ventana: [hoy - DIAS_ATRAS, hoy] -> por defecto, ayer y hoy
PAUSA_ENTRE_PAGINAS_SEGUNDOS = 0.5     # ritmo de consulta responsable con el buscador
PAUSA_ENTRE_DETALLES_SEGUNDOS = 0.2    # idem, con el endpoint de detalle (uno por convocatoria)
MAX_PAGINAS_SEGURIDAD = 100            # red de seguridad por si el filtrado de fecha no encontrase el corte
LOTE_ENVIO_SUPABASE = 25

FUENTE = "BDNS"

# Campos que, si cambian respecto a lo ya guardado, marcan la convocatoria
# como "actualizada" y disparan un recálculo de su embedding.
CAMPOS_COMPARABLES = (
    "titulo", "descripcion", "organismo", "ambito", "beneficiarios",
    "presupuesto_total", "fecha_inicio_solicitud", "fecha_fin_solicitud",
)


# ------------------------------------------------------------------
# Acceso a la API de la BDNS
# ------------------------------------------------------------------
def obtener_pagina_convocatorias(pagina: int) -> dict:
    """Consulta el buscador de convocatorias, ordenado por fecha de recepción descendente."""
    parametros = {
        "page": pagina,
        "pageSize": TAMANO_PAGINA,
        "order": "fechaRecepcion",
        "direccion": "desc",
        "vpd": "GE",  # Vista pública por defecto (catálogo general)
    }
    print(f"--> Consultando página {pagina}...", flush=True)
    respuesta = requests.get(BDNS_BUSQUEDA, params=parametros, timeout=30)
    print(f"    HTTP: {respuesta.status_code}", flush=True)
    respuesta.raise_for_status()
    return respuesta.json()


def obtener_detalle_convocatoria(numero_convocatoria) -> dict:
    """
    IMPORTANTE: este endpoint se consulta por número de convocatoria
    (`numConv`), NO por el `id` interno que devuelve el buscador.
    """
    parametros = {"numConv": numero_convocatoria, "vpd": "GE"}
    print(f"      -> Consultando detalle numConv={numero_convocatoria}...", flush=True)

    try:
        respuesta = requests.get(BDNS_DETALLE, params=parametros, timeout=30)
        print(f"         HTTP: {respuesta.status_code}", flush=True)

        if respuesta.status_code != 200:
            print(f"         Error: {respuesta.text[:300]}", flush=True)
            return {}

        datos = respuesta.json()

        if isinstance(datos, dict):
            return datos
        if isinstance(datos, list) and datos:
            return datos[0]
        return {}

    except Exception as error:
        print(f"         ERROR obteniendo detalle: {error}", flush=True)
        return {}


# ------------------------------------------------------------------
# Conversión y extracción de campos (formato real confirmado contra la API)
# ------------------------------------------------------------------
def convertir_fecha(fecha):
    if not fecha:
        return None
    try:
        return datetime.fromisoformat(str(fecha).replace("Z", "")).date()
    except Exception:
        try:
            return datetime.strptime(str(fecha)[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def extraer_organismo(data: dict):
    organo = data.get("organo") or {}
    if not isinstance(organo, dict):
        return None
    # nivel3 es el más específico (p.ej. una concejalía); se cae hacia
    # niveles más generales si no está informado.
    return organo.get("nivel3") or organo.get("nivel2") or organo.get("nivel1") or organo.get("nombre")


def extraer_ambito(data: dict):
    """
    Devuelve el valor tal cual lo da la BDNS en `organo.nivel1`
    (por ejemplo, "LOCAL"). No se traduce/normaliza a propósito: los
    filtros de la app (ver app/search.py) calculan sus opciones a partir
    de los valores reales presentes en la tabla, así que da igual el
    formato exacto que use la fuente.
    """
    organo = data.get("organo") or {}
    if not isinstance(organo, dict):
        return None
    return organo.get("nivel1")


def extraer_ccaa(data: dict) -> list:
    """La BDNS da las regiones en `regiones` (p.ej. 'ES130 - Cantabria')."""
    regiones = data.get("regiones") or []
    if not isinstance(regiones, list):
        return []
    valores = [r.get("descripcion") for r in regiones if isinstance(r, dict) and r.get("descripcion")]
    return list(dict.fromkeys(valores))


def extraer_beneficiarios(data: dict) -> str:
    beneficiarios = data.get("tiposBeneficiarios") or []
    if not isinstance(beneficiarios, list):
        return ""
    valores = [b.get("descripcion") for b in beneficiarios if isinstance(b, dict) and b.get("descripcion")]
    return ", ".join(dict.fromkeys(valores))


def extraer_descripcion(data: dict):
    """
    La BDNS no tiene un campo "descripcion" independiente del título (su
    propio campo `descripcion` es, de hecho, el título — ver
    `construir_registro`). Para no inventar contenido, la descripción que
    guardamos se construye a partir de `descripcionFinalidad` y
    `descripcionBasesReguladoras`, cuando existen.
    """
    partes = []
    finalidad = data.get("descripcionFinalidad")
    bases = data.get("descripcionBasesReguladoras")
    if finalidad:
        partes.append(f"Finalidad: {finalidad}")
    if bases:
        partes.append(f"Bases reguladoras: {bases}")
    return "\n".join(partes) if partes else None


def construir_texto_completo(datos: dict) -> str:
    """Texto fuente para el embedding semántico."""
    partes = [f"Título: {datos['titulo']}"]
    if datos.get("descripcion"):
        partes.append(datos["descripcion"])
    if datos.get("organismo"):
        partes.append(f"Organismo: {datos['organismo']}")
    if datos.get("ambito"):
        partes.append(f"Ámbito: {datos['ambito']}")
    if datos.get("ccaa"):
        partes.append(f"CCAA: {', '.join(datos['ccaa'])}")
    if datos.get("beneficiarios"):
        partes.append(f"Beneficiarios: {datos['beneficiarios']}")
    if datos.get("presupuesto_total") is not None:
        partes.append(f"Presupuesto: {datos['presupuesto_total']}")
    if datos.get("fecha_inicio_solicitud"):
        partes.append(f"Inicio solicitud: {datos['fecha_inicio_solicitud']}")
    if datos.get("fecha_fin_solicitud"):
        partes.append(f"Fin solicitud: {datos['fecha_fin_solicitud']}")
    return "\n".join(partes)


# ------------------------------------------------------------------
# Normalización al esquema de la tabla `subvenciones`
# ------------------------------------------------------------------
def construir_registro(item: dict, detalle: dict) -> dict:
    """
    Fusiona el resultado del buscador (`item`) con el detalle
    (`detalle`, que tiene prioridad si un campo aparece en ambos) y lo
    traduce al esquema de `subvenciones`.
    """
    data = {**item, **detalle}

    codigo_bdns = data.get("codigoBDNS") or data.get("numeroConvocatoria")
    numero_convocatoria = data.get("numeroConvocatoria")
    # El campo "descripcion" de la BDNS es, en la práctica, el título.
    titulo = data.get("descripcion") or "Sin título"

    return {
        "codigo_unico": f"BDNS-{codigo_bdns}" if codigo_bdns else None,
        "codigo_bdns": str(codigo_bdns) if codigo_bdns else None,
        "fuente_origen": FUENTE,
        "titulo": titulo,
        "descripcion": extraer_descripcion(data),
        "organismo": extraer_organismo(data),
        "ambito": extraer_ambito(data),
        "ccaa": extraer_ccaa(data),
        "url_oficial": (
            f"https://www.infosubvenciones.es/bdnstrans/GE/es/convocatoria/{numero_convocatoria}"
            if numero_convocatoria else None
        ),
        "url_boe": None,
        "fecha_publicacion": convertir_fecha(data.get("fechaRecepcion")),
        "fecha_inicio_solicitud": convertir_fecha(data.get("fechaInicioSolicitud")),
        "fecha_fin_solicitud": convertir_fecha(data.get("fechaFinSolicitud")),
        "presupuesto_total": data.get("presupuestoTotal"),
        "beneficiarios": extraer_beneficiarios(data),
    }


# ------------------------------------------------------------------
# Decidir qué subir (nuevas / cambiadas) sin recalcular embeddings de más
# ------------------------------------------------------------------
def preparar_lote_para_subir(normalizados: list, registros_existentes: dict) -> list:
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
    hoy = date.today()
    desde = hoy - timedelta(days=DIAS_ATRAS)

    print("=" * 100, flush=True)
    print("SINCRONIZACIÓN DE SUBVENCIONES — BDNS", flush=True)
    print("=" * 100, flush=True)
    print(f"Ventana de búsqueda: {desde} .. {hoy}", flush=True)
    print("=" * 100, flush=True)

    candidatos = []
    pagina = 0
    detener = False

    while pagina < MAX_PAGINAS_SEGURIDAD and not detener:
        datos = obtener_pagina_convocatorias(pagina)
        contenido = datos.get("content", [])

        if not contenido:
            print("    No hay más resultados.", flush=True)
            break

        for item in contenido:
            fecha_recepcion = convertir_fecha(item.get("fechaRecepcion"))
            if fecha_recepcion is None:
                continue

            # Ordenado DESC: en cuanto bajamos del límite inferior, paramos.
            if fecha_recepcion < desde:
                print(f"    Llegamos a {fecha_recepcion}, anterior a {desde}. Fin del escaneo.", flush=True)
                detener = True
                break

            if desde <= fecha_recepcion <= hoy:
                candidatos.append(item)

        pagina += 1
        time.sleep(PAUSA_ENTRE_PAGINAS_SEGUNDOS)

    print(f"\nConvocatorias candidatas en la ventana: {len(candidatos)}", flush=True)

    if not candidatos:
        print("No se han encontrado convocatorias nuevas en la ventana.", flush=True)
        return

    normalizados = []
    for item in candidatos:
        numero_convocatoria = item.get("numeroConvocatoria")
        titulo_preview = item.get("descripcion") or "Sin título"

        print(flush=True)
        print("-" * 100, flush=True)
        print("SUBVENCIÓN ENCONTRADA", flush=True)
        print(f"Fecha publicación: {item.get('fechaRecepcion')}", flush=True)
        print(f"Nº convocatoria:   {numero_convocatoria}", flush=True)
        print(f"Título:            {titulo_preview}", flush=True)

        detalle = {}
        if numero_convocatoria:
            detalle = obtener_detalle_convocatoria(numero_convocatoria)
            time.sleep(PAUSA_ENTRE_DETALLES_SEGUNDOS)

        registro = construir_registro(item, detalle)
        if registro["codigo_unico"]:
            normalizados.append(registro)
        else:
            print("    ⚠️ Sin codigoBDNS/numeroConvocatoria: se descarta.", flush=True)

    supabase = obtener_cliente_supabase()

    print(flush=True)
    print("Comparando con lo ya existente en Supabase...", flush=True)
    registros_existentes = obtener_registros_existentes(
        supabase,
        columnas=("id", "codigo_unico") + CAMPOS_COMPARABLES,
        codigos_unicos=[d["codigo_unico"] for d in normalizados],
    )

    lote_final = preparar_lote_para_subir(normalizados, registros_existentes)

    if not lote_final:
        print("No hay convocatorias nuevas ni cambios que sincronizar.", flush=True)
        return

    subidas = subir_en_lotes(supabase, lote_final, tamano_lote=LOTE_ENVIO_SUPABASE)
    print(f"\nSincronización BDNS completada: {subidas}/{len(lote_final)} registros subidos.", flush=True)


if __name__ == "__main__":
    ejecutar_sincronizacion()
