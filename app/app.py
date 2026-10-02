import hmac
from datetime import date, timedelta
from pathlib import Path

import streamlit as st


# Configurar la página de Streamlit
# (tiene que ser lo PRIMERO que se ejecute: el login ya emite elementos)
st.set_page_config(
    page_title="Buscador inteligente de Subvenciones",
    page_icon="💶",
    layout="wide",
)


# ============================================================
# LOGIN
# ------------------------------------------------------------
# POR QUÉ ESTÁ HECHO ASÍ (para que no se solape con la app)
#
# Streamlit no "borra" la pantalla al relanzar el script: cada
# elemento nuevo REEMPLAZA al que ocupaba su misma posición en la
# ejecución anterior, y lo que sobra se queda en pantalla (atenuado)
# hasta que el script termina. Si el login emitiera varios elementos
# sueltos, al entrar los sobrantes seguirían visibles durante toda la
# carga del modelo, mezclados con la app.
#
#   1. Toda la pantalla de login (CSS incluido) es UN SOLO elemento
#      raíz (un st.container). En cuanto la app emite su primer
#      elemento, ese único elemento se reemplaza entero y el login
#      desaparece al instante, sin restos.
#   2. El CSS del login vive dentro de ese mismo elemento: se elimina
#      con él y no puede afectar a la app.
#   3. Los módulos pesados (pandas, sentence-transformers, supabase...)
#      se importan DESPUÉS de comprobar el login, y el modelo se
#      precarga mientras el usuario escribe sus credenciales: el login
#      aparece al instante incluso en frío y, al entrar, el modelo ya
#      está en caché (st.cache_resource).
#
# REGLA: mientras el usuario no esté identificado, no emitas nada
# fuera de _pantalla_login(): cada elemento raíz extra reaparecería
# como resto en pantalla al entrar en la aplicación.
# ============================================================

# True: mientras se muestra el login se carga en segundo plano (en la
# propia ejecución, ya con el formulario pintado) el modelo de IA. Pon
# False si prefieres que se cargue solo al iniciar sesión.
PRECARGAR_MODELO_EN_LOGIN = True

# Logo opcional: se usa el primero que exista; si no hay ninguno, la marca
# del login es el 💶 del título de la app.
_DIRECTORIO_APP = Path(__file__).resolve().parent
RUTA_LOGO = next(
    (
        ruta
        for ruta in (
            _DIRECTORIO_APP / "assets" / "logo.png",
            _DIRECTORIO_APP.parent / "assets" / "logo.png",
        )
        if ruta.exists()
    ),
    None,
)

