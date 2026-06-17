"""Dashboard de Rentabilidad por Anuncio — Streamlit.

Cruza el gasto de Facebook Ads con las ventas de Supabase por `ad_id` y muestra
KPIs, evolución diaria, tabla por anuncio y ranking de los que más venden.
Todo en pesos colombianos (COP).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Permite importar desde src/ sin necesidad de __init__.py.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import get_config, save_config  # noqa: E402
from facebook_client import (  # noqa: E402
    clear_account_cache,
    get_daily_spend_by_ad,
    sync_ads_to_supabase,
)
from metrics import (  # noqa: E402
    NO_ACCOUNT_LABEL,
    NO_CAMPAIGN_LABEL,
    NO_CAMPAIGN_SENTINEL,
    NO_ACCOUNT_SENTINEL,
    SIN_NOMBRE,
    aggregate_by,
    cpa_status,
    get_ad_performance,
    get_daily_totals,
    get_rolling_roas_by_ad,
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

# Todas las fechas visibles se calculan en día calendario de Bogotá (UTC-5).
BOGOTA = ZoneInfo("America/Bogota")


@st.cache_data(ttl=CACHE_TTL, show_spinner="Cargando rendimiento por anuncio…")
def load_performance(since: str, until: str) -> pd.DataFrame:
    return get_ad_performance(since, until)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Cargando ROAS reciente…")
def load_rolling(today: str) -> pd.DataFrame:
    """ROAS por anuncio en ventanas móviles de 3 y 7 días (cacheado por día)."""
    return get_rolling_roas_by_ad(today)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Cargando gasto diario por anuncio…")
def load_daily_spend_by_ad(since: str, until: str) -> pd.DataFrame:
    """Gasto diario por anuncio (FB), cacheado con TTL y refrescable.

    Se cachea aquí —y NO con `@lru_cache` en facebook_client— para que el
    botón "🔄 Refrescar datos" (que llama `st.cache_data.clear()`) y el TTL de
    10 min lo actualicen. Una sola llamada a FB por refresco, compartida por
    todas las tabs de país.
    """
    return get_daily_spend_by_ad(since, until)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Cargando evolución diaria…")
def load_daily(
    since: str,
    until: str,
    ad_ids: tuple[str, ...] | None = None,
    pais: str | None = None,
) -> pd.DataFrame:
    # Para las tabs de país (ad_ids dado) traemos el gasto-por-anuncio ya
    # cacheado y se lo pasamos a get_daily_totals, que solo lo filtra.
    spend_by_ad = (
        load_daily_spend_by_ad(since, until) if ad_ids else None
    )
    return get_daily_totals(
        since, until, set(ad_ids) if ad_ids else None, pais, spend_by_ad
    )



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
    ad_df = df.copy()
    ad_df["account_id"] = ad_df["account_id"].fillna(NO_ACCOUNT_SENTINEL)
    ad_df["account_name"] = ad_df["account_name"].fillna(NO_ACCOUNT_LABEL)
    accounts = aggregate_by(ad_df, ["account_id", "account_name"])

    ad_df2 = df.copy()
    ad_df2["campaign_id"] = ad_df2["campaign_id"].fillna(NO_CAMPAIGN_SENTINEL)
    ad_df2["campaign_name"] = ad_df2["campaign_name"].fillna(NO_CAMPAIGN_LABEL)
    campaigns = aggregate_by(ad_df2, ["campaign_id", "campaign_name"])

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
    m2.metric("Ventas (COP)", fmt_cop(float(ad["monto_ventas"])))
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

# "Hoy" en Bogotá, no en la timezone del servidor (que puede ser UTC).
today = datetime.now(BOGOTA).date()
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

if st.sidebar.button(
    "🔁 Refrescar cuentas",
    help="Vuelve a descubrir las cuentas publicitarias en Facebook. Úsalo "
    "cuando agregues una cuenta nueva al Business Manager y no aparezca "
    "todavía (sin reiniciar la app).",
):
    clear_account_cache()
    st.cache_data.clear()
    st.sidebar.success("Cuentas re-descubiertas. Recargando…")
    st.rerun()

if st.sidebar.button("📥 Sincronizar anuncios con Facebook"):
    with st.spinner("Sincronizando anuncios con Supabase…"):
        sync_result = sync_ads_to_supabase(since, until)
    if sync_result["errors"]:
        for _err in sync_result["errors"]:
            st.sidebar.error(f"❌ {_err}")
    else:
        _already = sync_result["requested"] - sync_result["new"]
        st.sidebar.success(
            f"✅ {sync_result['new']} anuncios nuevos insertados.\n\n"
            f"({sync_result['requested']} en el rango; "
            f"{_already} ya existían y no se tocaron.)"
        )


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

# ROAS de ventanas móviles (últimos 3 y 7 días desde HOY en Bogotá), fijo e
# independiente del rango seleccionado. Se cruza por ad_id; los anuncios sin
# actividad reciente quedan con gasto_3d/7d = 0 → ROAS NaN (semáforo gris).
today_str = datetime.now(BOGOTA).date().isoformat()
rolling = load_rolling(today_str)
if not df.empty:
    df["ad_id"] = df["ad_id"].astype(str)
    df = df.merge(rolling, on="ad_id", how="left")


# ──────────────────────────────────────────────────────────────────────────────
# Header
# ──────────────────────────────────────────────────────────────────────────────

st.title("Dashboard de Rentabilidad por Anuncio")
st.caption(
    f"Periodo: **{since}** → **{until}** · "
    f"Todos los valores convertidos a COP"
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers para renderizar secciones reutilizables por país
# ──────────────────────────────────────────────────────────────────────────────

_PAISES_ORDEN = [
    "Colombia", "Perú", "Chile", "México", "Venezuela", "Costa Rica", "Ecuador",
]

_PAIS_BANDERAS = {
    "Colombia": "🇨🇴",
    "Perú": "🇵🇪",
    "Chile": "🇨🇱",
    "México": "🇲🇽",
    "Venezuela": "🇻🇪",
    "Costa Rica": "🇨🇷",
    "Ecuador": "🇪🇨",
}


def _estado_badge(estado: str, ad_name: str) -> str:
    if ad_name == SIN_NOMBRE:
        return "⚠️ Sin datos"
    return {
        "prendido": "🟢 Prendido",
        "apagado": "🔴 Apagado",
        "otro": "⚫ Otro",
    }.get(estado, "⚫ Otro")


def _render_kpis(df_country: pd.DataFrame, conv_total: int) -> None:
    """Renderiza las 2 filas de KPIs a partir de un DataFrame de anuncios.

    `conv_total` es el conteo de conversaciones del país (de `contactos`
    filtrado por su columna `pais`), que se pasa explícito para que el KPI y
    el gráfico de conversaciones por día sean consistentes — y NO dependan de
    la atribución por ad_id.
    """
    gasto_total = float(df_country["gasto"].sum())
    monto_ventas_total = float(df_country["monto_ventas"].sum())
    n_ventas_total = int(df_country["n_ventas"].sum())
    roas_global = monto_ventas_total / gasto_total if gasto_total > 0 else None
    cpa_global = gasto_total / n_ventas_total if n_ventas_total > 0 else None
    cost_per_conv = gasto_total / conv_total if conv_total > 0 else None
    tasa_conv = (100 * n_ventas_total / conv_total) if conv_total > 0 else None
    ganancia_total = monto_ventas_total - gasto_total

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Gasto total", fmt_cop(gasto_total))
    col2.metric("Ventas totales (COP)", fmt_cop(monto_ventas_total))
    col3.metric(
        "ROAS global",
        f"{roas_global:.2f}" if roas_global is not None else "—",
        help="Monto de ventas ÷ Gasto.",
    )
    col4.metric(
        "Nº ventas (CPA)",
        f"{n_ventas_total}",
        delta=fmt_cop(cpa_global) if cpa_global is not None else None,
        delta_color="off",
        help="Total de ventas. Debajo, el CPA global (costo por venta).",
    )

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Conversaciones", f"{conv_total:,}")
    col6.metric(
        "Costo / conversación",
        fmt_cop(cost_per_conv) if cost_per_conv is not None else "—",
    )
    col7.metric(
        "Tasa Conv → Venta",
        f"{tasa_conv:.1f}%" if tasa_conv is not None else "—",
    )
    col8.metric("Ganancia", fmt_cop(ganancia_total), help="Ventas − Gasto.")


def _render_accounts_table(df_country: pd.DataFrame) -> None:
    """Tabla de rendimiento por cuenta derivada del DataFrame filtrado."""
    ad_df = df_country.copy()
    ad_df["account_id"] = ad_df["account_id"].fillna(NO_ACCOUNT_SENTINEL)
    ad_df["account_name"] = ad_df["account_name"].fillna(NO_ACCOUNT_LABEL)
    accounts = aggregate_by(ad_df, ["account_id", "account_name"])

    st.subheader("Rendimiento por cuenta publicitaria")
    st.caption(thresholds_caption())

    if accounts.empty:
        st.info("Sin datos de cuentas.")
        return

    acc_display = accounts.rename(
        columns={
            "account_name": "Cuenta",
            "n_anuncios": "Anuncios",
            "gasto": "Gasto",
            "conversaciones": "Conv.",
            "n_ventas": "Ventas",
            "monto_ventas": "Ventas (COP)",
            "utilidad": "Utilidad",
            "costo_por_conversacion": "Costo/Conv.",
            "cpa": "CPA",
            "tasa_conversion": "% Conv→Venta",
            "roas": "ROAS",
            "roas_3d": "ROAS 3d",
            "roas_7d": "ROAS 7d",
        }
    )[
        [
            "Cuenta", "Anuncios", "Gasto", "Conv.", "Ventas",
            "Ventas (COP)", "Utilidad", "Costo/Conv.", "CPA",
            "% Conv→Venta", "ROAS", "ROAS 3d", "ROAS 7d",
        ]
    ]
    styled = (
        acc_display.style
        .map(_cpa_cell_style, subset=["CPA"])
        .map(_roas_cell_style, subset=["ROAS", "ROAS 3d", "ROAS 7d"])
        .format(
            {
                "Anuncios": "{:,.0f}",
                "Gasto": _fmt_money_or_dash,
                "Conv.": "{:,.0f}",
                "Ventas": "{:,.0f}",
                "Ventas (COP)": _fmt_money_or_dash,
                "Utilidad": _fmt_money_or_dash,
                "Costo/Conv.": _fmt_money_or_dash,
                "CPA": _fmt_money_or_dash,
                "% Conv→Venta": _fmt_pct_or_dash,
                "ROAS": _fmt_roas_or_dash,
                "ROAS 3d": _fmt_roas_or_dash,
                "ROAS 7d": _fmt_roas_or_dash,
            }
        )
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)


def _render_campaigns_table(df_country: pd.DataFrame) -> None:
    """Tabla de rendimiento por campaña derivada del DataFrame filtrado."""
    ad_df = df_country.copy()
    ad_df["campaign_id"] = ad_df["campaign_id"].fillna(NO_CAMPAIGN_SENTINEL)
    ad_df["campaign_name"] = ad_df["campaign_name"].fillna(NO_CAMPAIGN_LABEL)
    campaigns = aggregate_by(ad_df, ["campaign_id", "campaign_name"])

    st.subheader("Rendimiento por campaña")
    st.caption(thresholds_caption())

    if campaigns.empty:
        st.info("Sin datos de campañas.")
        return

    camp_display = campaigns.rename(
        columns={
            "campaign_name": "Campaña",
            "n_anuncios": "Anuncios",
            "gasto": "Gasto",
            "conversaciones": "Conv.",
            "n_ventas": "Ventas",
            "monto_ventas": "Ventas (COP)",
            "utilidad": "Utilidad",
            "costo_por_conversacion": "Costo/Conv.",
            "cpa": "CPA",
            "tasa_conversion": "% Conv→Venta",
            "roas": "ROAS",
            "roas_3d": "ROAS 3d",
            "roas_7d": "ROAS 7d",
        }
    )[
        [
            "Campaña", "Anuncios", "Gasto", "Conv.", "Ventas",
            "Ventas (COP)", "Utilidad", "Costo/Conv.", "CPA",
            "% Conv→Venta", "ROAS", "ROAS 3d", "ROAS 7d",
        ]
    ]
    styled = (
        camp_display.style
        .map(_cpa_cell_style, subset=["CPA"])
        .map(_roas_cell_style, subset=["ROAS", "ROAS 3d", "ROAS 7d"])
        .format(
            {
                "Anuncios": "{:,.0f}",
                "Gasto": _fmt_money_or_dash,
                "Conv.": "{:,.0f}",
                "Ventas": "{:,.0f}",
                "Ventas (COP)": _fmt_money_or_dash,
                "Utilidad": _fmt_money_or_dash,
                "Costo/Conv.": _fmt_money_or_dash,
                "CPA": _fmt_money_or_dash,
                "% Conv→Venta": _fmt_pct_or_dash,
                "ROAS": _fmt_roas_or_dash,
                "ROAS 3d": _fmt_roas_or_dash,
                "ROAS 7d": _fmt_roas_or_dash,
            }
        )
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)


def _render_anuncios_section(df_country: pd.DataFrame, key_suffix: str) -> None:
    """Sección de rendimiento por anuncio: filtros, tabla con badge, detalle."""
    st.subheader("Rendimiento por anuncio")
    st.caption(
        "Filtra por cuenta o busca por nombre. Haz clic en una fila para ver "
        "su semáforo.  " + thresholds_caption()
    )

    if df_country.empty:
        st.info("Sin anuncios ni ventas.")
        return

    account_names_present = sorted(
        df_country["account_name"].dropna().unique().tolist()
    )
    has_sin_cuenta = df_country["account_name"].isna().any()
    account_options = ["Todas"] + account_names_present
    if has_sin_cuenta:
        account_options.append(NO_ACCOUNT_LABEL)

    f_col1, f_col2 = st.columns([1, 2])
    selected_account = f_col1.selectbox(
        "Cuenta publicitaria",
        options=account_options,
        index=0,
        key=f"ad_filter_account_{key_suffix}",
    )
    search_text = f_col2.text_input(
        "Buscar anuncio por nombre",
        value="",
        placeholder="ej. CP2",
        key=f"ad_filter_search_{key_suffix}",
    )

    filtered_df = df_country
    if selected_account == NO_ACCOUNT_LABEL:
        filtered_df = filtered_df[filtered_df["account_name"].isna()]
    elif selected_account != "Todas":
        filtered_df = filtered_df[
            filtered_df["account_name"] == selected_account
        ]
    if search_text:
        filtered_df = filtered_df[
            filtered_df["ad_name"].str.contains(
                search_text, case=False, na=False, regex=False
            )
        ]
    filtered_df = filtered_df.reset_index(drop=True)

    if filtered_df.empty:
        st.info("Ningún anuncio coincide con los filtros actuales.")
        return

    detail_container = st.container()

    estado_label = st.radio(
        "Estado del anuncio",
        ["Todos", "🟢 Prendidos", "🔴 Apagados", "⚠️ Sin datos FB", "Otros"],
        horizontal=True,
        key=f"estado_filter_{key_suffix}",
    )

    if estado_label == "🟢 Prendidos":
        df_view = filtered_df[
            (filtered_df["estado"] == "prendido")
            & (filtered_df["ad_name"] != SIN_NOMBRE)
        ]
    elif estado_label == "🔴 Apagados":
        df_view = filtered_df[
            (filtered_df["estado"] == "apagado")
            & (filtered_df["ad_name"] != SIN_NOMBRE)
        ]
    elif estado_label == "⚠️ Sin datos FB":
        df_view = filtered_df[filtered_df["ad_name"] == SIN_NOMBRE]
    elif estado_label == "Otros":
        df_view = filtered_df[
            (filtered_df["estado"] == "otro")
            & (filtered_df["ad_name"] != SIN_NOMBRE)
        ]
    else:
        df_view = filtered_df

    df_view = df_view.copy()
    df_view["_sin_fb"] = (df_view["ad_name"] == SIN_NOMBRE).astype(int)
    df_view = df_view.sort_values(
        by=["_sin_fb", "gasto", "monto_ventas"],
        ascending=[True, False, False],
        na_position="last",
    ).drop(columns=["_sin_fb"]).reset_index(drop=True)

    hide_no_fb = st.checkbox(
        "Ocultar anuncios sin datos de FB",
        value=False,
        key=f"hide_no_fb_{key_suffix}",
    )
    if hide_no_fb:
        df_view = df_view[df_view["ad_name"] != SIN_NOMBRE].reset_index(
            drop=True
        )

    st.caption(f"Mostrando **{len(df_view)}** anuncio(s).")

    if df_view.empty:
        with detail_container:
            st.info("Selecciona un anuncio de la tabla para ver su semáforo")
        st.info("Ningún anuncio coincide con los filtros actuales.")
        return

    kpi_container = st.container()

    table_df = df_view.copy()
    table_df.insert(
        0,
        "estado_badge",
        table_df.apply(
            lambda r: _estado_badge(r["estado"], r["ad_name"]), axis=1
        ),
    )
    drop_cols = [
        "ad_id", "account_id", "campaign_id", "impressions", "reach",
        "clicks", "estado", "estado_raw", "pais", "pais_inconsistente",
        "gasto_3d", "ventas_3d", "gasto_7d", "ventas_7d",
    ]
    table_df = table_df.drop(
        columns=[c for c in drop_cols if c in table_df.columns]
    )

    # Ubicar ROAS 3d / 7d justo después de ROAS para leerlos juntos.
    cols = list(table_df.columns)
    for c in ("roas_3d", "roas_7d"):
        if c in cols:
            cols.remove(c)
    if "roas" in cols:
        i = cols.index("roas") + 1
        cols[i:i] = [c for c in ("roas_3d", "roas_7d") if c in table_df.columns]
    table_df = table_df[cols]

    table_event = st.dataframe(
        table_df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"ad_table_{key_suffix}",
        column_config={
            "estado_badge": st.column_config.TextColumn(
                "Estado", width="small"
            ),
            "ad_name": st.column_config.TextColumn("Anuncio", width="large"),
            "account_name": st.column_config.TextColumn("Cuenta"),
            "campaign_name": st.column_config.TextColumn("Campaña"),
            "cuenta_perfil": st.column_config.TextColumn("Perfil"),
            "gasto": st.column_config.NumberColumn(
                "Gasto (COP)", format="$%.0f"
            ),
            "frequency": st.column_config.NumberColumn(
                "Frecuencia", format="%.2f"
            ),
            "conversaciones": st.column_config.NumberColumn(
                "Conv.", format="%d"
            ),
            "n_ventas": st.column_config.NumberColumn("Nº ventas", format="%d"),
            "monto_ventas": st.column_config.NumberColumn(
                "Ventas (COP)", format="$%.0f"
            ),
            "ventas_local_info": st.column_config.TextColumn("Ventas (local)"),
            "utilidad": st.column_config.NumberColumn(
                "Utilidad (COP)", format="$%.0f"
            ),
            "costo_por_conversacion": st.column_config.NumberColumn(
                "Costo/Conv. (COP)", format="$%.0f"
            ),
            "cpa": st.column_config.NumberColumn("CPA (COP)", format="$%.0f"),
            "tasa_conversion": st.column_config.NumberColumn(
                "% Conv→Venta", format="%.1f%%"
            ),
            "roas": st.column_config.NumberColumn("ROAS", format="%.2f"),
            "roas_3d": st.column_config.NumberColumn(
                "ROAS 3d",
                format="%.2f",
                help="ROAS de los últimos 3 días (hoy y los 2 anteriores), "
                "fijo e independiente del rango seleccionado.",
            ),
            "roas_7d": st.column_config.NumberColumn(
                "ROAS 7d",
                format="%.2f",
                help="ROAS de los últimos 7 días, fijo e independiente del "
                "rango seleccionado.",
            ),
        },
    )

    selected_rows = (
        table_event.selection.rows
        if table_event is not None and hasattr(table_event, "selection")
        else []
    )
    has_valid_selection = (
        bool(selected_rows) and selected_rows[0] < len(df_view)
    )

    with detail_container:
        if has_valid_selection:
            render_ad_detail(df_view.iloc[selected_rows[0]])
        else:
            st.info("Selecciona un anuncio de la tabla para ver su semáforo")

    if has_valid_selection:
        ad = df_view.iloc[selected_rows[0]]
        kpi_mode_label = (
            f"📍 Datos del anuncio seleccionado: **{ad['ad_name']}**"
        )
        kpi_impressions = int(ad["impressions"])
        kpi_gasto = float(ad["gasto"])
        kpi_clicks = int(ad["clicks"])
        kpi_freq = float(ad["frequency"]) if ad["frequency"] > 0 else None
    else:
        kpi_mode_label = (
            f"📊 Totales — {len(df_view)} anuncio(s) del filtro actual."
        )
        kpi_impressions = int(df_view["impressions"].sum())
        kpi_reach_total = int(df_view["reach"].sum())
        kpi_gasto = float(df_view["gasto"].sum())
        kpi_clicks = int(df_view["clicks"].sum())
        kpi_freq = (
            kpi_impressions / kpi_reach_total if kpi_reach_total > 0 else None
        )

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
            f"{kpi_freq:.2f}" if kpi_freq is not None else "—",
        )
        k2.metric(
            "CPM (COP)",
            fmt_cop(kpi_cpm) if kpi_cpm is not None else "—",
        )
        k3.metric("Visualizaciones", f"{kpi_impressions:,}")
        k4.metric(
            "CTR",
            f"{kpi_ctr:.2f}%" if kpi_ctr is not None else "—",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Evolución diaria (helpers — se renderizan dentro de cada tab)
# ──────────────────────────────────────────────────────────────────────────────


def _render_daily_charts(daily_df: pd.DataFrame, key_suffix: str) -> None:
    """Gráficas de gasto/ventas y conversaciones por día."""
    st.subheader("Gasto y ventas por día")

    if daily_df.empty or (
        daily_df["gasto"].sum() == 0 and daily_df["monto_ventas"].sum() == 0
    ):
        st.info("Sin gasto ni ventas en el rango seleccionado.")
    else:
        daily_long = daily_df.melt(
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
        st.plotly_chart(
            fig_daily, use_container_width=True, key=f"daily_line_{key_suffix}"
        )

    st.subheader("Conversaciones por día")
    st.caption(
        "Total de contactos por día de Bogotá (tabla `contactos` de Supabase)."
    )

    if daily_df.empty or daily_df["conversaciones"].sum() == 0:
        st.info("Sin conversaciones en el rango.")
    else:
        fig_conv = px.bar(
            daily_df,
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
        st.plotly_chart(
            fig_conv, use_container_width=True, key=f"daily_conv_{key_suffix}"
        )


def _render_extra_sections(
    df_country: pd.DataFrame, daily_country: pd.DataFrame, key_suffix: str
) -> None:
    """Top anuncios, embudo, ganancia por día y tasa de conversión por día,
    todos filtrados al país de la pestaña (df_country / daily_country)."""

    # ── Top anuncios por ventas ─────────────────────────────────────────────
    st.subheader("Top anuncios por ventas")
    top_n = st.slider(
        "¿Cuántos mostrar?",
        min_value=5,
        max_value=30,
        value=15,
        step=5,
        key=f"top_n_{key_suffix}",
    )
    top = df_country[df_country["monto_ventas"] > 0].head(top_n)

    if top.empty:
        st.info("Aún no hay anuncios con ventas en este rango.")
    else:
        top = top.copy()
        top["label"] = top["ad_name"].apply(
            lambda s: s if len(s) <= 50 else s[:47] + "…"
        )
        fig_top = px.bar(
            top.iloc[::-1],
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
        st.plotly_chart(
            fig_top, use_container_width=True, key=f"top_ads_{key_suffix}"
        )

    st.divider()

    # ── Embudo: conversaciones → ventas por anuncio ─────────────────────────
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
        key=f"funnel_n_{key_suffix}",
    )

    funnel_df = (
        df_country[(df_country["conversaciones"] > 0) | (df_country["n_ventas"] > 0)]
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
        st.plotly_chart(
            fig_funnel, use_container_width=True, key=f"funnel_{key_suffix}"
        )

    st.divider()

    # ── Ganancia por día ────────────────────────────────────────────────────
    st.subheader("Ganancia por día")

    if daily_country.empty:
        st.info("Sin datos diarios en el rango.")
    else:
        st.caption(
            "Ganancia diaria = ventas del día − gasto del día.  🟢 ganancia · 🔴 pérdida."
        )

        daily_g = daily_country.copy()
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
        st.plotly_chart(
            fig_profit, use_container_width=True, key=f"profit_{key_suffix}"
        )

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

    # ── Tasa de conversión por día ──────────────────────────────────────────
    st.subheader("Tasa de conversión por día")

    if daily_country.empty:
        st.info("Sin datos diarios en el rango.")
    elif daily_country["conversaciones"].sum() == 0:
        st.info("Sin conversaciones en el rango — no se puede calcular la tasa.")
    else:
        st.caption(
            "Tasa de conversión diaria = (nº ventas del día ÷ conversaciones del "
            "día) × 100. Los días con 0 conversaciones se omiten de la línea."
        )

        daily_r = daily_country.copy()
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
        fig_rate.update_traces(
            connectgaps=False,
            hovertemplate="%{y:.1f}%",
        )
        st.plotly_chart(
            fig_rate, use_container_width=True, key=f"rate_{key_suffix}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Tabs de país en la parte superior: KPIs + Gráficas + Cuentas + Campañas + Anuncios
# ──────────────────────────────────────────────────────────────────────────────

if df.empty:
    st.info("Sin anuncios ni ventas en el rango.")
else:
    tab_labels = ["🌎 Todos"]
    all_paises = ["Todos"]
    for p in _PAISES_ORDEN:
        if (df["pais"] == p).any():
            flag = _PAIS_BANDERAS.get(p, "")
            tab_labels.append(f"{flag} {p}")
            all_paises.append(p)

    country_tabs = st.tabs(tab_labels)

    for tab, pais in zip(country_tabs, all_paises):
        with tab:
            if pais == "Todos":
                df_country = df
                daily_country = daily
            else:
                df_country = df[df["pais"] == pais].reset_index(drop=True)
                country_ad_ids = tuple(
                    sorted(df_country["ad_id"].dropna().unique().tolist())
                )
                # Gasto/ventas por ad_id; conversaciones por contactos.pais.
                daily_country = load_daily(
                    since, until, ad_ids=country_ad_ids, pais=pais
                )

            # Conversaciones del país: del daily (contactos filtrado por pais),
            # para que KPI y gráfico de conversaciones por día coincidan.
            conv_total = int(daily_country["conversaciones"].sum())

            if "pais_inconsistente" in df_country.columns:
                n_incon = int(df_country["pais_inconsistente"].sum())
                if n_incon > 0:
                    st.warning(
                        f"⚠️ {n_incon} anuncio(s) tienen país diferente en "
                        f"Supabase vs nombre de campaña. Se usa el de Supabase."
                    )

            _render_kpis(df_country, conv_total)
            st.divider()
            _render_daily_charts(
                daily_country, key_suffix=pais.lower().replace(" ", "_")
            )
            st.divider()
            _render_accounts_table(df_country)
            st.divider()
            _render_campaigns_table(df_country)
            st.divider()
            _render_anuncios_section(
                df_country, key_suffix=pais.lower().replace(" ", "_")
            )
            st.divider()
            _render_extra_sections(
                df_country,
                daily_country,
                key_suffix=pais.lower().replace(" ", "_"),
            )

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
            + build_data_context(df, daily, since, until)
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
