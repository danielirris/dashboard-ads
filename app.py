"""Dashboard de Rentabilidad por Anuncio — Streamlit.

Cruza el gasto de Facebook Ads con las ventas de Supabase por `ad_id` y muestra
KPIs, evolución diaria, tabla por anuncio y ranking de los que más venden.
Todo en pesos colombianos (COP).
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Permite importar desde src/ sin necesidad de __init__.py.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import get_config, save_config  # noqa: E402
from metrics import (  # noqa: E402
    NO_ACCOUNT_LABEL,
    cpa_status,
    get_account_performance,
    get_ad_performance,
    get_campaign_performance,
    get_daily_totals,
    roas_status,
)

# ── OpenAI ───────────────────────────────────────────────────────────────────
# Import perezoso: si el paquete no está instalado, el chat se muestra como
# deshabilitado en vez de romper toda la app.
try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# Cambia a "gpt-5-mini" para abaratar; a "gpt-4o" si tienes problemas con gpt-5.
OPENAI_MODEL = "gpt-5"


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


@st.cache_data(ttl=CACHE_TTL, show_spinner="Cargando rendimiento por campaña…")
def load_campaigns(since: str, until: str) -> pd.DataFrame:
    return get_campaign_performance(since, until)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Cargando rendimiento por cuenta…")
def load_accounts(since: str, until: str) -> pd.DataFrame:
    return get_account_performance(since, until)


def fmt_cop(value: float) -> str:
    """Formato pesos colombianos sin decimales y con separador de miles."""
    return f"${value:,.0f} COP"


# ──────────────────────────────────────────────────────────────────────────────
# Semáforo: paleta y helpers de pintado
# ──────────────────────────────────────────────────────────────────────────────

# Fondos suaves para celdas de tabla (legibles en tema claro y oscuro).
STATUS_CELL_BG = {
    "verde":     "background-color: #d4edda; color: #155724",
    "amarillo":  "background-color: #fff3cd; color: #856404",
    "rojo":      "background-color: #f8d7da; color: #721c24",
    "sin_datos": "",
}

# Colores intensos para el panel de detalle (cards grandes).
STATUS_CARD = {
    "verde":     {"bg": "#28a745", "fg": "white"},
    "amarillo":  {"bg": "#ffc107", "fg": "#212529"},
    "rojo":      {"bg": "#dc3545", "fg": "white"},
    "sin_datos": {"bg": "#6c757d", "fg": "white"},
}


def _cpa_cell_style(v):
    return STATUS_CELL_BG[cpa_status(v)]


def _roas_cell_style(v):
    return STATUS_CELL_BG[roas_status(v)]


def _fmt_money_or_dash(v):
    return "—" if pd.isna(v) else f"${v:,.0f}"


def _fmt_roas_or_dash(v):
    return "—" if pd.isna(v) else f"{v:.2f}"


def _fmt_pct_or_dash(v):
    return "—" if pd.isna(v) else f"{v:.1f}%"


def big_traffic_light_card(label: str, value_str: str, status: str, subtitle: str = ""):
    """Tarjeta grande coloreada según el semáforo. Usada en el panel de detalle."""
    palette = STATUS_CARD[status]
    badge = {"verde": "🟢", "amarillo": "🟡", "rojo": "🔴", "sin_datos": "⚪"}[status]
    sub_html = (
        f"<div style='font-size:0.85rem; opacity:0.9; margin-top:0.35rem;'>{subtitle}</div>"
        if subtitle
        else ""
    )
    html = f"""
    <div style="background:{palette['bg']}; color:{palette['fg']};
                padding:1.2rem 1rem; border-radius:0.6rem; text-align:center;
                box-shadow:0 1px 3px rgba(0,0,0,.08);">
      <div style="font-size:0.85rem; opacity:0.9; letter-spacing:.04em;">
        {badge}  {label}
      </div>
      <div style="font-size:2.4rem; font-weight:700; margin-top:0.25rem;
                  line-height:1.1;">
        {value_str}
      </div>
      {sub_html}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def thresholds_caption() -> str:
    """Línea de pie con los umbrales activos del panel Configuración."""
    cfg = get_config()
    cpa_part = (
        f"CPA 🟢 ≤ ${cfg['cpa_bueno']:,.0f} · "
        f"🟡 ≤ ${cfg['cpa_maximo']:,.0f} · 🔴 >"
    )
    roas_part = (
        f"ROAS 🔴 < {cfg['roas_minimo']:.2f} · "
        f"🟡 < {cfg['roas_bueno']:.2f} · 🟢 ≥"
    )
    return f"{cpa_part}  ·  {roas_part}"