# Todo este CSS se emite DENTRO del contenedor del login (ver
# _pantalla_login), así que desaparece con él al entrar en la app.
CSS_LOGIN = """
/* Franja superior */
header[data-testid="stHeader"] {
    background-color: #0A2F5C !important;
    height: 60px !important;
}

/* Iconos y textos de la barra superior legibles sobre el azul marino */
header[data-testid="stHeader"] [data-testid="stToolbar"],
header[data-testid="stHeader"] [data-testid="stToolbar"] * {
    color: #FFFFFF !important;
}
header[data-testid="stHeader"] [data-testid="stToolbar"] button:hover {
    background-color: rgba(255, 255, 255, 0.14) !important;
}

/* Fondo liso (el mismo gris claro de la app) */
.stApp {
    background-color: #F8F9FA;
}

/* La tarjeta es el propio formulario */
[data-testid="stForm"] {
    background-color: #FFFFFF;
    border: 1px solid #E0E0E0;
    border-radius: 12px;
    padding: 2rem 2rem 1.5rem 2rem;
}

/* Logo centrado dentro de la tarjeta (acotado al formulario) */
[data-testid="stForm"] [data-testid="stFullScreenFrame"] {
    display: flex;
    justify-content: center;
}

/* Etiquetas de los campos */
[data-testid="stForm"] label p {
    color: #172033;
    font-weight: 600;
}

/* Campos con colores fijos: la tarjeta es blanca también si el navegador está en modo oscuro.
   (Se listan el selector actual de Streamlit y el antiguo `data-baseweb`, por compatibilidad.) */
[data-testid="stForm"] [data-testid="stTextInputRootElement"],
[data-testid="stForm"] div[data-baseweb="input"],
[data-testid="stForm"] div[data-baseweb="base-input"] {
    background-color: #F0F2F6 !important;
    border: 1px solid #E0E0E0 !important;
}
[data-testid="stForm"] input {
    color: #172033 !important;
    -webkit-text-fill-color: #172033;
    caret-color: #172033;
}
[data-testid="stForm"] input::placeholder {
    color: #667085 !important;
    -webkit-text-fill-color: #667085;
    opacity: 1;
}
[data-testid="stForm"] [data-testid="stTextInputRootElement"] button,
[data-testid="stForm"] [data-testid="stTextInputRootElement"] svg {
    color: #172033 !important;
    fill: #172033;
}

/* Foco visible en los campos */
[data-testid="stForm"] [data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stForm"] div[data-baseweb="input"]:focus-within {
    border-color: #0066CC !important;
    box-shadow: 0 0 0 1px #0066CC;
}

/* Quita el aviso "Press Enter to submit form" */
[data-testid="stForm"] [data-testid="InputInstructions"] {
    display: none;
}

/* Botón Iniciar sesión (a todo el ancho sin usar parámetros obsoletos) */
[data-testid="stElementContainer"]:has([data-testid="stFormSubmitButton"]),
.element-container:has([data-testid="stFormSubmitButton"]),
[data-testid="stFormSubmitButton"],
[data-testid="stFormSubmitButton"] button {
    width: 100% !important;
}
[data-testid="stFormSubmitButton"] button {
    background-color: #0066CC;
    border: none;
    border-radius: 8px;
    min-height: 45px;
}
[data-testid="stFormSubmitButton"] button p {
    color: #FFFFFF;
    font-weight: 600;
}
[data-testid="stFormSubmitButton"] button:hover {
    background-color: #0052A3;
}
[data-testid="stFormSubmitButton"] button:focus-visible {
    outline: 2px solid #0A2F5C;
    outline-offset: 2px;
}
"""

# El 💶 hace de marca cuando no hay logo (igual que en el título de la app).
MARCA_EMOJI_HTML = """
<div style="text-align: center; font-size: 44px; line-height: 1.1; margin: 4px 0 0 0;">💶</div>
"""

CABECERA_LOGIN_HTML = """
<div role="heading" aria-level="1" style="
    text-align: center;
    font-size: 26px;
    font-weight: 700;
    line-height: 1.25;
    color: #0A2F5C;
    margin: 12px 0 4px 0;
">
    Buscador inteligente de Subvenciones
</div>

<p style="
    text-align: center;
    color: #667085;
    margin: 0 0 12px 0;
">
    Accede a tu plataforma
</p>
"""


def _leer_secretos_login():
    """(usuario, contraseña) de los secrets de la app, o None si no están configurados."""
    try:
        return str(st.secrets["LOGIN_USER"]), str(st.secrets["LOGIN_PASSWORD"])
    except (KeyError, FileNotFoundError):
        return None


def _credenciales_correctas(usuario: str, password: str, usuario_ok: str, password_ok: str) -> bool:
    """Comparación en tiempo constante (y segura con ñ, acentos y demás caracteres no ASCII)."""
    # `&` (no `and`) para que las dos comparaciones se evalúen siempre.
    return hmac.compare_digest(usuario.encode("utf-8"), usuario_ok.encode("utf-8")) & hmac.compare_digest(
        password.encode("utf-8"), password_ok.encode("utf-8")
    )


def _pantalla_login():
    """Pinta el login como UN ÚNICO elemento raíz (ver explicación arriba)."""
    with st.container():

        st.markdown(f"<style>{CSS_LOGIN}</style>", unsafe_allow_html=True)

        _, centro, _ = st.columns([1, 1.4, 1])

        with centro:

            with st.form("form_login", clear_on_submit=False):

                if RUTA_LOGO is not None:
                    st.image(str(RUTA_LOGO), width=150)
                else:
                    st.markdown(MARCA_EMOJI_HTML, unsafe_allow_html=True)

                st.markdown(CABECERA_LOGIN_HTML, unsafe_allow_html=True)

                usuario = st.text_input(
                    "Usuario",
                    placeholder="Introduce tu usuario",
                )

                password = st.text_input(
                    "Contraseña",
                    type="password",
                    placeholder="Introduce tu contraseña",
                )

                entrar = st.form_submit_button("Iniciar sesión", type="primary")

                if entrar:

                    secretos = _leer_secretos_login()

                    if secretos is None:

                        st.error(
                            "Falta configurar LOGIN_USER y LOGIN_PASSWORD en los secrets de la aplicación."
                        )

                    elif _credenciales_correctas(usuario, password, *secretos):

                        st.session_state["logueado"] = True

                        st.rerun()

                    else:

                        st.error("Usuario o contraseña incorrectos.")


