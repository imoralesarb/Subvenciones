"""
app.py
------
Buscador y gestor de subvenciones — punto de entrada de Streamlit.

Ejecutar en local:
    cd app
    streamlit run app.py

Requiere SUPABASE_URL y SUPABASE_ANON_KEY (ver README.md, sección 2).
"""
import streamlit as st

from config import AMBITOS_DISPONIBLES, CATEGORIAS_BASE, CCAA_DISPONIBLES
from db import obtener_cliente, obtener_encoder
from search import buscar_subvenciones, formatear_para_tabla

st.set_page_config(
    page_title="Buscador de Subvenciones",
    page_icon="💶",
    layout="wide",
)

st.markdown(
    """
    <style>
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
        }
        div.stButton > button:first-child:hover {
            background-color: #0052a3;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("💶 Buscador inteligente de Subvenciones")
st.caption(
    "Datos oficiales de la BDNS (Base de Datos Nacional de Subvenciones) "
    "y del BOE (Boletín Oficial del Estado)."
)

supabase = obtener_cliente()

with st.spinner("Cargando el modelo de búsqueda semántica..."):
    encoder = obtener_encoder()

# ------------------------------------------------------------------
# Filtros (barra lateral)
# ------------------------------------------------------------------
with st.sidebar:
    st.header("Filtros")

    consulta_texto = st.text_input(
        "Búsqueda en lenguaje natural",
        placeholder="p. ej. ayudas a la digitalización de pymes en Canarias",
        help="Deja este campo vacío para listar convocatorias por fecha en vez de por relevancia semántica.",
    )

    categorias_sel = st.multiselect("Categoría", CATEGORIAS_BASE)
    ambito_sel = st.selectbox("Ámbito", ["Todos"] + AMBITOS_DISPONIBLES)
    ccaa_sel = st.multiselect("Comunidad Autónoma", CCAA_DISPONIBLES)

    st.markdown("**Importe (€)**")
    col_min, col_max = st.columns(2)
    with col_min:
        importe_min = st.number_input("Mínimo", min_value=0, value=0, step=1000)
    with col_max:
        importe_max = st.number_input("Máximo (0 = sin límite)", min_value=0, value=0, step=1000)

    solo_vigentes = st.checkbox("Solo convocatorias con plazo abierto", value=True)
    solo_novedades = st.checkbox("Solo novedades y actualizaciones recientes", value=False)

    fuente_sel = st.selectbox("Fuente", ["Todas", "BDNS", "BOE"])

    limite_resultados = st.slider("Nº máximo de resultados", 25, 500, 150, step=25)

    buscar_click = st.button("🔍 Buscar", use_container_width=True)

# ------------------------------------------------------------------
# Ejecución de la búsqueda
# ------------------------------------------------------------------
if "df_resultados" not in st.session_state:
    st.session_state.df_resultados = None

if buscar_click:
    with st.spinner("Buscando en Supabase..."):
        df = buscar_subvenciones(
            supabase,
            encoder,
            texto=consulta_texto,
            categorias=categorias_sel,
            ambito=None if ambito_sel == "Todos" else ambito_sel,
            ccaa=ccaa_sel,
            fuente=None if fuente_sel == "Todas" else fuente_sel,
            importe_min=importe_min or None,
            importe_max=importe_max or None,
            solo_vigentes=solo_vigentes,
            solo_novedades=solo_novedades,
            limite=limite_resultados,
        )
        st.session_state.df_resultados = df

# ------------------------------------------------------------------
# Resultados
# ------------------------------------------------------------------
df = st.session_state.df_resultados

if df is None:
    st.info("Define tus filtros y pulsa **Buscar** para empezar.")
elif df.empty:
    st.warning("No se han encontrado subvenciones que coincidan con los filtros indicados.")
else:
    st.success(f"Se han encontrado **{len(df)}** convocatorias.")

    tabla = formatear_para_tabla(df)
    st.dataframe(
        tabla,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Enlace": st.column_config.LinkColumn("Enlace", display_text="Ver convocatoria"),
            "Relevancia (%)": st.column_config.ProgressColumn(
                "Relevancia (%)", min_value=0, max_value=100, format="%.1f%%"
            ),
        },
    )

    csv = tabla.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Descargar resultados (CSV)",
        csv,
        "subvenciones.csv",
        "text/csv",
        use_container_width=False,
    )
