import re
import sys
from datetime import date, timedelta
from xml.etree import ElementTree

import requests

from common import (
    generar_embedding,
    obtener_cliente_supabase,
    obtener_registros_existentes,
    subir_en_lotes,
)

BOE_SUMARIO_URL = "https://www.boe.es/datosabiertos/api/boe/sumario/{fecha}"

# Secciones del BOE donde aparecen ayudas/subvenciones.
SECCIONES_RELEVANTES = {"3", "VB"}  # "3" = Sección III, "VB" = Sección V-B

# Términos que indican que el anuncio está relacionado
# con una convocatoria o ayuda.
PATRON_CONVOCATORIA = re.compile(
    r"\b(extracto|convocatoria|convocan|convoca|"
    r"subvenci[oó]n|subvenciones|ayuda|ayudas|"
    r"beca|becas|premio|premios)\b",
    re.IGNORECASE
)

# Términos que suelen indicar concesiones, beneficiarios
# o adjudicaciones ya realizadas.
PATRON_CONCESION = re.compile(
    r"\b(concesi[oó]n|concedida|concedidas|concedido|concedidos|"
    r"beneficiari[oa]s|adjudicaci[oó]n|pagos?)\b",
    re.IGNORECASE
)

# Un número de convocatoria BDNS suele citarse como "BDNS(Identif.): 123456".
PATRON_NUMERO_BDNS = re.compile(r"BDNS[^\d]{0,15}(\d{5,9})", re.IGNORECASE)

DIAS_ATRAS = 1  # por defecto se procesan ayer y hoy (misma ventana que la BDNS)

COLUMNAS_EXISTENTES = (
    "id",
    "codigo_unico",
    "fuente_origen",
    "url_boe",
    "fecha_publicacion",
)


# ------------------------------------------------------------------
# Descarga y parseo del sumario del BOE
# ------------------------------------------------------------------
def obtener_sumario(fecha_aaaammdd: str):
    url = BOE_SUMARIO_URL.format(fecha=fecha_aaaammdd)
    print(f"Descargando sumario BOE: {fecha_aaaammdd}", flush=True)
    respuesta = requests.get(url, headers={"Accept": "application/xml"}, timeout=30)
    print(f"HTTP: {respuesta.status_code}", flush=True)
    respuesta.raise_for_status()
    return ElementTree.fromstring(respuesta.content)


def extraer_items_relevantes(raiz_xml) -> list:
    """Recorre el sumario y devuelve los <item> relevantes (ver docstring del módulo)."""
    items_relevantes = []

    for seccion in raiz_xml.iter("seccion"):
        codigo_seccion = seccion.get("codigo", "")
        if codigo_seccion not in SECCIONES_RELEVANTES:
            continue

        print(f"Procesando sección: {codigo_seccion}", flush=True)

        for departamento in seccion.findall("departamento"):
            nombre_departamento = departamento.get("nombre")

            items_del_departamento = list(departamento.findall("item"))
            for epigrafe in departamento.findall("epigrafe"):
                items_del_departamento.extend(epigrafe.findall("item"))

            for item in items_del_departamento:
                titulo = (item.findtext("titulo") or "").strip()

                # Debe parecer una convocatoria o ayuda.
                if not PATRON_CONVOCATORIA.search(titulo):
                    continue

                # Excluir anuncios que claramente correspondan
                # a concesiones, beneficiarios o adjudicaciones.
                if PATRON_CONCESION.search(titulo):
                    continue

                items_relevantes.append({
                    "identificador_boe": item.findtext("identificador"),
                    "titulo": titulo,
                    "url_html": item.findtext("url_html"),
                    "url_pdf": item.findtext("url_pdf"),
                    "departamento": nombre_departamento,
                    "seccion": codigo_seccion,
                })

    return items_relevantes


# ------------------------------------------------------------------
# Normalización al esquema de la tabla `subvenciones`
# ------------------------------------------------------------------
def normalizar_item_boe(item: dict, fecha_aaaammdd: str) -> dict:
    coincidencia_bdns = PATRON_NUMERO_BDNS.search(item["titulo"])
    url = item["url_html"] or item["url_pdf"]

    codigo_unico = (
        f"BDNS-{coincidencia_bdns.group(1)}"
        if coincidencia_bdns
        else f"BOE-{item['identificador_boe']}"
    )

    return {
        "codigo_unico": codigo_unico,
        "codigo_bdns": coincidencia_bdns.group(1) if coincidencia_bdns else None,
        "titulo": item["titulo"],
        "descripcion": (
            f"Publicado en el BOE (Sección {item['seccion']}). "
            "Consulta el texto completo en la fuente oficial."
        ),
        "fuente_origen": "BOE",
        "url_oficial": url,
        "url_boe": url,
        "organismo": item["departamento"],

        # Fecha en la que aparece el anuncio en el BOE
        "fecha_publicacion": (
            f"{fecha_aaaammdd[:4]}-"
            f"{fecha_aaaammdd[4:6]}-"
            f"{fecha_aaaammdd[6:8]}"
        ),

        "ambito": "Nacional",
        "ccaa": [],
    }