# ──────────────────────────────────────────────────────────────────────────────
# Asistente IA — helpers para system prompt y contexto de datos
# ──────────────────────────────────────────────────────────────────────────────


def build_system_prompt() -> str:
    """System prompt: rol, idioma, moneda, objetivos del negocio."""
    cfg = get_config()
    parts = [
        "Eres un analista experto en tráfico de pago (Facebook Ads / Meta Ads), "
        "especializado en campañas Click-to-WhatsApp.",
        "",
        "Tu misión: dar recomendaciones ACCIONABLES y CONCRETAS para optimizar "
        "el rendimiento de los anuncios, basándote estrictamente en los datos "
        "que aparecen más abajo (el dashboard de Dani).",
        "",
        "Reglas estrictas:",
        "- Responde SIEMPRE en español, tono directo y profesional.",
        "- Todos los importes están en pesos colombianos (COP).",
        "- Cita el nombre concreto del anuncio o campaña cuando hagas una "
        "  recomendación (no hables en abstracto).",
        "- Si te falta información para responder, dilo claramente en lugar de "
        "  inventar números.",
        "- Estructura las recomendaciones en bullets cuando tengas varias.",
        "- Prioriza: pausar lo que pierde dinero, escalar lo que rinde, y "
        "  detectar fatiga creativa (frecuencia alta + CTR cayendo).",
        "",
        "Objetivos del negocio:",
        f"- CPA (costo por venta) ideal: ≤ ${cfg['cpa_bueno']:,.0f} COP; "
        f"máximo aceptable: ≤ ${cfg['cpa_maximo']:,.0f} COP.",
        f"- ROAS mínimo aceptable: ≥ {cfg['roas_minimo']:.2f}; "
        f"objetivo ideal: ≥ {cfg['roas_bueno']:.2f}.",
        f"- Margen bruto sobre ventas: {cfg['margen_porcentaje']:.0%}. "
        f"Útil para razonar rentabilidad real: "
        f"ganancia_real ≈ ventas × {cfg['margen_porcentaje']:.2f} − gasto.",
        f"- Meta diaria de ganancia: ${cfg['meta_ganancia_diaria']:,.0f} COP.",
    ]
    return "\n".join(parts)


def _fmt_money_llm(v) -> str:
    return f"${v:,.0f}" if pd.notna(v) else "—"


def _fmt_num_llm(v) -> str:
    return f"{v:.2f}" if pd.notna(v) else "—"


