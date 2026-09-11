"""
config.py
---------
Configuración y constantes compartidas por la app de Streamlit.

Las credenciales se leen primero de `st.secrets` (lo recomendado al
desplegar en Streamlit Community Cloud) y, si no existen ahí, de las
variables de entorno (cómodo para desarrollo local con un `.env`).
"""
import os

import streamlit as st


def _config(clave: str, por_defecto: str = "") -> str:
    try:
        valor = st.secrets.get(clave)
        if valor:
            return valor
    except Exception:
        pass
    return os.getenv(clave, por_defecto)


# La app SOLO lee datos: usa siempre la clave ANÓNIMA (anon/public) de
# Supabase. La Service Role Key (con permisos de escritura) vive
# exclusivamente en los GitHub Actions de ingesta — ver ingest/common.py.
SUPABASE_URL = _config("SUPABASE_URL")
SUPABASE_ANON_KEY = _config("SUPABASE_ANON_KEY")

# Debe coincidir con el modelo usado en ingest/common.py y con el
# `vector(384)` definido en sql/schema.sql.
MODELO_EMBEDDING = "intfloat/multilingual-e5-small"

CCAA_DISPONIBLES = [
    "Andalucía", "Aragón", "Asturias", "Baleares", "Canarias", "Cantabria",
    "Castilla-La Mancha", "Castilla y León", "Cataluña", "Comunidad Valenciana",
    "Extremadura", "Galicia", "La Rioja", "Madrid", "Murcia", "Navarra",
    "País Vasco", "Ceuta", "Melilla",
]

# Debe coincidir EXACTAMENTE con las claves de CATEGORIAS_PALABRAS_CLAVE
# en ingest/common.py: son las etiquetas que de verdad se guardan en la
# columna `categorias`, así que el filtro solo tiene sentido si usa el
# mismo vocabulario.
CATEGORIAS_BASE = [
    "Digitalización", "I+D+i", "Emprendimiento", "Internacionalización",
    "Empleo y formación", "Igualdad y conciliación", "Cultura", "Deporte",
    "Medio ambiente y sostenibilidad", "Energía", "Turismo", "Comercio",
    "Industria", "Agricultura y pesca", "Vivienda", "Educación",
    "Servicios sociales", "Movilidad y transporte", "Juventud",
]

AMBITOS_DISPONIBLES = ["Nacional", "Autonómico", "Local"]
