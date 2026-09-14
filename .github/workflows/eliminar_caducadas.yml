name: Eliminar subvenciones caducadas

on:
  schedule:
    # Todos los días a las 00:30 UTC
    - cron: "30 0 * * *"

  # Permite ejecutarlo manualmente desde GitHub
  workflow_dispatch:

jobs:
  eliminar-caducadas:
    runs-on: ubuntu-latest

    steps:
      - name: Instalar dependencias
        run: |
          pip install supabase

      - name: Eliminar subvenciones caducadas
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
        run: |
          python - <<'PY'
          import os
          from datetime import date
          from supabase import create_client

          SUPABASE_URL = os.environ["SUPABASE_URL"]
          SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

          supabase = create_client(
              SUPABASE_URL,
              SUPABASE_SERVICE_KEY
          )

          hoy = date.today().isoformat()

          print(f"🗑️ Eliminando subvenciones caducadas antes de {hoy}...")

          respuesta = (
              supabase
              .table("subvenciones")
              .delete()
              .lt("fecha_fin_solicitud", hoy)
              .execute()
          )

          print("✅ Subvenciones caducadas eliminadas correctamente.")
          print(respuesta)
          PY
