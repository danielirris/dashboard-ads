"""Dashboard de Rentabilidad por Anuncio — Streamlit.

Cruza el gasto de Facebook Ads con las ventas de Supabase por `ad_id` y muestra
KPIs, evolución diaria, tabla por anuncio y ranking de los que más venden.
Todo en pesos colombianos (COP).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Permite importar desde src/ sin necesidad de __init__.py.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from metrics import get_ad_performance, get_daily_totals  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Configuración general
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Dashboard de Anuncios",
    page_icon="📊",
    layout="wide",
)

CACHE_TTL = 600  # 10 minutos


@st.cache_data(ttl=CACHE_TTL, show_spinner="Cargando rendimiento por anuncio…")
def load_performance(since: str, until: str) -> pd.DataFrame:
    return get_ad_performance(since, until)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Cargando evolución diaria…")
def load_daily(since: str, until: str) -> pd.DataFrame:
    return get_daily_totals(since, until)


def fmt_cop(value: float) -> str:
    """Formato pesos colombianos sin decimales y con separador de miles."""
    return f"${value:,.0f} COP"


# ──────────────────────────────────────────────────────────────────────────────
# Sidebar: filtros
# ──────────────────────────────────────────────────────────────────────────────

st.sidebar.title("Filtros")

today = date.today()
default_since = today - timedelta(days=7)

date_range = st.sidebar.date_input(
    "Rango de fechas",
    value=(default_since, today),
    max_value=today,
    format="YYYY-MM-DD",
)

if not isinstance(date_range, tuple) or len(date_range) != 2:
    st.sidebar.warning("Selecciona un rango con fecha de inicio y de fin.")
    st.stop()

since_d, until_d = date_range
if since_d > until_d:
    st.sidebar.error("La fecha de inicio es posterior a la de fin.")
    st.stop()

since = since_d.isoformat()
until = until_d.isoformat()

st.sidebar.caption(f"Periodo: **{since}** → **{until}**")

if st.sidebar.button("🔄 Refrescar datos"):
    st.cache_data.clear()
    st.rerun()


# ──────────────────────────────────────────────────────────────────────────────
# Carga de datos
# ──────────────────────────────────────────────────────────────────────────────

df = load_performance(since, until)
daily = load_daily(since, until)


# ──────────────────────────────────────────────────────────────────────────────
# Header + KPIs
# ──────────────────────────────────────────────────────────────────────────────

st.title("Dashboard de Rentabilidad por Anuncio")
st.caption(f"Periodo: **{since}** → **{until}** · Pesos colombianos (COP)")

gasto_total = float(df["gasto"].sum())
monto_ventas_total = float(df["monto_ventas"].sum())
n_ventas_total = int(df["n_ventas"].sum())
conv_total = int(df["conversaciones"].sum())
roas_global = monto_ventas_total / gasto_total if gasto_total > 0 else None
cpa_global = gasto_total / n_ventas_total if n_ventas_total > 0 else None
cost_per_conv_global = gasto_total / conv_total if conv_total > 0 else None
tasa_conv_global = (100 * n_ventas_total / conv_total) if conv_total > 0 else None

col1, col2, col3, col4 = st.columns(4)
col1.metric("Gasto total", fmt_cop(gasto_total))
col2.metric("Ventas totales", fmt_cop(monto_ventas_total))
col3.metric(
    "ROAS global",
    f"{roas_global:.2f}" if roas_global is not None else "—",
    help="Monto de ventas ÷ Gasto. >1 significa que ingresas más de lo que inviertes.",
)
col4.metric(
    "Nº ventas (CPA)",
    f"{n_ventas_total}",
    delta=fmt_cop(cpa_global) if cpa_global is not None else None,
    delta_color="off",
    help="Total de ventas. Debajo, el CPA global (costo por venta).",
)

# Segunda fila: métricas de embudo / mensajería.
col5, col6, col7, _ = st.columns(4)
col5.metric(
    "Conversaciones",
    f"{conv_total:,}",
    help=(
        "Total de contactos de WhatsApp en el rango (tabla `contactos` de "
        "Supabase). Incluye los no atribuidos a un anuncio — esos aparecen en "
        "la tabla como 'Sin anuncio (no atribuido)' para que los totales "
        "reconcilien."
    ),
)
col6.metric(
    "Costo / conversación",
    fmt_cop(cost_per_conv_global) if cost_per_conv_global is not None else "—",
    help="Gasto ÷ Conversaciones.",
)
col7.metric(
    "Tasa Conv → Venta",
    f"{tasa_conv_global:.1f}%" if tasa_conv_global is not None else "—",
    help="Nº ventas ÷ Conversaciones × 100. Eficiencia del speech de venta.",
)

st.divider()


# ──────────────────────────────────────────────────────────────────────────────
# Evolución diaria
# ──────────────────────────────────────────────────────────────────────────────

st.subheader("Gasto y ventas por día")

if daily.empty or (daily["gasto"].sum() == 0 and daily["monto_ventas"].sum() == 0):
    st.info("Sin gasto ni ventas en el rango seleccionado.")
else:
    daily_long = daily.melt(
        id_vars="date",
        value_vars=["gasto", "monto_ventas"],
        var_name="serie",
        value_name="valor",
    )
    nombres = {"gasto": "Gasto", "monto_ventas": "Ventas"}
    daily_long["serie"] = daily_long["serie"].map(nombres)

    fig_daily = px.line(
        daily_long,
        x="date",
        y="valor",
        color="serie",
        markers=True,
        labels={"date": "Día", "valor": "COP", "serie": ""},
        color_discrete_map={"Gasto": "#EF553B", "Ventas": "#00CC96"},
    )
    fig_daily.update_layout(
        legend_title_text="",
        hovermode="x unified",
        yaxis_tickformat=",.0f",
    )
    fig_daily.update_traces(hovertemplate="$%{y:,.0f} COP")
    st.plotly_chart(fig_daily, use_container_width=True)


# ──────────────────────────────────────────────────────────────────────────────
# Conversaciones por día (tabla contactos: TODAS, atribuidas o no)
# ──────────────────────────────────────────────────────────────────────────────

st.subheader("Conversaciones por día")
st.caption(
    "Total de contactos por día de Bogotá (tabla `contactos` de Supabase). "
    "Cuenta todos los contactos, incluso los que llegaron sin `primer_ad_id`."
)

if daily.empty or daily["conversaciones"].sum() == 0:
    st.info("Sin conversaciones en el rango.")
else:
    fig_conv = px.bar(
        daily,
        x="date",
        y="conversaciones",
        labels={"date": "Día", "conversaciones": "Conversaciones"},
        color_discrete_sequence=["#636EFA"],
    )
    fig_conv.update_layout(
        hovermode="x unified",
        yaxis_tickformat=",.0f",
    )
    fig_conv.update_traces(hovertemplate="%{y:,} conversaciones")
    st.plotly_chart(fig_conv, use_container_width=True)

st.divider()


# ──────────────────────────────────────────────────────────────────────────────
# Tabla por anuncio
# ──────────────────────────────────────────────────────────────────────────────

st.subheader("Rendimiento por anuncio")
st.caption(
    "Click en cualquier cabecera para ordenar. Incluye anuncios con gasto pero "
    "sin ventas y ventas cuyo ad_id no aparece en Facebook."
)

if df.empty:
    st.info("Sin anuncios ni ventas en el rango.")
else:
    st.dataframe(
        df.drop(columns=["ad_id"]),
        use_container_width=True,
        hide_index=True,
        column_config={
            "ad_name": st.column_config.TextColumn("Anuncio", width="large"),
            "gasto": st.column_config.NumberColumn("Gasto (COP)", format="$%.0f"),
            "frequency": st.column_config.NumberColumn(
                "Frecuencia",
                format="%.2f",
                help="Promedio de veces que cada persona vio el anuncio.",
            ),
            "conversaciones": st.column_config.NumberColumn(
                "Conv.",
                format="%d",
                help="Conversaciones iniciadas (WhatsApp).",
            ),
            "n_ventas": st.column_config.NumberColumn("Nº ventas", format="%d"),
            "monto_ventas": st.column_config.NumberColumn(
                "Ventas (COP)", format="$%.0f"
            ),
            "costo_por_conversacion": st.column_config.NumberColumn(
                "Costo/Conv. (COP)",
                format="$%.0f",
                help="Gasto ÷ Conversaciones. NaN sin conversaciones.",
            ),
            "cpa": st.column_config.NumberColumn(
                "CPA (COP)",
                format="$%.0f",
                help="Gasto ÷ Nº de ventas. NaN cuando no hay ventas.",
            ),
            "tasa_conversion": st.column_config.NumberColumn(
                "% Conv→Venta",
                format="%.1f%%",
                help="Nº ventas ÷ Conversaciones × 100. Eficiencia del speech.",
            ),
            "roas": st.column_config.NumberColumn(
                "ROAS",
                format="%.2f",
                help="Monto de ventas ÷ Gasto. NaN cuando el gasto es 0.",
            ),
        },
    )

st.divider()


# ──────────────────────────────────────────────────────────────────────────────
# Ranking de los que más venden
# ──────────────────────────────────────────────────────────────────────────────

st.subheader("Top anuncios por ventas")

top_n = st.slider("¿Cuántos mostrar?", min_value=5, max_value=30, value=15, step=5)
top = df[df["monto_ventas"] > 0].head(top_n)

if top.empty:
    st.info("Aún no hay anuncios con ventas en este rango.")
else:
    # Truncamos nombres largos para que la gráfica sea legible.
    top = top.copy()
    top["label"] = top["ad_name"].apply(
        lambda s: s if len(s) <= 50 else s[:47] + "…"
    )
    fig_top = px.bar(
        top.iloc[::-1],  # invertimos para que el #1 quede arriba
        x="monto_ventas",
        y="label",
        orientation="h",
        labels={"monto_ventas": "Monto de ventas (COP)", "label": ""},
        text="monto_ventas",
        color="roas",
        color_continuous_scale="RdYlGn",
        hover_data={"label": False, "ad_name": True, "roas": ":.2f", "monto_ventas": ":,.0f"},
    )
    fig_top.update_traces(texttemplate="$%{x:,.0f}", textposition="outside")
    fig_top.update_layout(
        height=max(350, 35 * len(top)),
        xaxis_tickformat=",.0f",
        coloraxis_colorbar=dict(title="ROAS"),
    )
    st.plotly_chart(fig_top, use_container_width=True)


st.divider()


# ──────────────────────────────────────────────────────────────────────────────
# Embudo: conversaciones vs ventas
# ──────────────────────────────────────────────────────────────────────────────

st.subheader("Embudo: conversaciones → ventas por anuncio")
st.caption(
    "Compara cuántas conversaciones genera cada anuncio en WhatsApp con cuántas "
    "terminan en venta. El porcentaje al lado de la barra verde es la tasa de "
    "conversión del speech (ventas ÷ conversaciones)."
)

funnel_n = st.slider(
    "¿Cuántos anuncios mostrar?",
    min_value=5,
    max_value=30,
    value=15,
    step=5,
    key="funnel_n",
)

# Ordenamos por conversaciones (volumen de entrada al embudo).
funnel_df = (
    df[(df["conversaciones"] > 0) | (df["n_ventas"] > 0)]
    .sort_values("conversaciones", ascending=False)
    .head(funnel_n)
    .copy()
)

if funnel_df.empty:
    st.info("Aún no hay conversaciones ni ventas en este rango.")
else:
    funnel_df["label"] = funnel_df["ad_name"].apply(
        lambda s: s if len(s) <= 50 else s[:47] + "…"
    )
    # Invertimos para que el de más conversaciones quede arriba en la gráfica.
    funnel_df = funnel_df.iloc[::-1]

    ventas_text = [
        f"{int(n):,}" + (f"  ({t:.1f}%)" if pd.notna(t) else "")
        for n, t in zip(funnel_df["n_ventas"], funnel_df["tasa_conversion"])
    ]

    fig_funnel = go.Figure()
    fig_funnel.add_trace(
        go.Bar(
            name="Conversaciones",
            y=funnel_df["label"],
            x=funnel_df["conversaciones"],
            orientation="h",
            marker_color="#636EFA",
            text=funnel_df["conversaciones"].apply(lambda v: f"{int(v):,}"),
            textposition="outside",
            hovertemplate="<b>%{customdata}</b><br>Conversaciones: %{x:,}<extra></extra>",
            customdata=funnel_df["ad_name"],
        )
    )
    fig_funnel.add_trace(
        go.Bar(
            name="Ventas",
            y=funnel_df["label"],
            x=funnel_df["n_ventas"],
            orientation="h",
            marker_color="#00CC96",
            text=ventas_text,
            textposition="outside",
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Ventas: %{x:,}<br>"
                "Tasa conv.: %{customdata[1]}<extra></extra>"
            ),
            customdata=list(
                zip(
                    funnel_df["ad_name"],
                    funnel_df["tasa_conversion"].apply(
                        lambda t: f"{t:.1f}%" if pd.notna(t) else "—"
                    ),
                )
            ),
        )
    )
    fig_funnel.update_layout(
        barmode="group",
        height=max(400, 45 * len(funnel_df)),
        xaxis_title="Cantidad",
        yaxis_title="",
        legend_title="",
        margin=dict(l=10, r=80, t=20, b=40),
    )
    st.plotly_chart(fig_funnel, use_container_width=True)
