-- ============================================================
-- schema.sql — Buscador de Subvenciones
-- ============================================================
-- Cómo ejecutarlo:
--   Supabase Dashboard -> SQL Editor -> New query -> pegar y ejecutar.
--   Es idempotente (usa IF NOT EXISTS / OR REPLACE / DROP...IF EXISTS),
--   así que se puede volver a ejecutar sin problemas si necesitas
--   reaplicar cambios.
--
-- Dimensión del vector: 384, porque la app y los scripts de ingesta usan
-- el modelo "intfloat/multilingual-e5-small" (multilingüe, ligero, y con
-- buen soporte de español). Si cambias de modelo de embeddings, actualiza
-- también el `vector(384)` de la tabla y de la función más abajo.
-- ============================================================


-- 1. Extensión pgvector (búsqueda semántica) ------------------------------
create extension if not exists vector;


-- 2. Tabla principal --------------------------------------------------------
create table if not exists public.subvenciones (
    id                      bigint generated always as identity primary key,

    -- Identidad y procedencia -----------------------------------------
    -- codigo_unico es la clave de deduplicación (upsert) usada por los
    -- scripts de ingesta: 'BDNS-123456' para convocatorias de la BDNS,
    -- o 'BOE-BOE-A-2026-1234' para anuncios del BOE sin número BDNS
    -- asociado todavía. Ver ingest/ingesta_bdns.py e ingest/ingesta_boe.py.
    codigo_unico            text not null unique,
    codigo_bdns             text,                          -- nº de convocatoria en la BDNS, si existe
    fuente_origen           text not null default 'BDNS',  -- 'BDNS' | 'BOE' | 'BDNS, BOE'

    -- Contenido ----------------------------------------------------------
    titulo                  text not null,
    descripcion             text,
    organismo               text,
    ambito                  text,                          -- 'Nacional' | 'Autonómico' | 'Local'
    ccaa                    text[] not null default '{}',
    categorias              text[] not null default '{}',  -- etiquetas temáticas (ver ingest/common.py)

    -- Enlaces oficiales ----------------------------------------------------
    url_oficial             text,                          -- ficha de la convocatoria (normalmente en la BDNS)
    url_boe                 text,                          -- extracto/bases reguladoras publicadas en el BOE

    -- Fechas y plazos --------------------------------------------------------
    fecha_publicacion       date,
    fecha_inicio_solicitud  date,
    fecha_fin_solicitud     date,                          -- clave para el filtro "plazo vigente"

    -- Importe y elegibilidad -------------------------------------------------
    presupuesto_total       numeric,
    beneficiarios           text,                          -- tipo de beneficiario (pymes, autónomos, entidades...)
    empleados_min           integer,
    empleados_max           integer,
    antiguedad_min_anios    numeric,
    antiguedad_max_anios    numeric,

    -- Búsqueda -----------------------------------------------------------
    texto_completo          text,                          -- texto fuente del embedding (trazabilidad/depuración)
    embedding               vector(384),

    -- Seguimiento de cambios ------------------------------------------------
    es_novedad              boolean not null default true,
    es_actualizada          boolean not null default false,
    fecha_alta              timestamptz not null default now(),
    fecha_actualizacion     timestamptz not null default now()
);


-- 3. Índices ------------------------------------------------------------
create index if not exists idx_subvenciones_fecha_fin  on public.subvenciones (fecha_fin_solicitud);
create index if not exists idx_subvenciones_ambito     on public.subvenciones (ambito);
create index if not exists idx_subvenciones_fuente     on public.subvenciones (fuente_origen);
create index if not exists idx_subvenciones_ccaa       on public.subvenciones using gin (ccaa);
create index if not exists idx_subvenciones_categorias on public.subvenciones using gin (categorias);

-- Índice vectorial para la búsqueda semántica.
-- HNSW es la opción recomendada actualmente en Supabase (mejor latencia de
-- consulta, no necesita re-tuning al crecer la tabla, y es el tipo de
-- índice vectorial por defecto en proyectos nuevos). Requiere pgvector
-- >= 0.5.0, ya disponible en cualquier proyecto de Supabase reciente.
create index if not exists idx_subvenciones_embedding
    on public.subvenciones using hnsw (embedding vector_cosine_ops);

-- Alternativa si tu proyecto usa una versión antigua de pgvector sin HNSW
-- (más rápida de construir en cargas masivas iniciales, pero necesita
-- ANALYZE y ajustar "lists" según el volumen de filas):
--
-- create index if not exists idx_subvenciones_embedding
--     on public.subvenciones using ivfflat (embedding vector_cosine_ops)
--     with (lists = 100);