def build_data_context(
    df: pd.DataFrame,
    campaigns: pd.DataFrame,
    accounts: pd.DataFrame,
    daily: pd.DataFrame,
    since: str,
    until: str,
) -> str:
    """Serializa los datos del dashboard a texto compacto para el LLM."""
    lines: list[str] = [
        "# DATOS ACTUALES DEL DASHBOARD",
        f"Período: {since} a {until} (días calendario de Bogotá).",
        "",
    ]

    # ── Totales globales ────────────────────────────────────────────────────
    gasto = float(df["gasto"].sum())
    ventas = float(df["monto_ventas"].sum())
    n_ventas = int(df["n_ventas"].sum())
    conv = int(df["conversaciones"].sum())

    lines.append("## Totales del período")
    lines.append(f"- Gasto total: ${gasto:,.0f} COP")
    lines.append(f"- Ventas (monto): ${ventas:,.0f} COP")
    lines.append(f"- Ganancia (ventas − gasto): ${ventas - gasto:,.0f} COP")
    lines.append(f"- Nº ventas: {n_ventas:,}")
    lines.append(f"- Conversaciones WhatsApp: {conv:,}")
    if gasto > 0:
        lines.append(f"- ROAS global: {ventas / gasto:.2f}")
    if n_ventas > 0:
        lines.append(f"- CPA global: ${gasto / n_ventas:,.0f} COP")
    if conv > 0:
        lines.append(f"- Tasa conv → venta: {100 * n_ventas / conv:.2f}%")
    lines.append("")

    # ── Cuentas ─────────────────────────────────────────────────────────────
    lines.append("## Por cuenta publicitaria")
    if accounts.empty:
        lines.append("(sin datos)")
    else:
        for _, r in accounts.iterrows():
            lines.append(
                f"- {r['account_name']} | "
                f"anuncios {int(r['n_anuncios'])} | "
                f"gasto {_fmt_money_llm(r['gasto'])} | "
                f"ventas {_fmt_money_llm(r['monto_ventas'])} | "
                f"utilidad {_fmt_money_llm(r['utilidad'])} | "
                f"n_ventas {int(r['n_ventas'])} | "
                f"conv {int(r['conversaciones'])} | "
                f"ROAS {_fmt_num_llm(r['roas'])} | "
                f"CPA {_fmt_money_llm(r['cpa'])}"
            )
    lines.append("")

    # ── Top campañas ────────────────────────────────────────────────────────
    n_camp = min(20, len(campaigns))
    lines.append(
        f"## Campañas (top {n_camp} por monto de ventas, de {len(campaigns)} totales)"
    )
    if campaigns.empty:
        lines.append("(sin datos)")
    else:
        for _, r in campaigns.head(20).iterrows():
            lines.append(
                f"- {r['campaign_name']} | "
                f"anuncios {int(r['n_anuncios'])} | "
                f"gasto {_fmt_money_llm(r['gasto'])} | "
                f"ventas {_fmt_money_llm(r['monto_ventas'])} | "
                f"utilidad {_fmt_money_llm(r['utilidad'])} | "
                f"n_ventas {int(r['n_ventas'])} | "
                f"conv {int(r['conversaciones'])} | "
                f"ROAS {_fmt_num_llm(r['roas'])} | "
                f"CPA {_fmt_money_llm(r['cpa'])}"
            )
    lines.append("")

    # ── Top anuncios ────────────────────────────────────────────────────────
    n_ads = min(30, len(df))
    lines.append(
        f"## Anuncios (top {n_ads} por monto de ventas, de {len(df)} totales)"
    )
    if df.empty:
        lines.append("(sin datos)")
    else:
        for _, r in df.head(30).iterrows():
            account = r.get("account_name") or "sin cuenta"
            lines.append(
                f"- [{account}] {r['ad_name']} | "
                f"gasto {_fmt_money_llm(r['gasto'])} | "
                f"ventas {_fmt_money_llm(r['monto_ventas'])} | "
                f"utilidad {_fmt_money_llm(r['utilidad'])} | "
                f"n_ventas {int(r['n_ventas'])} | "
                f"conv {int(r['conversaciones'])} | "
                f"ROAS {_fmt_num_llm(r['roas'])} | "
                f"CPA {_fmt_money_llm(r['cpa'])} | "
                f"freq {r['frequency']:.2f} | "
                f"impr {int(r['impressions']):,}"
            )
    lines.append("")

    # ── Totales por día ─────────────────────────────────────────────────────
    lines.append("## Totales por día (Bogotá)")
    if daily.empty:
        lines.append("(sin datos)")
    else:
        lines.append("fecha | gasto | ventas | n_ventas | conversaciones")
        for _, r in daily.iterrows():
            lines.append(
                f"{r['date']} | ${r['gasto']:,.0f} | ${r['monto_ventas']:,.0f}"
                f" | {int(r['n_ventas'])} | {int(r['conversaciones'])}"
            )

    return "\n".join(lines)


def render_ad_detail(ad: pd.Series) -> None:
    """Panel de detalle para un anuncio: título + 2 tarjetas de semáforo + 5 KPIs."""
    cpa_val = ad["cpa"]
    roas_val = ad["roas"]
    cpa_s = cpa_status(cpa_val)
    roas_s = roas_status(roas_val)

    cfg = get_config()
    cpa_sub = ""
    if pd.notna(cpa_val):
        if cpa_s == "verde":
            cpa_sub = f"≤ objetivo (${cfg['cpa_bueno']:,.0f})"
        elif cpa_s == "amarillo":
            cpa_sub = f"entre ${cfg['cpa_bueno']:,.0f} y ${cfg['cpa_maximo']:,.0f}"
        elif cpa_s == "rojo":
            cpa_sub = f"sobre el máximo (${cfg['cpa_maximo']:,.0f})"

    roas_sub = ""
    if pd.notna(roas_val):
        if roas_s == "verde":
            roas_sub = f"≥ objetivo ({cfg['roas_bueno']:.2f})"
        elif roas_s == "amarillo":
            roas_sub = f"entre {cfg['roas_minimo']:.2f} y {cfg['roas_bueno']:.2f}"
        elif roas_s == "rojo":
            roas_sub = f"bajo el mínimo ({cfg['roas_minimo']:.2f})"

    campaign_label = (
        ad["campaign_name"]
        if pd.notna(ad.get("campaign_name")) and ad["campaign_name"]
        else "Sin campaña"
    )
    st.markdown(f"**{ad['ad_name']}**  ·  campaña: `{campaign_label}`")

    cpa_col, roas_col = st.columns(2)
    with cpa_col:
        big_traffic_light_card(
            "CPA — costo por venta",
            _fmt_money_or_dash(cpa_val),
            cpa_s,
            cpa_sub,
        )
    with roas_col:
        big_traffic_light_card(
            "ROAS",
            _fmt_roas_or_dash(roas_val),
            roas_s,
            roas_sub,
        )

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Gasto", fmt_cop(float(ad["gasto"])))
    m2.metric("Ventas (monto)", fmt_cop(float(ad["monto_ventas"])))
    m3.metric("Conversaciones", f"{int(ad['conversaciones']):,}")
    m4.metric("Tasa Conv → Venta", _fmt_pct_or_dash(ad["tasa_conversion"]))
    m5.metric(
        "Utilidad",
        fmt_cop(float(ad["utilidad"])),
        delta_color="off",
    )


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
# Sidebar: panel de Configuración (escribe a config.json)
# ──────────────────────────────────────────────────────────────────────────────