def _precargar_modelo():
    """Calienta la caché del modelo (st.cache_resource) sin mostrar nada en pantalla."""
    try:
        from db import obtener_encoder

        obtener_encoder()
    except Exception:
        # Si falla, no se rompe el login: el error saldrá, con su traza
        # normal, al entrar en la aplicación (que vuelve a intentarlo).
        pass


def login():

    if st.session_state.get("logueado", False):
        return True

    _pantalla_login()

    if PRECARGAR_MODELO_EN_LOGIN:
        _precargar_modelo()

    return False


# ============================================================
# COMPROBAR LOGIN
# ============================================================

if not login():
    st.stop()


# ============================================================
# IMPORTS PESADOS: solo cuando el usuario ya ha entrado
# (y ya cacheados si el modelo se precargó durante el login)
# ============================================================

import pandas as pd

from config import FUENTES_DISPONIBLES
from db import obtener_cliente, obtener_encoder
from search import buscar_por_bases_reguladoras, buscar_semantica, listar_novedades, listar_todas, obtener_opciones_filtro

# Desactivar traductor automático del navegador
st.markdown(
    """
    <head>
        <meta name="google" content="notranslate">
    </head>
    """,
    unsafe_allow_html=True,
)

# --- ESTILOS CSS PERSONALIZADOS PARA DISEÑO Y RECUADROS ---
st.markdown(
    """
    <style>
        div.stButton > button:first-child {
            background-color: #0066cc;
            color: white;
            font-weight: bold;
            font-size: 13px;
            padding: 0.48rem 0.95rem;
            border-radius: 7px;
            border: none;
            width: 100%;
            box-shadow: 0 3px 5px rgba(0, 0, 0, 0.1);
            transition: all 0.3s ease;
        }
        div.stButton > button:first-child:hover {
            background-color: #0052a3;
            box-shadow: 0 5px 7px rgba(0, 0, 0, 0.15);
            color: white;
        }
        .results-container {
            background-color: #f8f9fa;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            padding: 16px;
            margin-top: 10px;
            margin-bottom: 10px;
        }
        h1 { font-size: 1.9rem !important; margin-bottom: 0.4rem !important; }
        h3 { font-size: 1.2rem !important; margin-top: 0.5rem !important; margin-bottom: 0.4rem !important; }
        [data-testid="stWidgetLabel"] p { font-size: 12px !important; margin-bottom: 2px !important; }
        label { font-size: 12px !important; }
        div[data-baseweb="input"] { min-height: 22px !important; height: 22px !important; }
        div[data-baseweb="input"] input { height: 20px !important; min-height: 20px !important; padding: 1px 6px !important; font-size: 11px !important; }
        div[data-baseweb="select"] { min-height: 22px !important; font-size: 11px !important; }
        div[data-baseweb="select"] * { font-size: 11px !important; }
        [data-baseweb="tag"] { font-size: 10px !important; padding: 0 4px !important; margin: 0 1px !important; line-height: 17px !important; }
        [data-testid="stNumberInput"] button { min-height: 22px !important; height: 22px !important; width: 22px !important; }
        [data-testid="stDateInput"] [data-baseweb="input"] { min-height: 22px !important; height: 22px !important; }
        [data-testid="stDateInput"] input { height: 20px !important; min-height: 20px !important; padding: 1px 6px !important; font-size: 11px !important; }
        [data-testid="stVerticalBlock"] { gap: 0.5rem; }
        [data-testid="column"] { padding-left: 0.3rem; padding-right: 0.3rem; }
        [data-testid="stCheckbox"] label { font-size: 12px !important; }
        [data-testid="stSlider"] { margin-top: 0px; margin-bottom: 0px; }
        [data-testid="stAlert"] { font-size: 13px !important; }
        .block-container { padding-top: 1.5rem; padding-bottom: 1.5rem; }
    </style>
    """,
    unsafe_allow_html=True
)

