from datetime import date, timedelta

import pandas as pd
import streamlit as st

from config import FUENTES_DISPONIBLES
from db import obtener_cliente, obtener_encoder
from search import buscar_semantica, listar_novedades, listar_todas, obtener_opciones_filtro

# Desactivar traductor automático del navegador
st.markdown(
    """
    <head>
        <meta name="google" content="notranslate">
    </head>
    """,
    unsafe_allow_html=True,
)

# Configurar la página de Streamlit
st.set_page_config(
    page_title="Buscador inteligente de Subvenciones",
    page_icon="💶",
    layout="wide",
)

# --- ESTILOS CSS PERSONALIZADOS PARA DISEÑO Y RECUADROS ---
st.markdown(
    """
    <style>
        /* Reducir el tamaño de toda la interfaz */
        .stApp {
            zoom: 0.75;
        }
        div.stButton > button:first-child {
            background-color: #0066cc;
            color: white;
            font-weight: bold;
            font-size: 16px;
            padding: 0.6rem 1.2rem;
            border-radius: 8px;
            border: none;
            width: 100%;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            transition: all 0.3s ease;
        }
        div.stButton > button:first-child:hover {
            background-color: #0052a3;
            box-shadow: 0 6px 8px rgba(0, 0, 0, 0.15);
            color: white;
        }
        .results-container {
            background-color: #f8f9fa;
            border: 1px solid #e0e0e0;
            border-radius: 10px;
            padding: 20px;
            margin-top: 15px;
            margin-bottom: 15px;
        }
        .alignment-fix {
            display: flex;
            align-items: center;
            height: 100%;
            font-size: 15px;
            color: #31333F;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# 1. Conexión a Supabase y modelo de IA
supabase = obtener_cliente()

with st.spinner("Cargando modelo de IA..."):
    encoder = obtener_encoder()

# Opciones de Ámbito/CCAA calculadas a partir de los datos reales (ver search.py)
AMBITOS_DISPONIBLES, CCAA_DISPONIBLES = obtener_opciones_filtro(supabase)

# Inicializar estados de sesión para persistencia de resultados
if "df_resultados" not in st.session_state:
    st.session_state.df_resultados = None
if "mensaje_estado" not in st.session_state:
    st.session_state.mensaje_estado = ""

# 2. Interfaz Visual y Gestión de Estado
st.title("💶 Buscador inteligente de Subvenciones")
st.caption(
    "Datos oficiales de la BDNS (Base de Datos Nacional de Subvenciones) "
    "y del BOE (Boletín Oficial del Estado)."
)


def limpiar_campos():
    st.session_state.consulta_texto = ""
    st.session_state.filtro_fuente = []
    st.session_state.filtro_ambito = []
    st.session_state.filtro_ccaa = []
    st.session_state.filtro_beneficiarios = ""
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
    filtro_fuente = st.multiselect(
        "🌐 Fuente",
        FUENTES_DISPONIBLES,
        default=[],
        key="filtro_fuente",
    )
with col_ambito:
    filtro_ambito = st.multiselect(
        "🏛️ Ámbito",
        AMBITOS_DISPONIBLES,
        default=[],
        key="filtro_ambito",
    )
with col1:
    importe_min = st.number_input("Importe Mínimo (€)", value=0.0, key="importe_min")
with col2:
    importe_max = st.number_input("Importe Máximo (€)", value=0.0, key="importe_max")
with col3:
    filtro_ccaa = st.multiselect(
        "📍 Comunidad Autónoma",
        CCAA_DISPONIBLES,
        default=[],
        key="filtro_ccaa",
    )

# Beneficiarios (texto libre)
filtro_beneficiarios = st.text_input(
    "👥 Beneficiarios (texto libre)",
    placeholder="ej. autónomos, pymes, entidades sin ánimo de lucro...",
    key="filtro_beneficiarios",
)

# Fila de fechas
col_fecha_fin, col_rango = st.columns([1, 2])

with col_fecha_fin:
    fecha_cierre_tope = st.date_input(
        "⏳ Fecha fin de presentación (Mínima)",
        value=date.today() - timedelta(days=1),
        key="fecha_cierre_tope",
    )

with col_rango:
    col_desde, col_hasta = st.columns(2)
    with col_desde:
        f_inicio = st.date_input(
            "📅 Rango publicación (Desde)",
            value=date(2026, 1, 1),
            key="f_inicio",
        )
    with col_hasta:
        f_fin = st.date_input(
            "📅 Rango publicación (Hasta)",
            value=date(2100, 12, 31),
            key="f_fin",
        )

col_resultados, col_vacio = st.columns([60, 40])

with col_resultados:
    with st.container(border=True):
        st.markdown(
            "<strong>¿Cuántos resultados quieres ver?</strong>",
            unsafe_allow_html=True,
        )

        if "mostrar_todos" not in st.session_state:
            st.session_state.mostrar_todos = True
        if "limite_resultados" not in st.session_state:
            st.session_state.limite_resultados = 10

        def actualizar_slider():
            st.session_state.mostrar_todos = False

        def actualizar_checkbox():
            pass

        col_res_chk, col_res_texto, col_res_slider = st.columns([2.5, 2, 4])

        with col_res_chk:
            mostrar_todos = st.checkbox(
                "Mostrar TODOS los resultados",
                key="mostrar_todos",
                on_change=actualizar_checkbox,
            )

        color_texto = "gray" if mostrar_todos else "inherit"

        with col_res_texto:
            st.markdown(
                f'<div style="font-size: 15px; padding-top: 8px; color: {color_texto};">'
                "Seleccionar número de resultados:"
                "</div>",
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
    btn_buscar = st.button(
        "🔍 Buscar subvenciones", type="primary", use_container_width=True
    )

with col_btn_novedades:
    btn_novedades = st.button(
        "✨ Novedades",
        type="secondary",
        use_container_width=True,
    )

with col_btn_limpiar:
    btn_limpiar = st.button(
        "🔄 Limpiar Filtros",
        on_click=limpiar_campos,
        type="secondary",
        use_container_width=True,
    )


def estilizar_filas(row):
    if row.get("Es Novedad", False):
        return ["background-color: #d4edda; color: #155724; font-weight: bold;"] * len(row)
    elif row.get("Es Actualizada", False):
        return ["background-color: #cce5ff; color: #004085; font-weight: bold;"] * len(row)
    return [""] * len(row)


def aplicar_filtros_comunes(df: pd.DataFrame) -> pd.DataFrame:
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

    # 5. Beneficiarios (texto libre)
    if filtro_beneficiarios.strip():
        df = df[
            df["beneficiarios"].str.contains(
                filtro_beneficiarios.strip(), case=False, na=False
            )
        ]

    # 6. Fecha de cierre
    def filtrar_fecha_fin(f_str):
        if pd.isna(f_str) or not str(f_str).strip():
            return True  # sin fecha de cierre especificada -> no se excluye
        try:
            return date.fromisoformat(str(f_str)[:10]) >= fecha_cierre_tope
        except (ValueError, TypeError):
            return True

    if "fecha_fin_solicitud" in df.columns:
        df = df[df["fecha_fin_solicitud"].apply(filtrar_fecha_fin)]

    # 7. Fecha de publicación
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


def construir_tabla_final(df: pd.DataFrame) -> pd.DataFrame:
    tabla_final = []
    for idx, row in enumerate(df.itertuples(), start=1):
        ccaa_valor = getattr(row, "ccaa", None) or []
        importe_valor = getattr(row, "presupuesto_total", None)

        tabla_final.append({
            "#": idx,
            "Relevancia (%)": f"{getattr(row, 'relevancia', 100.0):.2f} %",
            "Título": row.titulo,
            "Organismo": getattr(row, "organismo", None) or "No especificado",
            "Ámbito": (getattr(row, "ambito", None) or "No especificado").title(),
            "CCAA": ", ".join(ccaa_valor) if ccaa_valor else "Nacional / No aplica",
            "Beneficiarios": getattr(row, "beneficiarios", None) or "No especificado",
            "Cierre": getattr(row, "fecha_fin_solicitud", None) or "No especificada",
            "Fecha Pub.": getattr(row, "fecha_publicacion", None) or "No especificada",
            "Importe": (
                f"{importe_valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"
                if importe_valor is not None and not pd.isna(importe_valor)
                else "No especificado"
            ),
            "Fuente": row.fuente_origen,
            "Enlace": row.url_oficial,
            "Es Novedad": getattr(row, "es_novedad", False),
            "Es Actualizada": getattr(row, "es_actualizada", False),
        })
    return pd.DataFrame(tabla_final)


def procesar_resultados(resultados: list, contexto: str):
    """Aplica filtros, trunca al límite elegido y guarda el resultado en session_state."""
    if not resultados:
        st.session_state.df_resultados = None
        st.session_state.mensaje_estado = ""
        return f"No se encontraron {contexto}."

    df = pd.DataFrame(resultados)

    if "es_novedad" in df.columns and "es_actualizada" in df.columns:
        df = df[(df["es_novedad"] == True) | (df["es_actualizada"] == True)] if contexto == "novedades" else df

    if "similarity" in df.columns:
        df["relevancia"] = (df["similarity"] * 100).round(2)
    else:
        df["relevancia"] = 100.0

    df = aplicar_filtros_comunes(df)

    if df.empty:
        st.session_state.df_resultados = None
        st.session_state.mensaje_estado = ""
        return f"No hay {contexto} que coincidan con los filtros y la búsqueda indicada."

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


# 3. Lógica del botón de Novedades
if btn_novedades:
    with st.spinner("Buscando en novedades y actualizaciones..."):
        if consulta_texto.strip():
            resultados = buscar_semantica(supabase, encoder, consulta_texto)
        else:
            resultados = listar_novedades(supabase)

        aviso = procesar_resultados(resultados, "novedades")
        if aviso:
            st.warning(aviso)

# 4. Lógica de búsqueda principal
elif btn_buscar:
    with st.spinner("Buscando en Supabase..."):
        if consulta_texto.strip():
            resultados = buscar_semantica(supabase, encoder, consulta_texto)
        else:
            resultados = listar_todas(supabase)

        aviso = procesar_resultados(resultados, "subvenciones")
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
    csv = st.session_state.df_resultados.drop(
        columns=["Es Novedad", "Es Actualizada"],
        errors="ignore"
    ).to_csv(index=False).encode("utf-8")
    
    st.download_button("⬇️ Descargar resultados (CSV)", csv, "subvenciones.csv", "text/csv")
