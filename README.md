# Buscador de Subvenciones — Streamlit + Supabase

Réplica, adaptada al dominio de **subvenciones y ayudas públicas**, de la
arquitectura del proyecto de referencia `Licitaciones-main` (Streamlit +
Supabase + pgvector + scripts de ingesta programados con GitHub Actions).

Fuentes de datos: **BDNS** (Base de Datos Nacional de Subvenciones, fuente
primaria) y **BOE** (Boletín Oficial del Estado, fuente complementaria
para el enlace normativo oficial).

```
subvenciones-app/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── .streamlit/
│   └── secrets.toml.example
├── sql/
│   └── schema.sql              # tabla, índices, RLS y función de búsqueda
├── app/                        # aplicación Streamlit (solo lectura)
│   ├── app.py
│   ├── config.py
│   ├── db.py
│   └── search.py
├── ingest/                     # scripts de ingesta (solo escritura, backend)
│   ├── common.py
│   ├── ingesta_bdns.py
│   └── ingesta_boe.py
└── .github/workflows/
    ├── sincronizar_bdns.yml
    └── sincronizar_boe.yml
```

Principio de diseño que recorre todo el proyecto: **la app solo lee, los
scripts de ingesta son los únicos que escriben**. Por eso hay dos claves de
Supabase distintas (`SUPABASE_ANON_KEY` para la app, `SUPABASE_SERVICE_KEY`
para la ingesta) y una política de Row Level Security que solo permite
`SELECT` a la clave anónima.


## 1. Configuración de Supabase

1. Crea un proyecto en [supabase.com](https://supabase.com) (plan gratuito
   de sobra para empezar).
2. Ve a **SQL Editor -> New query**, pega el contenido de
   [`sql/schema.sql`](sql/schema.sql) y ejecútalo. Esto crea:
   - la extensión `pgvector`,
   - la tabla `subvenciones` (título, organismo, fechas de plazo, importe,
     descripción, ámbito, CCAA y **categorías** como columnas de etiquetas,
     más los campos de trazabilidad de la fuente),
   - los índices (incluido el índice vectorial HNSW para la búsqueda
     semántica),
   - la Row Level Security (lectura pública, escritura solo con la clave
     de servicio),
   - la función `buscar_subvenciones`, que hace de "buscador avanzado":
     combina similitud semántica con los filtros de categoría, ámbito,
     CCAA, importe y plazo vigente, todo resuelto en la base de datos.
3. Copia tus credenciales desde **Project Settings -> API**:
   - `Project URL` -> `SUPABASE_URL`
   - `anon` `public` key -> `SUPABASE_ANON_KEY` (para la app)
   - `service_role` key -> `SUPABASE_SERVICE_KEY` (para la ingesta —
     **trátala como una contraseña de administrador**, nunca la pongas en
     el frontend ni la subas al repositorio)

¿Por qué esta estructura de tabla y no una más simple? Porque además de los
campos que pediste (título, organismo, fecha límite, importe, descripción,
categorías) hacen falta columnas para:
- **deduplicar** entre dos fuentes distintas (`codigo_unico`, `codigo_bdns`),
- **filtrar por plazo vigente sin borrar nada** (`fecha_fin_solicitud` se
  compara con `current_date` en cada consulta, en vez de tener un proceso
  aparte que borre convocatorias caducadas como hace el proyecto de
  licitaciones de referencia — para subvenciones no hace falta: el filtro
  de "plazo vigente" ya resuelve el mismo problema de forma más segura,
  sin riesgo de borrar datos históricos útiles),
- **saber qué ha cambiado** (`es_novedad`, `es_actualizada`, útil para una
  vista de "novedades" o para alertas por email/Slack más adelante).


## 2. Estructura de Streamlit y conexión segura

La app vive en `app/` y se ejecuta así:

```bash
pip install -r requirements.txt
cd app
streamlit run app.py
```