# 1. Conexión a Supabase y modelo de IA
supabase = obtener_cliente()

with st.spinner("Cargando modelo de IA..."):
    encoder = obtener_encoder()

# Opciones de Ámbito, CCAA, Beneficiarios y Tipo convocatoria
(
    AMBITOS_DISPONIBLES,
    CCAA_DISPONIBLES,
    BENEFICIARIOS_DISPONIBLES,
    TIPOS_CONVOCATORIA_DISPONIBLES,
) = obtener_opciones_filtro(supabase)

# Inicializar estados de sesión para persistencia de resultados
if "df_resultados" not in st.session_state:
    st.session_state.df_resultados = None
if "mensaje_estado" not in st.session_state:
    st.session_state.mensaje_estado = ""

# 2. Interfaz Visual y Gestión de Estado


def cerrar_sesion():
    """
    Cierra la sesión. Se borra TODO el estado (filtros, resultados...), no solo
    la marca de sesión iniciada, para que quien entre después desde el mismo
    navegador no vea la última búsqueda. Al ser un callback (on_click), Streamlit
    relanza el script a continuación y vuelve a aparecer el login.
    """
    st.session_state.clear()


col_titulo, col_cerrar_sesion = st.columns([8, 1])

with col_titulo:
    st.title("💶 Buscador inteligente de Subvenciones")

with col_cerrar_sesion:
    # Espaciador: baja el botón para que quede alineado con el título y no lo tape la
    # barra superior de Streamlit (el CSS de arriba deja solo 1,5 rem de margen superior).
    st.markdown('<div style="height: 32px;"></div>', unsafe_allow_html=True)
    st.button(
        "Cerrar sesión",
        on_click=cerrar_sesion,
        type="secondary",
        use_container_width=True,
        key="btn_cerrar_sesion",
    )


def limpiar_campos():
    st.session_state.consulta_texto = ""
    st.session_state.filtro_fuente = []
    st.session_state.filtro_ambito = []
    st.session_state.filtro_ccaa = []
    st.session_state.filtro_beneficiario_opcion = []
    st.session_state.filtro_titulo_bases_texto = ""
    st.session_state.filtro_tipo_convocatoria = []
    st.session_state.importe_min = 0.0
    st.session_state.importe_max = 0.0
    st.session_state.limite_resultados = 10
    st.session_state.mostrar_todos = True
    st.session_state.df_resultados = None
    st.session_state.mensaje_estado = ""


# Buscador principal
consulta_texto = st.text_input(
    "¿Qué tipo de subvención buscas?",
    placeholder="ej. ayudas a la digitalización de pymes, subvenciones culturales, becas de formación...",
    key="consulta_texto",
)

# Panel de filtros avanzados
st.markdown("### ⚙️ Filtros avanzados")
col0, col_ambito, col1, col2, col3 = st.columns(5)

with col0:
    filtro_fuente = st.multiselect("🌐 Fuente", FUENTES_DISPONIBLES, default=[], key="filtro_fuente")
with col_ambito:
    filtro_ambito = st.multiselect("🏛️ Ámbito", AMBITOS_DISPONIBLES, default=[], key="filtro_ambito")
with col1:
    importe_min = st.number_input("Importe Mínimo (€)", value=0.0, key="importe_min")
with col2:
    importe_max = st.number_input("Importe Máximo (€)", value=0.0, key="importe_max")
with col3:
    filtro_ccaa = st.multiselect("📍 Comunidad Autónoma", CCAA_DISPONIBLES, default=[], key="filtro_ccaa")

# Campos de Beneficiarios (multiselect) y Título de bases reguladoras (texto libre con IA)
col_beneficiarios_filtro, col_titulo_bases = st.columns(2)

with col_beneficiarios_filtro:
    filtro_beneficiario_opcion = st.multiselect(
        "👥 Beneficiarios",
        BENEFICIARIOS_DISPONIBLES,
        default=[],
        key="filtro_beneficiario_opcion",
    )
with col_titulo_bases:
    filtro_titulo_bases_texto = st.text_input(
        "📜 Título de bases reguladoras",
        placeholder="ej. bases reguladoras de digitalización...",
        key="filtro_titulo_bases_texto",
    )

