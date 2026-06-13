# CLAUDE.md — Dashboard de Rentabilidad por Anuncio

> Archivo de contexto para Claude Code. Léelo siempre antes de trabajar en este proyecto.

## Qué es este proyecto

Dashboard interno (uso personal, una sola persona) que cruza el **gasto de Facebook Ads**
con las **ventas registradas en Supabase**, para mostrar —por cada anuncio y con su nombre
real— cuánto se gastó, cuánto vendió, su ROAS y su CPA.

**Objetivo central: saber qué anuncio vende.** No basta con ver totales; lo importante es
identificar el anuncio (por nombre) que genera ventas.

## Concepto clave: el cruce por `ad_id`

Cada venta en Supabase guarda el `ad_id` del anuncio de Facebook que la originó (vienen de
anuncios Click-to-WhatsApp). Facebook entrega el gasto y el nombre de cada anuncio por
`ad_id`.

**El cruce se hace uniendo ambas fuentes por `ad_id`.** Es la pieza más importante del
proyecto.

## Stack

- Python 3.11+
- Streamlit — dashboard
- pandas — cruce de datos y cálculos
- facebook-business — SDK oficial de la API de Facebook Marketing
- supabase — cliente de Python (SOLO LECTURA)
- python-dotenv — manejo de credenciales
- plotly — gráficas

## Arquitectura / flujo de datos

```
Facebook Ads ──(ad_id, nombre, gasto, frecuencia)──────┐
                                                        │
Supabase                                                ├──> metrics.py ──> app.py
  ├── compradores  (ventas por ad_id) ──────────────────┤    (cruza por
  └── contactos    (conversaciones por primer_ad_id) ───┘     ad_id, calcula
                                                              ROAS, CPA,
                                                              costo/conv, %conv)
```

Las conversaciones (mensajes iniciados en WhatsApp) vienen de la tabla `contactos`
de Supabase, NO de Facebook (la métrica de Facebook es menos confiable).

## Modelo de datos

**Tabla `compradores`** (ventas):

- `ad_id` (text)        — id del anuncio de Facebook ← clave del cruce
- `valor` (numeric)     — monto de la venta **en moneda local** (PEN, COP, MXN, etc.)
- `moneda` (text, nullable) — código ISO de la moneda (NULL se asume COP)
- `fecha_compra` (timestamptz UTC) — usada para filtrar por rango

`supabase_client.get_sales()` convierte `valor` a COP usando las tasas `*_TO_COP`
del `.env`. El DataFrame devuelve `valor_local`, `moneda` y `valor_cop`.

**Tabla `contactos`** (origen real de las conversaciones de WhatsApp):

- `primer_ad_id` (text, nullable) — ad_id que originó el contacto (a veces null)
- `primer_contacto_at` (timestamptz UTC) — momento del primer contacto
- `pais` (text, nullable) — código ISO-2 del país del contacto (NULL → Colombia)

Toda agrupación o cruce por fecha se hace en **día calendario de Bogotá**
(`America/Bogota`): los timestamps UTC se convierten con `.dt.tz_convert(BOGOTA)`
antes de tomar el día. NO usar `tz_localize(None)` — eso solo borra la zona sin
convertir y mete las ventas/contactos nocturnos en el día equivocado.

Los contactos con `primer_ad_id` nulo se agrupan aparte en una fila
`"Sin anuncio (no atribuido)"` para que los totales reconcilien
(total = atribuidas + sin atribuir).

**Filtro de conversaciones por país:** las conversaciones se filtran por la
columna `pais` de `contactos` (NO por el cruce de ad_id), porque muchos
contactos no tienen `primer_ad_id`. En cambio el **gasto** y las **ventas** se
filtran por `ad_id` (atribución por anuncio). El KPI de conversaciones y el
gráfico "Conversaciones por día" derivan del mismo `get_daily_totals(..., pais=)`
para que siempre coincidan.

## Estructura del proyecto

```
.
├── CLAUDE.md            # este archivo
├── PROJECT_PLAN.md      # plan por fases con checklists y prompts
├── README.md
├── .gitignore           # debe ignorar .env y el entorno virtual
├── .env.example         # plantilla de variables (sin valores reales)
├── requirements.txt
├── src/
│   ├── currency.py          # tasas *_TO_COP compartidas (FB gasto + ventas)
│   ├── facebook_client.py   # gasto, frecuencia, nombre por ad_id (FB API)
│   ├── supabase_client.py   # ventas (compradores) + contactos (Supabase)
│   └── metrics.py           # cruza por ad_id y calcula métricas
└── app.py                   # dashboard Streamlit
```

## Múltiples cuentas y moneda