with st.sidebar.expander("⚙️ Configuración del negocio", expanded=False):
    st.caption(
        "Estos valores se guardan en `config.json`. "
        "Las claves y tokens (`OPENAI_API_KEY`, `FB_*`, `SUPABASE_KEY`) "
        "**NO** se gestionan aquí — viven solo en `.env` por seguridad."
    )
    _cfg_actual = get_config()
    with st.form("config_form", clear_on_submit=False):
        cfg_margen = st.number_input(
            "Margen bruto (decimal, 0–1)",
            min_value=0.0,
            max_value=1.0,
            value=float(_cfg_actual["margen_porcentaje"]),
            step=0.01,
            format="%.2f",
            help="0.30 = 30 %. Usado por el asistente IA para razonar rentabilidad.",
        )
        cfg_cpa_bueno = st.number_input(
            "CPA bueno (COP)",
            min_value=0,
            value=int(_cfg_actual["cpa_bueno"]),
            step=1000,
            help="≤ este valor → 🟢 verde.",
        )
        cfg_cpa_max = st.number_input(
            "CPA máximo (COP)",
            min_value=0,
            value=int(_cfg_actual["cpa_maximo"]),
            step=1000,
            help="Entre bueno y máximo → 🟡; > máximo → 🔴.",
        )
        cfg_roas_min = st.number_input(
            "ROAS mínimo",
            min_value=0.0,
            value=float(_cfg_actual["roas_minimo"]),
            step=0.1,
            format="%.2f",
            help="< este valor → 🔴 rojo.",
        )
        cfg_roas_bueno = st.number_input(
            "ROAS bueno",
            min_value=0.0,
            value=float(_cfg_actual["roas_bueno"]),
            step=0.1,
            format="%.2f",
            help="≥ este valor → 🟢; entre mínimo y bueno → 🟡.",
        )
        cfg_meta = st.number_input(
            "Meta diaria de ganancia (COP)",
            min_value=0,
            value=int(_cfg_actual["meta_ganancia_diaria"]),
            step=10000,
            help="Línea de referencia en la gráfica de ganancia por día.",
        )

        if st.form_submit_button("💾 Guardar configuración"):
            errors = []
            if cfg_cpa_max < cfg_cpa_bueno:
                errors.append("CPA máximo debe ser ≥ CPA bueno.")
            if cfg_roas_bueno < cfg_roas_min:
                errors.append("ROAS bueno debe ser ≥ ROAS mínimo.")
            if errors:
                for e in errors:
                    st.error(e)
            else:
                save_config(
                    {
                        "margen_porcentaje": cfg_margen,
                        "cpa_bueno": cfg_cpa_bueno,
                        "cpa_maximo": cfg_cpa_max,
                        "roas_minimo": cfg_roas_min,
                        "roas_bueno": cfg_roas_bueno,
                        "meta_ganancia_diaria": cfg_meta,
                    }
                )
                st.success("Guardado. Recargando…")
                st.rerun()


# ──────────────────────────────────────────────────────────────────────────────
# Carga de datos
# ──────────────────────────────────────────────────────────────────────────────

df = load_performance(since, until)
daily = load_daily(since, until)
campaigns = load_campaigns(since, until)
accounts = load_accounts(since, until)


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