# Fila adicional: Tipo de convocatoria y Fechas
col_tipo_conv, col_fecha_fin = st.columns([1, 1])

with col_tipo_conv:
    filtro_tipo_convocatoria = st.multiselect(
        "📋 Tipo de convocatoria",
        TIPOS_CONVOCATORIA_DISPONIBLES,
        default=[],
        key="filtro_tipo_convocatoria",
    )

with col_fecha_fin:
    fecha_cierre_tope = st.date_input(
        "⏳ Fecha fin de presentación (Mínima)",
        value=date.today() - timedelta(days=1),
        key="fecha_cierre_tope",
    )

col_rango_desde, col_rango_hasta = st.columns(2)
with col_rango_desde:
    f_inicio = st.date_input("📅 Rango publicación (Desde)", value=date(2026, 1, 1), key="f_inicio")
with col_rango_hasta:
    f_fin = st.date_input("📅 Rango publicación (Hasta)", value=date(2100, 12, 31), key="f_fin")

col_resultados, col_vacio = st.columns([60, 40])

with col_resultados:
    with st.container(border=True):
        st.markdown("<strong>¿Cuántos resultados quieres ver?</strong>", unsafe_allow_html=True)

        if "mostrar_todos" not in st.session_state:
            st.session_state.mostrar_todos = True
        if "limite_resultados" not in st.session_state:
            st.session_state.limite_resultados = 10

        def actualizar_slider():
            st.session_state.mostrar_todos = False

        col_res_chk, col_res_texto, col_res_slider = st.columns([2.5, 2, 4])

        with col_res_chk:
            mostrar_todos = st.checkbox("Mostrar TODOS los resultados", key="mostrar_todos")

        color_texto = "gray" if mostrar_todos else "inherit"

        with col_res_texto:
            st.markdown(
                f'<div style="font-size: 12px; padding-top: 8px; color: {color_texto};">Seleccionar número de resultados:</div>',
                unsafe_allow_html=True,
            )

        with col_res_slider:
            limite_resultados = st.slider(
                "Resultados",
                min_value=1,
                max_value=500,
                key="limite_resultados",
                label_visibility="collapsed",
                disabled=mostrar_todos,
                on_change=actualizar_slider,
            )

# --- BOTONES DE ACCIÓN PRINCIPAL ---
col_btn_buscar, col_btn_novedades, col_btn_limpiar = st.columns([2, 2, 2])

with col_btn_buscar:
    btn_buscar = st.button("🔍 Buscar subvenciones", type="primary", use_container_width=True)

with col_btn_novedades:
    btn_novedades = st.button("✨ Novedades", type="secondary", use_container_width=True)

with col_btn_limpiar:
    btn_limpiar = st.button("🔄 Limpiar Filtros", on_click=limpiar_campos, type="secondary", use_container_width=True)


def estilizar_filas(row):
    if row.get("Es Novedad", False):
        return ["background-color: #d4edda; color: #155724; font-weight: bold;"] * len(row)
    elif row.get("Es Actualizada", False):
        return ["background-color: #cce5ff; color: #004085; font-weight: bold;"] * len(row)
    return [""] * len(row)


