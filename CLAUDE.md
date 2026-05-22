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
anuncios Click-to-WhatsApp, por eso también se guarda el `ctwa_clid`). Facebook entrega el
gasto y el nombre de cada anuncio por `ad_id`.

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
Facebook Ads API ──(ad_id, nombre, gasto)──┐
                                            ├──> metrics.py ──> app.py (Streamlit)
Supabase ──────────(ventas por ad_id)───────┘   (une por ad_id,
                                                  calcula ROAS y CPA)
```

## Modelo de datos  ⚠️ AJUSTAR A TU ESQUEMA REAL

Tabla de ventas en Supabase. **Reemplaza estos nombres por los reales de tu tabla:**

- Tabla: `ventas`            ⚠️ AJUSTAR
- `ad_id` (text)             — id del anuncio de Facebook ← clave del cruce  ⚠️ AJUSTAR
- `valor` (numeric)          — monto de la venta                              ⚠️ AJUSTAR
- `created_at` (timestamptz) — fecha de la venta                              ⚠️ AJUSTAR
- `ctwa_clid` (text)         — click id de Click-to-WhatsApp                  ⚠️ AJUSTAR

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
│   ├── facebook_client.py   # trae gasto + nombre por ad_id (Fase 1)
│   ├── supabase_client.py   # lee ventas (Fase 1)
│   └── metrics.py           # une por ad_id y calcula ROAS/CPA (Fase 2)
└── app.py                   # dashboard Streamlit (Fase 3)
```

## Variables de entorno (archivo `.env` — NUNCA subir a git)

```
FB_ACCESS_TOKEN=
FB_AD_ACCOUNT_ID=act_XXXXXXXXX
FB_APP_ID=
FB_APP_SECRET=
SUPABASE_URL=
SUPABASE_KEY=
SALES_TABLE=ventas
```

## Comandos

- Instalar dependencias: `pip install -r requirements.txt`
- Correr en local: `streamlit run app.py`

## Reglas y convenciones (IMPORTANTES)

- Las credenciales viven **solo** en `.env`. Nunca hardcodear secretos ni hacer commit de
  ellos. `.env` debe estar en `.gitignore`.
- El acceso a Supabase y a Facebook es de **solo lectura**. Esta app no escribe ni borra nada
  en ninguna de las dos fuentes.
- Cachear las llamadas a la API de Facebook (`st.cache_data`): es lenta y tiene límites de uso.
- Trabajar **una fase a la vez** (ver PROJECT_PLAN.md). Hacer commit cada vez que algo funcione.
- Validar siempre los números (gasto y ventas) contra el Administrador de Anuncios de Facebook
  antes de dar una fase por terminada.

## Estado actual

Fase 0 (Setup). Ver `PROJECT_PLAN.md` para el detalle y el checklist de cada fase.
