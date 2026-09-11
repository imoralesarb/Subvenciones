"""
ingesta_boe.py
---------------
Sincroniza el sumario diario del Boletín Oficial del Estado (BOE) contra
la tabla `subvenciones` de Supabase. Es una fuente COMPLEMENTARIA a la
BDNS (ver ingesta_bdns.py): sirve para (a) enlazar cada convocatoria BDNS
con su publicación normativa oficial y (b) detectar el mismo día anuncios
que el BOE ya ha publicado pero que la BDNS todavía no ha indexado.

Documentación oficial:
  - https://www.boe.es/datosabiertos/api/boe/ (API de datos abiertos)
  - "API para acceder a los sumarios del BOE" (PDF de especificación)

Endpoint usado (sumario del día, en XML):
  GET https://www.boe.es/datosabiertos/api/boe/sumario/{AAAAMMDD}
  Accept: application/xml

Estrategia:
  1. Descarga el sumario de la fecha indicada (por defecto, hoy).
  2. Recorre únicamente las secciones que contienen ayudas:
       - Sección III  (Otras disposiciones -> bases reguladoras)
       - Sección VB   (Otros anuncios oficiales -> extractos de convocatoria)
  3. Filtra por patrón de texto en el título (extracto, convocatoria,
     subvención, ayuda, beca, premio).
  4. Si el texto incluye un número BDNS, se usa como clave `codigo_unico`
     (BDNS-<numero>) para que el upsert FUSIONE este anuncio con el
     registro que (normalmente) ya habrá creado ingesta_bdns.py, en vez de
     machacar sus datos, ya mucho más completos, con los escasos datos
     del sumario del BOE. Si no lo incluye, se usa el identificador propio
     del BOE (BOE-<id>) como convocatoria "solo BOE" hasta que la BDNS la
     indexe.

Variables de entorno requeridas: SUPABASE_URL, SUPABASE_SERVICE_KEY.
Ejecución local:
    python ingesta_boe.py            (usa la fecha de hoy)
    python ingesta_boe.py 20260910   (fecha concreta, formato AAAAMMDD)
Ejecución programada: ver .github/workflows/sincronizar_boe.yml
"""

import re
import sys
from datetime import date
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

# Palabras clave que delatan un extracto de convocatoria de ayuda.
PATRON_PALABRAS_CLAVE = re.compile(
    r"\b(extracto|convocatoria|subvenci[oó]n|ayuda|beca|premio)\b", re.IGNORECASE
)

# Un número de convocatoria BDNS suele citarse como "BDNS(Identif.): 123456".
PATRON_NUMERO_BDNS = re.compile(r"BDNS[^\d]{0,15}(\d{5,9})", re.IGNORECASE)

COLUMNAS_EXISTENTES = ("id", "codigo_unico", "fuente_origen", "url_boe")


# ------------------------------------------------------------------
# Descarga y parseo del sumario del BOE
# ------------------------------------------------------------------
def obtener_sumario(fecha_aaaammdd: str):
    """Descarga y parsea el sumario diario del BOE (XML)."""
    url = BOE_SUMARIO_URL.format(fecha=fecha_aaaammdd)
    respuesta = requests.get(url, headers={"Accept": "application/xml"}, timeout=30)
    respuesta.raise_for_status()
    return ElementTree.fromstring(respuesta.content)