def aplicar_filtros_comunes(df: pd.DataFrame, scores_bases: dict = None) -> pd.DataFrame:
    if df.empty:
        return df

    # 1. Fuente
    if filtro_fuente:
        df = df[
            df["fuente_origen"]
            .fillna("")
            .astype(str)
            .apply(lambda x: any(f.strip().casefold() in x.casefold() for f in filtro_fuente))
        ]

    # 2. Ámbito
    if filtro_ambito:
        df = df[df["ambito"].isin(filtro_ambito)]

    # 3. Importes
    if importe_min > 0:
        df = df[df["presupuesto_total"] >= importe_min]
    if importe_max > 0:
        df = df[df["presupuesto_total"] <= importe_max]

    # 4. CCAA
    if filtro_ccaa:
        seleccion = set(filtro_ccaa)
        df = df[df["ccaa"].apply(lambda lst: bool(set(lst or []) & seleccion))]

    # 5. Beneficiarios (desplegable inteligente)
    if filtro_beneficiario_opcion:
        seleccion = set(filtro_beneficiario_opcion)
        def cumple_beneficiarios(val):
            if not val or pd.isna(val):
                return False
            partes = [p.strip().casefold() for p in str(val).replace(";", ",").split(",")]
            return any(s.casefold() in partes for s in seleccion)
        df = df[df["beneficiarios"].apply(cumple_beneficiarios)]

    # 6. Título de bases reguladoras (búsqueda híbrida semántica + léxica,
    # resuelta ENTERAMENTE en Postgres vía la función RPC
    # buscar_por_bases_reguladoras -- ver search.py. `scores_bases` ya
    # viene calculado desde procesar_resultados con UNA sola llamada;
    # aquí solo se cruza por codigo_unico, sin ningún cálculo de
    # embeddings en este bucle -- eso es lo que antes causaba la
    # lentitud al combinar los dos filtros de texto.)
    if scores_bases:
        df["score_bases"] = df["codigo_unico"].map(scores_bases).fillna(0.0)
        df = df[df["score_bases"] > 0.0]

    # 7. Tipo de convocatoria
    if filtro_tipo_convocatoria:
        seleccion = set(filtro_tipo_convocatoria)
        df = df[df["tipo_convocatoria"].apply(lambda lst: bool(set(lst or []) & seleccion))]

    # 8. Fecha de cierre
    def filtrar_fecha_fin(f_str):
        if pd.isna(f_str) or not str(f_str).strip():
            return True
        try:
            return date.fromisoformat(str(f_str)[:10]) >= fecha_cierre_tope
        except (ValueError, TypeError):
            return True

    if "fecha_fin_solicitud" in df.columns:
        df = df[df["fecha_fin_solicitud"].apply(filtrar_fecha_fin)]

    # 9. Fecha de publicación
    def filtrar_fecha_pub(f_str):
        if pd.isna(f_str) or not str(f_str).strip():
            return False
        try:
            fecha_pub = date.fromisoformat(str(f_str)[:10])
            return f_inicio <= fecha_pub <= f_fin
        except (ValueError, TypeError):
            return False

    if "fecha_publicacion" in df.columns:
        df = df[df["fecha_publicacion"].apply(filtrar_fecha_pub)]

    return df


def procesar_resultados(resultados: list, contexto: str, texto_bases_busqueda: str = ""):
    """Aplica filtros, calcula relevancia combinada, trunca al límite y guarda en session_state."""
    if not resultados:
        st.session_state.df_resultados = None
        st.session_state.mensaje_estado = ""
        return f"No se encontraron {contexto}."

    df = pd.DataFrame(resultados)

    if "es_novedad" in df.columns and "es_actualizada" in df.columns:
        df = df[(df["es_novedad"] == True) | (df["es_actualizada"] == True)] if contexto == "novedades" else df

    # Asignar relevancia inicial basada en la búsqueda semántica general de Supabase
    if "similarity" in df.columns:
        df["relevancia_general"] = df["similarity"].fillna(0.0)
    else:
        df["relevancia_general"] = 1.0 if not consulta_texto.strip() else 0.5

    # Búsqueda híbrida de "Título de bases reguladoras": UNA sola llamada
    # a Postgres (vía RPC) para todo el DataFrame, no un bucle en Python
    # fila a fila -- ver search.buscar_por_bases_reguladoras.
    scores_bases = None
    if texto_bases_busqueda and texto_bases_busqueda.strip():
        scores_bases = buscar_por_bases_reguladoras(supabase, encoder, texto_bases_busqueda)

    # Aplicar filtros (incluyendo el filtrado por puntuación de bases reguladoras)
    df = aplicar_filtros_comunes(df, scores_bases)

    if df.empty:
        st.session_state.df_resultados = None
        st.session_state.mensaje_estado = ""
        return f"No hay {contexto} que coincidan con los filtros y la búsqueda indicada."

    # Combinación inteligente de niveles de relevancia (Búsqueda General + Filtro de Bases)
    if "score_bases" in df.columns and texto_bases_busqueda.strip():
        # Combinamos ponderando ambas notas (ej. 60% búsqueda general de IA + 40% coincidencia en bases)
        df["relevancia_final"] = (df["relevancia_general"] * 0.6) + (df["score_bases"] * 0.4)
    else:
        df["relevancia_final"] = df["relevancia_general"]

    # Convertir a porcentaje final para mostrar en la tabla y ordenar de mayor a menor relevancia
    df["relevancia"] = (df["relevancia_final"] * 100).round(2)
    df = df.sort_values(by="relevancia", ascending=False)

    if not st.session_state.mostrar_todos:
        total_encontrados = len(df)
        df = df.head(st.session_state.limite_resultados)
        mostrados = len(df)
        if total_encontrados > mostrados:
            mensaje = f"¡Mostrando las **{mostrados} subvenciones más relevantes** de un total de **{total_encontrados}** encontradas!"
        else:
            mensaje = f"¡Se han encontrado y mostrado las {mostrados} subvenciones relevantes!"
    else:
            mensaje = f"¡Se han encontrado y mostrado las {len(df)} subvenciones relevantes!"

    st.session_state.df_resultados = construir_tabla_final(df)
    st.session_state.mensaje_estado = mensaje
    return None


