-- Esquema base de la aplicación.
-- Los nombres coinciden con la estructura real de la base compartida:
-- `noticias` y `scrape_logs`, incluyendo `ejecutado_en`.

-- gen_random_uuid() pertenece a la extensión pgcrypto. En servicios como
-- Supabase normalmente ya está habilitada; esta línea también permite usar el
-- esquema en una base de pruebas nueva.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public.noticias (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    titulo TEXT NULL,
    url TEXT NULL,
    medio VARCHAR NULL,
    descubierto_en TIMESTAMPTZ DEFAULT now() NULL,
    fecha_publicacion TIMESTAMPTZ NULL,
    titulo_anterior TEXT NULL,
    CONSTRAINT noticias_nueva_pkey PRIMARY KEY (id),
    CONSTRAINT noticias_url_unique UNIQUE (url)
);

CREATE TABLE IF NOT EXISTS public.scrape_logs (
    id SERIAL4 NOT NULL,
    medio VARCHAR(100) NULL,
    ejecutado_en TIMESTAMPTZ DEFAULT now() NULL,
    noticias_nuevas INT4 NULL,
    noticias_vistas INT4 NULL,
    status VARCHAR(20) NULL,
    error_msg TEXT,
    noticias_total INTEGER GENERATED ALWAYS AS (
        noticias_nuevas + noticias_vistas
    ) STORED,
    CONSTRAINT scrape_logs_pkey PRIMARY KEY (id)
);