`FB_AD_ACCOUNT_ID` puede contener **una o varias cuentas publicitarias separadas por coma**.
El gasto de todas las cuentas se combina en un único DataFrame.

Las ventas en Supabase están en **pesos colombianos (COP)**, por lo que el gasto también se
devuelve en COP. Para cada cuenta se consulta `account_currency` vía la API; las cuentas que
no estén en COP se convierten multiplicando su gasto por `USD_TO_COP` (tasa configurable en
`.env`, con un valor por defecto razonable en el código).

## Variables de entorno (archivo `.env` — NUNCA subir a git)

```
FB_ACCESS_TOKEN=
FB_APP_ID=
FB_APP_SECRET=

# Cuentas: vacío → auto-discovery (todas las del token vía /me/adaccounts).
# Definida → filtro a esas cuentas. Lista separada por coma.
FB_AD_ACCOUNT_ID=

# Tasas a COP. Una variable <MONEDA>_TO_COP por cada moneda usada.
USD_TO_COP=4000
MXN_TO_COP=200

SUPABASE_URL=
SUPABASE_KEY=
SALES_TABLE=compradores
CONTACTS_TABLE=contactos

# Asistente IA (OpenAI). Si está vacío, el chat aparece deshabilitado.
OPENAI_API_KEY=
```

## Parámetros del negocio (config.json, NO .env)

Los umbrales del semáforo (CPA, ROAS), el margen y la meta diaria de ganancia
viven en `config.json` en la raíz, gestionados desde el panel
"⚙️ Configuración del negocio" del dashboard (sidebar).

Estructura de `config.json`:

```
{
  "margen_porcentaje": 0.30,
  "cpa_bueno": 80000,
  "cpa_maximo": 120000,
  "roas_minimo": 2.0,
  "roas_bueno": 3.0,
  "meta_ganancia_diaria": 100000
}
```

- Lo lee `src/config.py` (`load_config`, `save_config`, `get_config`).
- Si no existe, se crea en el primer arranque: bootstrap desde el `.env`
  (compatibilidad con la versión previa) o desde defaults razonables.
- Está en `.gitignore` (config personal, no se versiona).
- Las **claves y tokens** siguen en `.env` (`OPENAI_API_KEY`, `FB_*`,
  `SUPABASE_KEY`, etc.); NUNCA se exponen en la UI.

## Semáforo de CPA y ROAS

El dashboard pinta CPA y ROAS en **verde/amarillo/rojo** según los umbrales
del `.env`. Se aplica en varios niveles: tabla por cuenta, por campaña, por
anuncio y en el panel de detalle (tarjetas grandes).

- CPA: `CPA_BUENO` y `CPA_MAXIMO` (`≤ BUENO` 🟢, `≤ MAXIMO` 🟡, `>` 🔴).
- ROAS: `ROAS_MINIMO` y `ROAS_BUENO` (`< MINIMO` 🔴, `< BUENO` 🟡, `≥ BUENO` 🟢).

La lógica vive en `metrics.cpa_status()` y `metrics.roas_status()`. Si los
umbrales no están definidos en el `.env`, el semáforo queda en gris ("sin datos").

## Comandos

- Instalar dependencias: `pip install -r requirements.txt`
- Correr en local: `streamlit run app.py`

## Reglas y convenciones (IMPORTANTES)

- Las credenciales viven **solo** en `.env`. Nunca hardcodear secretos ni hacer commit de
  ellos. `.env` debe estar en `.gitignore`.
- El acceso a Facebook es siempre de **solo lectura**.
- El acceso a Supabase es **mayoritariamente de solo lectura**. La única excepción es la
  función `sync_ads_to_supabase(since, until)` en `src/facebook_client.py`, invocada
  desde el botón "📥 Sincronizar anuncios con Facebook" del sidebar: hace **INSERT**
  (nunca UPDATE ni DELETE) en la tabla `anuncios` cuando el usuario lo pide. Si el
  `ad_id` ya existe, la fila no se toca (preserva `notas`, `producto_id`, `ad_headline`
  y cualquier `activo=False` puesto a mano). Para escribir usa `SUPABASE_SERVICE_KEY`
  del `.env` con fallback a `SUPABASE_KEY`.
- Cachear las llamadas a la API de Facebook (`st.cache_data`): es lenta y tiene límites de uso.
- Trabajar **una fase a la vez** (ver PROJECT_PLAN.md). Hacer commit cada vez que algo funcione.
- Validar siempre los números (gasto y ventas) contra el Administrador de Anuncios de Facebook
  antes de dar una fase por terminada.

## Estado actual

Fase 1 (Clientes de lectura por separado). Ver `PROJECT_PLAN.md` para el detalle y el
checklist de cada fase.