def construir_tabla_final(df: pd.DataFrame) -> pd.DataFrame:
    tabla_final = []
    for idx, row in enumerate(df.itertuples(), start=1):
        ccaa_valor = getattr(row, "ccaa", None) or []
        importe_valor = getattr(row, "presupuesto_total", None)
        bases_valor = getattr(row, "titulo_bases_reguladoras", None)
        convocatoria_valor = getattr(row, "tipo_convocatoria", None)

        bases_str = ", ".join(bases_valor) if isinstance(bases_valor, list) else (str(bases_valor) if bases_valor else "No especificado")
        convocatoria_str = ", ".join(convocatoria_valor) if isinstance(convocatoria_valor, list) else (str(convocatoria_valor) if convocatoria_valor else "No especificado")

        tabla_final.append({
            "#": idx,
            "Relevancia (%)": f"{getattr(row, 'relevancia', 100.0):.2f} %",
            "Título": row.titulo,
            "Organismo": getattr(row, "organismo", None) or "No especificado",
            "Ámbito": str(getattr(row, "ambito", "") or "No especificado").title(),
            "CCAA": ", ".join(ccaa_valor) if ccaa_valor else "Nacional / No aplica",
            "Beneficiarios": getattr(row, "beneficiarios", None) or "No especificado",
            "Bases Reguladoras": bases_str,
            "Tipo Convocatoria": convocatoria_str,
            "Cierre": getattr(row, "fecha_fin_solicitud", None) or "No especificada",
            "Fecha Pub.": getattr(row, "fecha_publicacion", None) or "No especificada",
            "Importe": (
                f"{importe_valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"
                if importe_valor is not None and not pd.isna(importe_valor)
                else "No especificado"
            ),
            "Enlace": row.url_oficial,
            "Es Novedad": getattr(row, "es_novedad", False),
            "Es Actualizada": getattr(row, "es_actualizada", False),
        })
    return pd.DataFrame(tabla_final)

# 3. Lógica del botón de Novedades
if btn_novedades:
    with st.spinner("Buscando en novedades y actualizaciones..."):
        if consulta_texto.strip():
            resultados = buscar_semantica(supabase, encoder, consulta_texto)
        else:
            resultados = listar_novedades(supabase)

        aviso = procesar_resultados(resultados, "novedades", filtro_titulo_bases_texto)
        if aviso:
            st.warning(aviso)

# 4. Lógica de búsqueda principal
elif btn_buscar:
    with st.spinner("Buscando en Supabase..."):
        if consulta_texto.strip():
            resultados = buscar_semantica(supabase, encoder, consulta_texto)
        else:
            resultados = listar_todas(supabase)

        aviso = procesar_resultados(resultados, "subvenciones", filtro_titulo_bases_texto)
        if aviso:
            st.warning(aviso)


# 5. Renderizado persistente de resultados
if st.session_state.df_resultados is not None and not st.session_state.df_resultados.empty:
    if st.session_state.mensaje_estado:
        st.success(st.session_state.mensaje_estado)

    st.markdown("🟢 *Verde*: Subvenciones nuevas | 🔵 *Azul*: Subvenciones actualizadas")

    st.dataframe(
        st.session_state.df_resultados.style.apply(estilizar_filas, axis=1),
        column_config={
            "Enlace": st.column_config.LinkColumn(
                "Enlace oficial", display_text="Ver convocatoria 🔗"
            ),
            "Es Novedad": None,
            "Es Actualizada": None,
        },
        hide_index=True,
        use_container_width=True,
    )
