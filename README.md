# Dashboard de Rentabilidad por Anuncio

Dashboard interno que cruza el **gasto de Facebook Ads** con las **ventas registradas en
Supabase** para mostrar, por cada anuncio y con su nombre real, cuánto se gastó, cuánto
vendió, su ROAS y su CPA.

El cruce se hace uniendo ambas fuentes por `ad_id` (las ventas vienen de anuncios
Click-to-WhatsApp y guardan el `ad_id` que las originó).

## Stack

- Python 3.11+
- Streamlit (dashboard)
- pandas (cruce y cálculos)
- facebook-business (Facebook Marketing API)
- supabase (cliente Python, solo lectura)
- python-dotenv (credenciales)
- plotly (gráficas)

## Setup

```bash
# 1. Crear y activar entorno virtual
python -m venv venv
source venv/bin/activate  # macOS / Linux

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Crear .env a partir de la plantilla y rellenar credenciales
cp .env.example .env

# 4. Correr el dashboard
streamlit run app.py
```

## Estructura

```
.
├── CLAUDE.md            # contexto para Claude Code
├── PROJECT_PLAN.md      # plan por fases
├── README.md
├── .gitignore
├── .env.example
├── requirements.txt
├── src/
│   ├── facebook_client.py   # gasto + nombre por ad_id
│   ├── supabase_client.py   # lectura de ventas
│   └── metrics.py           # cruce por ad_id y métricas
└── app.py                   # dashboard Streamlit
```

## Reglas

- Las credenciales viven **solo** en `.env` (ignorado por git).
- El acceso a Supabase y Facebook es de **solo lectura**.
- Ver `PROJECT_PLAN.md` para el plan de construcción por fases.