# ------------------------------------------------------------------
# Fusión con lo ya existente en Supabase (por codigo_unico)
# ------------------------------------------------------------------
def preparar_operaciones(items_normalizados: list, registros_existentes: dict):
    """
    Devuelve dos listas:
      - `nuevos`: convocatorias vistas por primera vez (el BOE se ha
        adelantado a la BDNS) — se insertan con upsert normal, con embedding.
      - `actualizaciones`: convocatorias que ya existían (normalmente
        creadas por ingesta_bdns.py) — solo se les añade la referencia
        legal del BOE (`url_boe`, `fuente_origen`, `fecha_publicacion`),
        sin pisar los datos más completos que ya aportó la BDNS.
    """
    nuevos, actualizaciones = [], []

    for datos in items_normalizados:
        existente = registros_existentes.get(datos["codigo_unico"])

        if existente is None:
            texto_completo = (
                f"Título: {datos['titulo']}\n"
                f"Fuente: BOE\n"
                f"Organismo: {datos['organismo'] or 'No especificado'}"
            )
            datos["texto_completo"] = texto_completo
            datos["embedding"] = generar_embedding(texto_completo)
            datos["es_novedad"] = True
            datos["es_actualizada"] = False
            nuevos.append(datos)
            continue

        cambios = {}
        fuente_actual = existente.get("fuente_origen") or ""

        if "BOE" not in fuente_actual:
            cambios["fuente_origen"] = (
                f"{fuente_actual}, BOE"
                if fuente_actual
                else "BOE"
            )

        if not existente.get("url_boe"):
            cambios["url_boe"] = datos["url_boe"]

        if not existente.get("fecha_publicacion"):
            cambios["fecha_publicacion"] = datos["fecha_publicacion"]

        if cambios:
            cambios["es_actualizada"] = True
            actualizaciones.append({
                "id": existente["id"],
                **cambios
            })

    return nuevos, actualizaciones


def aplicar_actualizaciones(supabase, actualizaciones: list) -> int:
    aplicadas = 0
    for cambio in actualizaciones:
        cambio = dict(cambio)
        id_registro = cambio.pop("id")
        try:
            supabase.table("subvenciones").update(cambio).eq("id", id_registro).execute()
            aplicadas += 1
        except Exception as error:
            print(
                f"⚠️ Error enriqueciendo el registro {id_registro} "
                f"con datos del BOE: {error}",
                flush=True
            )
    return aplicadas


# ------------------------------------------------------------------
# Ejecución principal
# ------------------------------------------------------------------
def procesar_fecha(fecha_aaaammdd: str) -> list:
    try:
        raiz_xml = obtener_sumario(fecha_aaaammdd)
    except requests.HTTPError as error:
        # Es habitual que no haya BOE publicado en fines de semana/festivos.
        print(
            f"No se pudo obtener el sumario ({error}). "
            f"Puede que no haya BOE ese día.",
            flush=True
        )
        return []
    except Exception as error:
        print(
            f"Error procesando el sumario del {fecha_aaaammdd}: {error}",
            flush=True
        )
        return []

    items = extraer_items_relevantes(raiz_xml)

    print(
        f"  Anuncios de ayudas/subvenciones encontrados: {len(items)}",
        flush=True
    )

    return [
        normalizar_item_boe(item, fecha_aaaammdd)
        for item in items
    ]


def ejecutar_sincronizacion(fecha: str = None):
    if fecha:
        fechas_a_procesar = [fecha]
    else:
        hoy = date.today()
        fechas_a_procesar = [
            (hoy - timedelta(days=dias)).strftime("%Y%m%d")
            for dias in range(DIAS_ATRAS, -1, -1)
        ]

    print("=" * 100, flush=True)
    print("SINCRONIZACIÓN DE SUBVENCIONES — BOE", flush=True)
    print("=" * 100, flush=True)
    print(
        f"Fechas a procesar: {', '.join(fechas_a_procesar)}",
        flush=True
    )
    print("=" * 100, flush=True)

    items_normalizados = []

    for fecha_aaaammdd in fechas_a_procesar:
        items_normalizados.extend(
            procesar_fecha(fecha_aaaammdd)
        )

    if not items_normalizados:
        print(
            "\nNo se han encontrado anuncios de ayudas en el periodo.",
            flush=True
        )
        return

    supabase = obtener_cliente_supabase()

    print(
        f"\nComparando {len(items_normalizados)} anuncios "
        f"con lo ya existente en Supabase...",
        flush=True
    )

    registros_existentes = obtener_registros_existentes(
        supabase,
        columnas=COLUMNAS_EXISTENTES,
        codigos_unicos=[
            d["codigo_unico"]
            for d in items_normalizados
        ],
    )

    nuevos, actualizaciones = preparar_operaciones(
        items_normalizados,
        registros_existentes
    )

    subidos = (
        subir_en_lotes(supabase, nuevos)
        if nuevos
        else 0
    )

    aplicadas = (
        aplicar_actualizaciones(
            supabase,
            actualizaciones
        )
        if actualizaciones
        else 0
    )

    print(
        f"\nSincronización BOE completada: "
        f"{subidos} convocatorias nuevas, "
        f"{aplicadas} enriquecidas con referencia legal del BOE.",
        flush=True,
    )


if __name__ == "__main__":
    fecha_arg = sys.argv[1] if len(sys.argv) > 1 else None
    ejecutar_sincronizacion(fecha_arg)