-- 4. Trigger: mantener fecha_actualizacion al día ----------------------------
create or replace function public.actualizar_fecha_actualizacion()
returns trigger
language plpgsql
as $$
begin
    new.fecha_actualizacion = now();
    return new;
end;
$$;

drop trigger if exists trg_subvenciones_actualizar_fecha on public.subvenciones;
create trigger trg_subvenciones_actualizar_fecha
    before update on public.subvenciones
    for each row
    execute function public.actualizar_fecha_actualizacion();


-- 5. Row Level Security: lectura pública, escritura solo backend ------------
-- La app de Streamlit lee con la clave ANÓNIMA (anon/public) -> necesita
-- una política de SELECT. Los scripts de ingesta (GitHub Actions) escriben
-- con la Service Role Key, que ignora la RLS por diseño de Supabase, así
-- que NO hace falta (ni conviene) crear políticas de INSERT/UPDATE/DELETE
-- para anon/authenticated.
alter table public.subvenciones enable row level security;

drop policy if exists "Lectura publica de subvenciones" on public.subvenciones;
create policy "Lectura publica de subvenciones"
    on public.subvenciones
    for select
    to anon, authenticated
    using (true);

grant select on public.subvenciones to anon, authenticated;


-- 6. Función de búsqueda -----------------------------------------------------
-- Combina, todo dentro de Postgres, la similitud semántica (opcional) con
-- los filtros estructurados del buscador (categoría, ámbito, CCAA, fuente,
-- importe, plazo vigente, novedades). Si no se pasa query_embedding, se
-- comporta como un listado filtrado y ordenado por fecha de publicación.
-- Resolver el filtrado en la base de datos (en vez de traer todas las
-- filas a Python/pandas y filtrar en memoria) es lo que permite que la
-- app siga siendo rápida cuando la tabla crezca a decenas de miles de filas.
create or replace function public.buscar_subvenciones(
    query_embedding     vector(384) default null,
    match_threshold      float       default 0.2,
    match_count          int         default 300,
    filtro_categorias    text[]      default null,
    filtro_ambito        text        default null,
    filtro_ccaa          text[]      default null,
    filtro_fuente        text        default null,
    filtro_importe_min   numeric     default null,
    filtro_importe_max   numeric     default null,
    solo_vigentes        boolean     default true,
    solo_novedades       boolean     default false
)
returns table (
    id                      bigint,
    codigo_unico            text,
    codigo_bdns             text,
    titulo                  text,
    descripcion             text,
    organismo               text,
    fuente_origen           text,
    url_oficial             text,
    url_boe                 text,
    ambito                  text,
    ccaa                    text[],
    categorias              text[],
    fecha_publicacion       date,
    fecha_inicio_solicitud  date,
    fecha_fin_solicitud     date,
    presupuesto_total       numeric,
    beneficiarios           text,
    es_novedad              boolean,
    es_actualizada          boolean,
    similarity              float
)
language plpgsql
stable
as $$
begin
    return query
    select
        s.id, s.codigo_unico, s.codigo_bdns, s.titulo, s.descripcion, s.organismo,
        s.fuente_origen, s.url_oficial, s.url_boe, s.ambito, s.ccaa, s.categorias,
        s.fecha_publicacion, s.fecha_inicio_solicitud, s.fecha_fin_solicitud,
        s.presupuesto_total, s.beneficiarios, s.es_novedad, s.es_actualizada,
        case
            when query_embedding is null then 1.0
            else 1 - (s.embedding <=> query_embedding)
        end as similarity
    from public.subvenciones s
    where
        (query_embedding is null or 1 - (s.embedding <=> query_embedding) >= match_threshold)
        and (filtro_categorias  is null or s.categorias && filtro_categorias)
        and (filtro_ambito      is null or s.ambito = filtro_ambito)
        and (filtro_ccaa        is null or s.ccaa && filtro_ccaa)
        and (filtro_fuente      is null or s.fuente_origen ilike '%' || filtro_fuente || '%')
        and (filtro_importe_min is null or s.presupuesto_total >= filtro_importe_min)
        and (filtro_importe_max is null or s.presupuesto_total <= filtro_importe_max)
        and (not solo_vigentes  or s.fecha_fin_solicitud is null or s.fecha_fin_solicitud >= current_date)
        and (not solo_novedades or s.es_novedad or s.es_actualizada)
    order by
        -- Si hay búsqueda semántica, manda la distancia (más similar primero).
        case when query_embedding is not null then (s.embedding <=> query_embedding) end asc nulls last,
        -- Si no la hay, el criterio anterior es NULL en todas las filas y
        -- Postgres pasa automáticamente a este segundo criterio.
        s.fecha_publicacion desc nulls last
    limit match_count;
end;
$$;

grant execute on function public.buscar_subvenciones to anon, authenticated;