def extraer_items_relevantes(raiz_xml) -> list:
    """
    Recorre el sumario y devuelve los <item> de las secciones relevantes
    (III y V-B) cuyo título contiene alguna palabra clave de ayuda.

    Estructura real del XML, según la especificación oficial ("API para
    el acceso a los sumarios del BOE", AEBOE, junio de 2024):

        <seccion codigo="..." nombre="...">
          <departamento codigo="..." nombre="...">
            <epigrafe nombre="...">      <!-- opcional -->
              <item>...</item>
            </epigrafe>
            <item>...</item>              <!-- o directamente aquí -->
          </departamento>
        </seccion>

    El nombre del departamento va en el ATRIBUTO `nombre` de
    <departamento> (no en su texto), y <item> puede colgar directamente
    de <departamento> o de un <epigrafe> intermedio — por eso se
    comprueban ambos casos explícitamente en vez de asumir un único nivel
    de anidación.
    """
    items_relevantes = []

    for seccion in raiz_xml.iter("seccion"):
        codigo_seccion = seccion.get("codigo", "")
        if codigo_seccion not in SECCIONES_RELEVANTES:
            continue

        for departamento in seccion.findall("departamento"):
            nombre_departamento = departamento.get("nombre")

            items_del_departamento = list(departamento.findall("item"))
            for epigrafe in departamento.findall("epigrafe"):
                items_del_departamento.extend(epigrafe.findall("item"))

            for item in items_del_departamento:
                titulo = (item.findtext("titulo") or "").strip()
                if not PATRON_PALABRAS_CLAVE.search(titulo):
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
def normalizar_item_boe(item: dict) -> dict:
    coincidencia_bdns = PATRON_NUMERO_BDNS.search(item["titulo"])
    url = item["url_html"] or item["url_pdf"]

    codigo_unico = (
        f"BDNS-{coincidencia_bdns.group(1)}" if coincidencia_bdns
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
        # Ámbito y CCAA no siempre son deducibles del sumario; si la BDNS
        # ya había creado el registro, sus valores se conservan tal cual
        # (ver preparar_operaciones: para registros existentes solo se
        # fusiona fuente_origen y url_boe, nunca se pisan estos campos).
        "ambito": "Nacional",
        "ccaa": [],
        "categorias": [],
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
        legal del BOE (`url_boe`, `fuente_origen`), sin pisar los datos
        más completos que ya aportó la BDNS.
    """
    nuevos, actualizaciones = [], []

    for datos in items_normalizados:
        existente = registros_existentes.get(datos["codigo_unico"])

        if existente is None:
            texto_completo = (
                f"Título: {datos['titulo']}. Fuente: BOE. "
                f"Organismo: {datos['organismo'] or 'No especificado'}."
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
            cambios["fuente_origen"] = f"{fuente_actual}, BOE" if fuente_actual else "BOE"
        if not existente.get("url_boe"):
            cambios["url_boe"] = datos["url_boe"]

        if cambios:
            cambios["es_actualizada"] = True
            actualizaciones.append({"id": existente["id"], **cambios})

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
            print(f"⚠️ Error enriqueciendo el registro {id_registro} con datos del BOE: {error}")
    return aplicadas


# ------------------------------------------------------------------
# Ejecución principal
# ------------------------------------------------------------------
def ejecutar_sincronizacion(fecha: str = None):
    fecha_aaaammdd = fecha or date.today().strftime("%Y%m%d")
    print(f"Descargando sumario del BOE para {fecha_aaaammdd}...")

    try:
        raiz_xml = obtener_sumario(fecha_aaaammdd)
    except requests.HTTPError as error:
        # Es habitual que no haya BOE publicado en fines de semana/festivos.
        print(f"No se pudo obtener el sumario ({error}). Puede que no haya BOE ese día.")
        return

    items = extraer_items_relevantes(raiz_xml)
    if not items:
        print("No se han encontrado anuncios de ayudas en el sumario de hoy.")
        return

    items_normalizados = [normalizar_item_boe(item) for item in items]

    supabase = obtener_cliente_supabase()
    registros_existentes = obtener_registros_existentes(
        supabase,
        columnas=COLUMNAS_EXISTENTES,
        codigos_unicos=[d["codigo_unico"] for d in items_normalizados],
    )

    nuevos, actualizaciones = preparar_operaciones(items_normalizados, registros_existentes)

    subidos = subir_en_lotes(supabase, nuevos) if nuevos else 0
    aplicadas = aplicar_actualizaciones(supabase, actualizaciones) if actualizaciones else 0

    print(
        f"Sincronización BOE completada: {subidos} convocatorias nuevas, "
        f"{aplicadas} enriquecidas con referencia legal del BOE."
    )


if __name__ == "__main__":
    fecha_arg = sys.argv[1] if len(sys.argv) > 1 else None
    ejecutar_sincronizacion(fecha_arg)
