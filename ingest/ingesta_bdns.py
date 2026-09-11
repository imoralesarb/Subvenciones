from datetime import datetime, timedelta
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
PAUSA_ENTRE_PAGINAS_SEGUNDOS = 1.0
MAX_PAGINAS_POR_EJECUCION = 2  # Solo necesitamos la(s) primera(s) página(s) para lo reciente
LOTE_ENVIO_SUPABASE = 25

FUENTE = "BDNS"

CAMPOS_COMPARABLES = (
    "titulo", "descripcion", "organismo", "ambito",
    "presupuesto_total", "fecha_inicio_solicitud", "fecha_fin_solicitud",
    "abierto_indefinidamente", "organismo_nivel1", "organismo_nivel2", "organismo_nivel3"
)


def obtener_pagina_convocatorias(pagina: int, tamano: int = TAMANO_PAGINA) -> dict:
    parametros = {
        "page": pagina,
        "pageSize": tamano,
        "order": "fechaRecepcion",
        "direccion": "desc",
        "vpd": "GE",
    }
    respuesta = requests.get(BDNS_BUSQUEDA, params=parametros, timeout=30)
    respuesta.raise_for_status()
    return respuesta.json()


def obtener_detalle_convocatoria(id_interno: int) -> dict:
    url_detalle = f"{BDNS_BASE}/convocatorias/{id_interno}"
    try:
        resp = requests.get(url_detalle, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {}


def normalizar_convocatoria(item_bdns: dict) -> dict:
    numero = item_bdns.get("codigoBDNS") or item_bdns.get("numeroConvocatoria")
    ambito_raw = (item_bdns.get("ambito") or "").upper()
    titulo = (item_bdns.get("titulo") or item_bdns.get("descripcion") or "").strip()
    descripcion = item_bdns.get("descripcion") or ""

    ccaa = [
        c.get("descripcion")
        for c in (item_bdns.get("comunidadesAutonomas") or [])
        if c.get("descripcion")
    ]

    instrumentos = item_bdns.get("instrumentos", [])
    instrumento_str = instrumentos[0].get("descripcion") if instrumentos else None

    beneficiarios = [b.get("descripcion") for b in item_bdns.get("tiposBeneficiarios", [])]
    sectores = [f"{s.get('codigo')} - {s.get('descripcion')}" for s in item_bdns.get("sectores", [])]
    regiones = [r.get("descripcion") for r in item_bdns.get("regiones", [])]
    organo = item_bdns.get("organo", {})

    categorias = set(inferir_categorias(titulo, descripcion))
    for elemento in instrumentos:
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
        "organismo": organo.get("nombre") or organo.get("nivel3"),
        "organismo_nivel1": organo.get("nivel1"),
        "organismo_nivel2": organo.get("nivel2"),
        "organismo_nivel3": organo.get("nivel3"),
        "ambito": "Nacional" if ambito_raw == "N" else "Autonómico",
        "ccaa": ccaa,
        "categorias": sorted(categorias),
        "fecha_publicacion": item_bdns.get("fechaRecepcion"),
        "fecha_inicio_solicitud": item_bdns.get("fechaInicioSolicitud"),
        "fecha_fin_solicitud": item_bdns.get("fechaFinSolicitud"),
        "presupuesto_total": item_bdns.get("presupuestoTotal"),
        "abierto_indefinidamente": item_bdns.get("abierto", False),
        "instrumento_ayuda": instrumento_str,
        "bases_reguladoras": item_bdns.get("descripcionBasesReguladoras"),
        "url_bases_reguladoras": item_bdns.get("urlBasesReguladoras"),
        "tipos_beneficiarios": ", ".join(beneficiarios) if beneficiarios else None,
        "sectores_economicos": ", ".join(sectores) if secteurs else None,
        "regiones_impacto": ", ".join(regiones) if regiones else None,
        "empleados_min": None,
        "empleados_max": None,
        "antiguedad_min_anios": None,
        "antiguedad_max_anios": None,
    }


def preparar_lote_para_subir(convocatorias_normalizadas: list, registros_existentes: dict) -> list:
    a_subir = []

    for datos in convocatorias_normalizadas:
        existente = registros_existentes.get(datos["codigo_unico"])

        texto_completo = (
            f"Título: {datos['titulo']}. Organismo: {datos['organismo'] or 'No especificado'}. "
            f"Ámbito: {datos['ambito']}. CCAA: {', '.join(datos['ccaa']) or 'Nacional'}. "
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
            continue

        datos["texto_completo"] = texto_completo
        datos["embedding"] = generar_embedding(texto_completo)
        datos["es_novedad"] = False
        datos["es_actualizada"] = True
        a_subir.append(datos)

    return a_subir


def ejecutar_sincronizacion():
    supabase = obtener_cliente_supabase()

    # Fechas de hoy y ayer en formato 'YYYY-MM-DD'
    hoy = datetime.now().date()
    ayer = hoy - timedelta(days=1)
    fechas_validas = {hoy.isoformat(), ayer.isoformat()}

    print(f"Iniciando sincronización BDNS (filtrando solo para hoy {hoy} y ayer {ayer})...", flush=True)

    pagina = 0
    convocatorias_normalizadas = []

    while pagina < MAX_PAGINAS_POR_EJECUCION:
        print(f"Consultando página {pagina}...", flush=True)
        datos = obtener_pagina_convocatorias(pagina)
        contenido = datos.get("content", [])

        if not contenido:
            break

        for item in contenido:
            fecha_recepcion = item.get("fechaRecepcion") or ""
            # Cortamos a los primeros 10 caracteres (YYYY-MM-DD) por si trae hora
            fecha_corta = fecha_recepcion[:10]

            if fecha_corta in fechas_validas:
                id_interno = item.get("id")
                detalle = obtener_detalle_convocatoria(id_interno) if id_interno else {}
                abierto = detalle.get("abierto", False)

                if abierto:
                    combinado = {**item, **detalle}
                    convocatorias_normalizadas.append(normalizar_convocatoria(combinado))
                    print(f"-> Añadida convocatoria ID {id_interno} ({fecha_corta})", flush=True)

        pagina += 1
        time.sleep(PAUSA_ENTRE_PAGINAS_SEGUNDOS)

    if not convocatorias_normalizadas:
        print("No se han encontrado convocatorias nuevas para hoy o ayer que estén abiertas.", flush=True)
        return

    print(f"Procesando {len(convocatorias_normalizadas)} convocatorias filtradas...", flush=True)
    registros_existentes = obtener_registros_existentes(
        supabase,
        cols=("id", "codigo_unico") + CAMPOS_COMPARABLES,
        codigos_unicos=[d["codigo_unico"] for d in convocatorias_normalizadas],
    )

    lote_final = preparar_lote_para_subir(convocatorias_normalizadas, registros_existentes)

    if not lote_final:
        print("No hay cambios nuevos que sincronizar en este lote.", flush=True)
        return

    subidas = subir_en_lotes(supabase, lote_final, tamano_lote=LOTE_ENVIO_SUPABASE)
    print(f"Sincronización completada: {subidas}/{len(lote_final)} registros subidos.", flush=True)


if __name__ == "__main__":
    ejecutar_sincronizacion()
