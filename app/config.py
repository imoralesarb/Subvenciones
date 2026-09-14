"""
config.py
---------
Configuración y constantes compartidas por la app de Streamlit.

Las credenciales se leen primero de `st.secrets` (lo recomendado al
desplegar en Streamlit Community Cloud) y, si no existen ahí, de las
variables de entorno (cómodo para desarrollo local con un `.env`).

Nota: las opciones de los filtros de Ámbito y CCAA NO están aquí como
listas fijas — se calculan en tiempo real a partir de los valores que de
verdad hay en la tabla (ver `search.obtener_opciones_filtro`), porque la
BDNS no documenta un vocabulario cerrado para esos campos (p. ej. el
ámbito puede venir como "LOCAL", y la CCAA como "ES130 - Cantabria").
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

FUENTES_DISPONIBLES = ["BDNS", "BOE", "Funding & Tenders"]