# Segunda fila: métricas de embudo / mensajería + ganancia.
ganancia_total = monto_ventas_total - gasto_total

col5, col6, col7, col8 = st.columns(4)
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
col8.metric(
    "Ganancia",
    fmt_cop(ganancia_total),
    help="Ventas − Gasto.",
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
# Rendimiento por CUENTA PUBLICITARIA (con semáforo CPA + ROAS)
# ──────────────────────────────────────────────────────────────────────────────

st.subheader("Rendimiento por cuenta publicitaria")
st.caption(
    "Vista agregada por cuenta de Facebook Ads. "
    "Las celdas de CPA y ROAS se pintan según los umbrales del `.env`.  "
    + thresholds_caption()
)

if accounts.empty:
    st.info("Sin datos de cuentas en el rango.")
else:
    acc_display = accounts.rename(
        columns={
            "account_name": "Cuenta",
            "n_anuncios": "Anuncios",
            "gasto": "Gasto",
            "conversaciones": "Conv.",
            "n_ventas": "Ventas",
            "monto_ventas": "Monto ventas",
            "utilidad": "Utilidad",
            "costo_por_conversacion": "Costo/Conv.",
            "cpa": "CPA",
            "tasa_conversion": "% Conv→Venta",
            "roas": "ROAS",
        }
    )[
        [
            "Cuenta",
            "Anuncios",
            "Gasto",
            "Conv.",
            "Ventas",
            "Monto ventas",
            "Utilidad",
            "Costo/Conv.",
            "CPA",
            "% Conv→Venta",
            "ROAS",
        ]
    ]

    styled_accounts = (
        acc_display.style
        .map(_cpa_cell_style, subset=["CPA"])
        .map(_roas_cell_style, subset=["ROAS"])
        .format(
            {
                "Anuncios": "{:,.0f}",
                "Gasto": _fmt_money_or_dash,
                "Conv.": "{:,.0f}",
                "Ventas": "{:,.0f}",
                "Monto ventas": _fmt_money_or_dash,
                "Utilidad": _fmt_money_or_dash,
                "Costo/Conv.": _fmt_money_or_dash,
                "CPA": _fmt_money_or_dash,
                "% Conv→Venta": _fmt_pct_or_dash,
                "ROAS": _fmt_roas_or_dash,
            }
        )
    )
    st.dataframe(styled_accounts, use_container_width=True, hide_index=True)

st.divider()


# ──────────────────────────────────────────────────────────────────────────────
# Rendimiento por CAMPAÑA (con semáforo CPA + ROAS)
# ──────────────────────────────────────────────────────────────────────────────

st.subheader("Rendimiento por campaña")
st.caption(
    "Vista agregada. Las celdas de CPA y ROAS se pintan según los umbrales del "
    "`.env`.  " + thresholds_caption()
)

if campaigns.empty:
    st.info("Sin datos de campañas en el rango.")
else:
    camp_display = campaigns.rename(
        columns={
            "campaign_name": "Campaña",
            "n_anuncios": "Anuncios",
            "gasto": "Gasto",
            "conversaciones": "Conv.",
            "n_ventas": "Ventas",
            "monto_ventas": "Monto ventas",
            "utilidad": "Utilidad",
            "costo_por_conversacion": "Costo/Conv.",
            "cpa": "CPA",
            "tasa_conversion": "% Conv→Venta",
            "roas": "ROAS",
        }
    )[
        [
            "Campaña",
            "Anuncios",
            "Gasto",
            "Conv.",
            "Ventas",
            "Monto ventas",
            "Utilidad",
            "Costo/Conv.",
            "CPA",
            "% Conv→Venta",
            "ROAS",
        ]
    ]

    styled_campaigns = (
        camp_display.style
        .map(_cpa_cell_style, subset=["CPA"])
        .map(_roas_cell_style, subset=["ROAS"])
        .format(
            {
                "Anuncios": "{:,.0f}",
                "Gasto": _fmt_money_or_dash,
                "Conv.": "{:,.0f}",
                "Ventas": "{:,.0f}",
                "Monto ventas": _fmt_money_or_dash,
                "Utilidad": _fmt_money_or_dash,
                "Costo/Conv.": _fmt_money_or_dash,
                "CPA": _fmt_money_or_dash,
                "% Conv→Venta": _fmt_pct_or_dash,
                "ROAS": _fmt_roas_or_dash,
            }
        )
    )
    st.dataframe(styled_campaigns, use_container_width=True, hide_index=True)

st.divider()


# ──────────────────────────────────────────────────────────────────────────────
# Rendimiento por anuncio — filtros + detalle del seleccionado arriba + tabla
# ──────────────────────────────────────────────────────────────────────────────

st.subheader("Rendimiento por anuncio")
st.caption(
    "Filtra por cuenta o busca por nombre. Haz clic en una fila para ver su "
    "semáforo arriba.  " + thresholds_caption()
)

# Contenedor reservado para el panel de detalle (lo llenamos después de
# renderizar la tabla, una vez que sabemos qué fila quedó seleccionada).
detail_container = st.container()

if df.empty:
    with detail_container:
        st.info("Sin anuncios ni ventas en el rango.")
else:
    # ── Filtros ───────────────────────────────────────────────────────────────
    account_names_present = sorted(df["account_name"].dropna().unique().tolist())
    has_sin_cuenta = df["account_name"].isna().any()
    account_options = ["Todas"] + account_names_present
    if has_sin_cuenta:
        account_options.append(NO_ACCOUNT_LABEL)

    f_col1, f_col2 = st.columns([1, 2])
    selected_account = f_col1.selectbox(
        "Cuenta publicitaria",
        options=account_options,
        index=0,
        key="ad_filter_account",
    )
    search_text = f_col2.text_input(
        "Buscar anuncio por nombre",
        value="",
        placeholder="ej. CP2",
        key="ad_filter_search",
    )

    # ── Aplicación de filtros ────────────────────────────────────────────────
    filtered_df = df
    if selected_account == NO_ACCOUNT_LABEL:
        filtered_df = filtered_df[filtered_df["account_name"].isna()]
    elif selected_account != "Todas":
        filtered_df = filtered_df[filtered_df["account_name"] == selected_account]
    if search_text:
        filtered_df = filtered_df[
            filtered_df["ad_name"].str.contains(
                search_text, case=False, na=False, regex=False
            )
        ]
    # Reseteamos índices para que la selección por posición de la tabla mapee
    # 1:1 a `filtered_df.iloc[idx]`.
    filtered_df = filtered_df.reset_index(drop=True)

    st.caption(f"Mostrando **{len(filtered_df)}** de **{len(df)}** filas.")

    if filtered_df.empty:
        with detail_container:
            st.info("Selecciona un anuncio de la tabla para ver su semáforo")
        st.info("Ningún anuncio coincide con los filtros actuales.")
    else:
        # Placeholder para la fila de 4 KPIs (Frecuencia / CPM / Visualizaciones /
        # CTR). Se rellena tras renderizar la tabla — depende de la selección.
        kpi_container = st.container()

        # Misma forma y orden que `filtered_df`, solo sin los ids internos ni
        # las columnas crudas de FB (impressions/reach/clicks) — esas ya las
        # muestra la fila de KPIs.
        table_df = filtered_df.drop(
            columns=[
                "ad_id",
                "account_id",
                "campaign_id",
                "impressions",
                "reach",
                "clicks",
            ]
        )

        table_event = st.dataframe(
            table_df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="ad_table",
            column_config={
                "ad_name": st.column_config.TextColumn("Anuncio", width="large"),
                "account_name": st.column_config.TextColumn("Cuenta"),
                "campaign_name": st.column_config.TextColumn("Campaña"),
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
                "utilidad": st.column_config.NumberColumn(
                    "Utilidad (COP)",
                    format="$%.0f",
                    help="Monto ventas − Gasto. Puede ser negativa.",
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

        selected_rows = (
            table_event.selection.rows
            if table_event is not None and hasattr(table_event, "selection")
            else []
        )

        # Bounds check: si los filtros cambiaron y la selección apunta a una
        # fila que ya no está, tratamos como "sin selección".
        has_valid_selection = (
            bool(selected_rows) and selected_rows[0] < len(filtered_df)
        )

        with detail_container:
            if has_valid_selection:
                render_ad_detail(filtered_df.iloc[selected_rows[0]])
            else:
                st.info("Selecciona un anuncio de la tabla para ver su semáforo")

        # ── KPI row: Frecuencia / CPM / Visualizaciones / CTR ────────────────
        # Si hay selección → datos del anuncio. Si no → agregados del filtro.
        if has_valid_selection:
            ad = filtered_df.iloc[selected_rows[0]]
            kpi_mode_label = (
                f"📍 Datos del anuncio seleccionado: **{ad['ad_name']}**"
            )
            kpi_impressions = int(ad["impressions"])
            kpi_gasto = float(ad["gasto"])
            kpi_clicks = int(ad["clicks"])
            kpi_frecuencia = (
                float(ad["frequency"]) if ad["frequency"] > 0 else None
            )
        else:
            kpi_mode_label = (
                f"📊 Totales generales — {len(filtered_df)} anuncio(s) "
                "del filtro actual."
            )
            kpi_impressions = int(filtered_df["impressions"].sum())
            kpi_reach_total = int(filtered_df["reach"].sum())
            kpi_gasto = float(filtered_df["gasto"].sum())
            kpi_clicks = int(filtered_df["clicks"].sum())
            kpi_frecuencia = (
                kpi_impressions / kpi_reach_total
                if kpi_reach_total > 0
                else None
            )

        # Cálculos con división por cero protegida.
        kpi_cpm = (
            (kpi_gasto / kpi_impressions) * 1000 if kpi_impressions > 0 else None
        )
        kpi_ctr = (
            (kpi_clicks / kpi_impressions) * 100 if kpi_impressions > 0 else None
        )

        with kpi_container:
            st.caption(kpi_mode_label)
            k1, k2, k3, k4 = st.columns(4)
            k1.metric(
                "Frecuencia",
                f"{kpi_frecuencia:.2f}" if kpi_frecuencia is not None else "—",
                help=(
                    "Promedio de veces que cada persona vio el anuncio "
                    "(impresiones ÷ reach)."
                ),
            )
            k2.metric(
                "CPM (COP)",
                fmt_cop(kpi_cpm) if kpi_cpm is not None else "—",
                help="Costo por mil impresiones = (Gasto ÷ Impresiones) × 1000.",
            )
            k3.metric(
                "Visualizaciones",
                f"{kpi_impressions:,}",
                help="Total de impresiones del periodo.",
            )
            k4.metric(
                "CTR",
                f"{kpi_ctr:.2f}%" if kpi_ctr is not None else "—",
                help="Click-through rate = (Clicks ÷ Impresiones) × 100.",
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


st.divider()


# ──────────────────────────────────────────────────────────────────────────────
# Ganancia por día (días de Bogotá, verde = ganancia, rojo = pérdida)
# ──────────────────────────────────────────────────────────────────────────────

st.subheader("Ganancia por día")

if daily.empty:
    st.info("Sin datos diarios en el rango.")
else:
    st.caption(
        "Ganancia diaria = ventas del día − gasto del día.  🟢 ganancia · 🔴 pérdida."
    )

    daily_g = daily.copy()
    daily_g["ganancia"] = daily_g["monto_ventas"] - daily_g["gasto"]
    daily_g["signo"] = daily_g["ganancia"].apply(
        lambda g: "Ganancia" if g >= 0 else "Pérdida"
    )

    fig_profit = px.bar(
        daily_g,
        x="date",
        y="ganancia",
        color="signo",
        color_discrete_map={"Ganancia": "#28a745", "Pérdida": "#dc3545"},
        category_orders={"signo": ["Ganancia", "Pérdida"]},
        labels={"date": "Día", "ganancia": "Ganancia (COP)", "signo": ""},
    )
    # Línea de referencia con la meta diaria configurada.
    meta_diaria = float(get_config()["meta_ganancia_diaria"])
    if meta_diaria > 0:
        fig_profit.add_hline(
            y=meta_diaria,
            line_dash="dash",
            line_color="#0066cc",
            annotation_text=f"Meta diaria: ${meta_diaria:,.0f}",
            annotation_position="top right",
            annotation_font_color="#0066cc",
        )
    fig_profit.update_layout(
        legend_title_text="",
        yaxis_tickformat=",.0f",
        hovermode="x unified",
    )
    fig_profit.update_traces(hovertemplate="$%{y:,.0f} COP")
    st.plotly_chart(fig_profit, use_container_width=True)

    # Resumen al pie: días positivos / negativos, días sobre la meta y acumulado.
    n_pos = int((daily_g["ganancia"] > 0).sum())
    n_neg = int((daily_g["ganancia"] < 0).sum())
    n_meta = int((daily_g["ganancia"] >= meta_diaria).sum()) if meta_diaria > 0 else None
    total_periodo = float(daily_g["ganancia"].sum())
    meta_str = (
        f"  ·  días sobre meta ({fmt_cop(meta_diaria)}): **{n_meta}**"
        if n_meta is not None
        else ""
    )
    st.caption(
        f"Días con ganancia: **{n_pos}**  ·  con pérdida: **{n_neg}**{meta_str}  "
        f"·  acumulada del periodo: **{fmt_cop(total_periodo)}**."
    )


st.divider()


# ──────────────────────────────────────────────────────────────────────────────
# Tasa de conversión por día (ventas ÷ conversaciones, día de Bogotá)
# ──────────────────────────────────────────────────────────────────────────────

st.subheader("Tasa de conversión por día")

if daily.empty:
    st.info("Sin datos diarios en el rango.")
elif daily["conversaciones"].sum() == 0:
    st.info("Sin conversaciones en el rango — no se puede calcular la tasa.")
else:
    st.caption(
        "Tasa de conversión diaria = (nº ventas del día ÷ conversaciones del "
        "día) × 100. Los días con 0 conversaciones se omiten de la línea."
    )

    daily_r = daily.copy()
    # División segura: NaN cuando conversaciones = 0 (en vez de error / inf).
    daily_r["tasa"] = np.where(
        daily_r["conversaciones"] > 0,
        100.0 * daily_r["n_ventas"] / daily_r["conversaciones"].replace(0, np.nan),
        np.nan,
    )

    fig_rate = px.line(
        daily_r,
        x="date",
        y="tasa",
        markers=True,
        labels={"date": "Día", "tasa": "Tasa de conversión (%)"},
        color_discrete_sequence=["#00CC96"],
    )
    fig_rate.update_layout(
        hovermode="x unified",
        yaxis_tickformat=".1f",
        yaxis_ticksuffix="%",
    )
    # Los NaN (días sin conversaciones) cortan la línea; con connectgaps=False
    # se ven los huecos sin extrapolar.
    fig_rate.update_traces(
        connectgaps=False,
        hovertemplate="%{y:.1f}%",
    )
    st.plotly_chart(fig_rate, use_container_width=True)


st.divider()


# ──────────────────────────────────────────────────────────────────────────────
# Asistente IA (OpenAI)
# ──────────────────────────────────────────────────────────────────────────────

st.subheader("💬 Asistente IA")
st.caption(
    f"Analista experto en tráfico de pago. Tiene acceso a los datos actuales "
    f"del dashboard y a tus objetivos. Modelo: `{OPENAI_MODEL}`."
)

if OpenAI is None:
    st.error(
        "El paquete `openai` no está instalado. Corre: "
        "`pip install -r requirements.txt`"
    )
elif not OPENAI_API_KEY:
    st.info(
        "Define `OPENAI_API_KEY` en tu `.env` y reinicia Streamlit para "
        "activar el asistente."
    )
else:
    # Historial vive en session_state.
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []

    # Botón para limpiar la conversación.
    if st.session_state.chat_messages:
        if st.button("🗑️ Limpiar conversación", key="clear_chat"):
            st.session_state.chat_messages = []
            st.rerun()

    # Render del historial.
    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Input nuevo.
    user_input = st.chat_input("Pregúntame sobre tus datos…")
    if user_input:
        # Pintamos el mensaje del usuario inmediatamente.
        st.session_state.chat_messages.append(
            {"role": "user", "content": user_input}
        )
        with st.chat_message("user"):
            st.markdown(user_input)

        # System prompt + datos actuales (regenerados en cada turno para que
        # el bot siempre vea los datos frescos del filtro/periodo activo).
        full_system = (
            build_system_prompt()
            + "\n\n"
            + build_data_context(df, campaigns, accounts, daily, since, until)
        )
        api_messages = [{"role": "system", "content": full_system}]
        api_messages.extend(st.session_state.chat_messages)

        # Llamada a OpenAI con manejo de errores.
        with st.chat_message("assistant"):
            try:
                client = OpenAI(api_key=OPENAI_API_KEY)
                with st.spinner("Pensando…"):
                    response = client.chat.completions.create(
                        model=OPENAI_MODEL,
                        messages=api_messages,
                    )
                reply = (response.choices[0].message.content or "").strip()
                if not reply:
                    reply = "_(respuesta vacía)_"
                st.markdown(reply)
                st.session_state.chat_messages.append(
                    {"role": "assistant", "content": reply}
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"❌ Error al llamar a OpenAI: {exc}")
                st.caption(
                    f"Si dice `model_not_found`, cambia `OPENAI_MODEL` "
                    f"en `app.py` (ahora: `{OPENAI_MODEL}`). "
                    f"Otros valores: `gpt-5-mini`, `gpt-4o`."
                )
