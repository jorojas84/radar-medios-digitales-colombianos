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

-- FALSE significa que el titular todavía no pasó por el detector o cambió
-- desde el último análisis.
ALTER TABLE public.noticias
    ADD COLUMN IF NOT EXISTS entidades_detectadas BOOLEAN NOT NULL DEFAULT FALSE;

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

-- Esta tabla conserva cada mención, no solamente un contador. Dos apariciones
-- de la misma entidad en un titular tienen posiciones diferentes.
CREATE TABLE IF NOT EXISTS public.entidades (
    -- Identificador interno de cada mención almacenada.
    id BIGSERIAL NOT NULL,
    -- Noticia de cuyo titular salió la mención.
    noticia_id UUID NOT NULL,
    -- Texto tal como apareció en el titular.
    texto TEXT NOT NULL,
    -- Texto preparado para agrupar búsquedas sin alterar la versión visible.
    texto_normalizado TEXT NOT NULL,
    -- Nombre canónico para unir alias seguros sin perder el texto original.
    entidad_canonica TEXT NOT NULL,
    -- Etapas que produjeron o modificaron esta mención.
    fuentes_deteccion TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
    -- Etiqueta de BETO: persona, organización o lugar.
    tipo VARCHAR(10) NOT NULL,
    -- Posición inicial inclusiva y final exclusiva dentro del titular.
    inicio INTEGER NOT NULL,
    fin INTEGER NOT NULL,
    -- Momento en que esta mención fue guardada por el detector.
    detectado_en TIMESTAMPTZ DEFAULT now() NOT NULL,
    CONSTRAINT entidades_pkey PRIMARY KEY (id),
    CONSTRAINT entidades_noticia_fkey
        FOREIGN KEY (noticia_id)
        REFERENCES public.noticias (id)
        ON DELETE CASCADE,
    CONSTRAINT entidades_tipo_check
        CHECK (tipo IN ('PER', 'ORG', 'LOC')),
    CONSTRAINT entidades_posicion_check
        CHECK (inicio >= 0 AND fin > inicio),
    CONSTRAINT entidades_ubicacion_unique
        UNIQUE (noticia_id, inicio, fin, tipo)
);

ALTER TABLE public.entidades
    ADD COLUMN IF NOT EXISTS entidad_canonica TEXT;

UPDATE public.entidades
SET entidad_canonica = texto_normalizado
WHERE entidad_canonica IS NULL;

ALTER TABLE public.entidades
    ALTER COLUMN entidad_canonica SET NOT NULL;

ALTER TABLE public.entidades
    ADD COLUMN IF NOT EXISTS fuentes_deteccion TEXT[];

UPDATE public.entidades
SET fuentes_deteccion = ARRAY['historico']::TEXT[]
WHERE fuentes_deteccion IS NULL;

ALTER TABLE public.entidades
    ALTER COLUMN fuentes_deteccion SET DEFAULT '{}'::TEXT[],
    ALTER COLUMN fuentes_deteccion SET NOT NULL;

-- Acelera la selección cronológica del worker sin indexar noticias procesadas.
CREATE INDEX IF NOT EXISTS noticias_entidades_pendientes_idx
    ON public.noticias (descubierto_en, id)
    WHERE entidades_detectadas IS NOT TRUE AND titulo IS NOT NULL;

-- La restricción única de ubicación ya comienza por noticia_id.
DROP INDEX IF EXISTS public.entidades_noticia_idx;

CREATE INDEX IF NOT EXISTS entidades_busqueda_idx
    ON public.entidades (tipo, texto_normalizado);

CREATE INDEX IF NOT EXISTS entidades_canonica_idx
    ON public.entidades (tipo, entidad_canonica);
