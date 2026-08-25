# Radar de medios colombianos

Recopila titulares desde RSS y sitemaps, los guarda en PostgreSQL y ejecuta un
worker NER para detectar personas, organizaciones y lugares.

## Flujo

1. `main.py` valida `medios.py` y evita ejecuciones simultáneas.
2. `operacion.py` coordina descarga, parsing, limpieza y persistencia.
3. `scraper.py` convierte cada fuente XML al mismo formato de noticia.
4. `base_datos.py` guarda noticias, logs y entidades en transacciones.
5. `procesar_entidades.py` toma noticias pendientes y usa `entidades.py`.
6. Los CSV corrigen tipos, resuelven alias y completan omisiones del modelo.

## Instalación local

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Completa las credenciales PostgreSQL en `.env` y aplica `schema.sql` antes de
ejecutar los procesos.

## Ejecución

Recopilar noticias:

```bash
.venv/bin/python main.py
```

Procesar entidades pendientes:

```bash
.venv/bin/python procesar_entidades.py
```

Ambos procesos escriben en `logs/app.log` de forma predeterminada. Las rutas
pueden cambiarse con `APP_LOG_FILE` y `APP_LOCK_FILE`.

## Catálogos NER

- `catalogo_entidades.csv`: nombres que se buscan directamente en titulares.
- `catalogo_aliases.csv`: variantes aprobadas y su nombre canónico.
- `catalogo_correcciones.csv`: cambios de tipo y términos descartados.

Los catálogos son parte de la aplicación. Toda edición debe pasar primero por
las pruebas para detectar duplicados, tipos incompatibles y conflictos al
normalizar tildes.

## Pruebas

```bash
.venv/bin/python -m unittest discover -q
```

Las pruebas unitarias no abren PostgreSQL, no descargan medios y no cargan el
modelo real. La aceptación en producción debe comprobar por separado conexión,
persistencia, disponibilidad del modelo y una muestra de resultados.

## Utilidades

`scripts/verificar_ips_powerbi.py` compara direcciones con el archivo público de
rangos de servicio de Microsoft:

```bash
.venv/bin/python scripts/verificar_ips_powerbi.py /ruta/ServiceTags_Public.json
```