En **desarrollo local**, Streamlit lee `.streamlit/secrets.toml`. Copia la
plantilla y rellénala:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edítalo con tu SUPABASE_URL y SUPABASE_ANON_KEY
```

`app/config.py` también admite variables de entorno como alternativa
(`.env` + `python-dotenv`), útil si prefieres ese flujo.

En **producción** (p. ej. Streamlit Community Cloud):
1. Sube el repositorio a GitHub (con `.gitignore` ya excluyendo los
   secretos).
2. En Streamlit Community Cloud, apunta la app a `app/app.py`.
3. En **Settings -> Secrets** de la app, pega el mismo contenido que
   `.streamlit/secrets.toml.example` pero con los valores reales.

Importante: la app usa **siempre** `SUPABASE_ANON_KEY`. Nunca despliegues
la app con la `service_role` key: al ser pública, cualquiera podría leer
esa clave desde el navegador e inyectar/borrar datos, porque esa clave
ignora la Row Level Security.


## 3. El buscador (código modular)

- **`app/db.py`**: conecta con Supabase y carga el modelo de embeddings
  (`intfloat/multilingual-e5-small`, multilingüe y ligero), ambos
  cacheados con `st.cache_resource` para no recargarlos en cada
  interacción.
- **`app/search.py`**: construye los parámetros y llama a la función RPC
  `buscar_subvenciones` de Supabase — ver función `buscar_subvenciones()`.
  Si el usuario escribe una consulta en lenguaje natural, se calcula su
  embedding y se ordena por similitud; si no, se listan/filtran por fecha.
  Todos los filtros (categoría, importe, ámbito, CCAA, fuente, plazo
  vigente, novedades) se resuelven **en SQL**, no con pandas en memoria:
  así la app sigue siendo rápida cuando la tabla crezca.
- **`app/app.py`**: la interfaz — barra lateral con los filtros, tabla de
  resultados con enlace a la convocatoria oficial y descarga en CSV.
- **`app/config.py`**: constantes (lista de CCAA, categorías disponibles,
  credenciales). Las categorías deben coincidir con las que genera
  `ingest/common.py` — están documentadas ahí para que no se desincronicen.

Puedes extender el buscador añadiendo parámetros nuevos a la función SQL
`buscar_subvenciones` (por ejemplo, un filtro por `beneficiarios` cuando
tengas ese dato bien poblado) sin tocar la lógica de embeddings.


## 4. Adaptación de los dos scripts de ejemplo

Los dos scripts que aportaste (`ingesta_bdns.py`, `ingesta_boe.py`) estaban
escritos para un plugin de WordPress: descargaban/parseaban los datos y
hacían `POST` a un endpoint REST propio, que era quien finalmente escribía
en base de datos. Aquí se han adaptado para escribir **directamente** en
Supabase, siguiendo el mismo patrón que los `sincronizar_<fuente>.py` del
proyecto de licitaciones de referencia. Cambios concretos:

| Original (WordPress) | Adaptado (Supabase) |
|---|---|
| `enviar_lote_a_wordpress()` hace `POST` a `WP_ENDPOINT` con `X-GF-API-KEY` | `subir_en_lotes()` (en `ingest/common.py`) hace `upsert(..., on_conflict="codigo_unico")` directamente contra la tabla `subvenciones`, con reintentos |
| El JSON normalizado no incluía embedding ni categorías | `normalizar_convocatoria()` / `normalizar_item_boe()` añaden `texto_completo`, `embedding` (384-d, `multilingual-e5-small`) y `categorias` (heurística por palabras clave en `ingest/common.py`, documentada ahí porque no hay taxonomía homogénea fiable en la fuente) |
| Sin lógica de "qué ha cambiado" | Se compara cada convocatoria con lo ya existente en Supabase (por `codigo_unico`) para marcar `es_novedad` / `es_actualizada` y **no recalcular embeddings si no hace falta** |
| BOE y BDNS eran independientes; solo se enlazaban por `codigo_bdns` en el JSON | Igual en espíritu, pero ahora la fusión ocurre en Postgres: si `ingesta_boe.py` encuentra un `codigo_unico` que ya existe (creado por `ingesta_bdns.py`), **no lo sobrescribe**: solo añade `url_boe` y añade "BOE" a `fuente_origen` |
| Persistía un cursor de paginación en `last_run.json` (pero no llegaba a usarse para reanudar, ni sobreviviría a un runner efímero de GitHub Actions) | Se ha retirado esa complejidad no funcional: al ser `upsert` idempotente y la BDNS devolver siempre lo más reciente primero, un re-escaneo diario de las últimas ~2000 convocatorias es correcto sin estado persistente (ver comentario al principio de `ingesta_bdns.py`) |
| Las funciones de utilidad (cliente, API key) se repetían en cada script | Extraídas a `ingest/common.py`: conexión a Supabase, carga del modelo de embeddings, clasificador de categorías, consulta de existentes y subida en lotes — reutilizado por ambos scripts |

El resto de la lógica de descarga/parseo (paginación de la BDNS, parseo
del XML del sumario del BOE, expresiones regulares para detectar el
número BDNS citado en un anuncio del BOE) se mantiene tal cual la
aportaste: es correcta y ya seguía buenas prácticas (ritmo de consulta
responsable, manejo de fines de semana sin BOE, etc.).


## 5. Automatización con GitHub Actions

En **Settings -> Secrets and variables -> Actions** de tu repositorio,
añade:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY` (la de `service_role`, **no** la anónima)

Los workflows `.github/workflows/sincronizar_bdns.yml` y
`sincronizar_boe.yml` ya están configurados para ejecutarse a diario
(puedes lanzarlos también a mano desde la pestaña **Actions ->
Run workflow**). Ajusta los horarios (`cron`, en UTC) a tu gusto.


## 6. Probar la ingesta en local antes de programarla

```bash
pip install -r requirements.txt
export SUPABASE_URL="https://TU-PROYECTO.supabase.co"
export SUPABASE_SERVICE_KEY="tu_clave_service_role"

cd ingest
python ingesta_bdns.py
python ingesta_boe.py            # usa la fecha de hoy
python ingesta_boe.py 20260910   # o una fecha concreta (AAAAMMDD)
```

La primera ejecución tardará algo más porque `sentence-transformers`
descarga el modelo (~120 MB) la primera vez que se usa; luego queda en
caché local (o en la caché del runner de GitHub Actions, gracias a
`cache: 'pip'` en los workflows — si quieres cachear también el propio
modelo descargado, añade una acción `actions/cache` apuntando a
`~/.cache/huggingface`).


## 7. Próximos pasos razonables

- **Enriquecer `beneficiarios` / `empleados_min` / `antiguedad_min_anios`**:
  hoy se guardan a `None` porque la API de búsqueda de la BDNS no siempre
  los da estructurados. El propio detalle de cada convocatoria
  (`GET /convocatorias?numConv=...`) puede traer más campos — añadir una
  llamada de detalle solo para las convocatorias nuevas es factible sin
  disparar el número de peticiones.
- **Alertas**: con `es_novedad` / `es_actualizada` ya poblados, es sencillo
  añadir un script que consulte esas filas y envíe un resumen diario por
  email o Slack.
- **Categorías**: sustituir/afinar la heurística de `ingest/common.py` por
  una clasificación más precisa (LLM o mapeo manual del catálogo de
  `sectores`/`instrumentos` de la BDNS) si el filtro por categoría se
  queda corto.
