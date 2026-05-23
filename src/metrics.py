"""Cruce de Facebook Ads + Supabase por `ad_id`. El corazón del proyecto.

Tres fuentes:
- Facebook (`get_ad_spend`, `get_daily_spend`): gasto + frecuencia por anuncio.
- Supabase `compradores` (`get_sales`): ventas.
- Supabase `contactos` (`get_contactos`): conversaciones REALES de WhatsApp.

Las conversaciones SIEMPRE vienen de la tabla `contactos` (más confiables que
las que reporta Facebook). La frecuencia sí viene de Facebook.

Métricas calculadas por anuncio:
- ROAS                   = monto_ventas / gasto
- CPA / costo por venta  = gasto / n_ventas
- costo por conversación = gasto / conversaciones
- tasa de conversión (%) = 100 · n_ventas / conversaciones
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Permite ejecutar `python src/metrics.py` desde la raíz del proyecto.
sys.path.insert(0, str(Path(__file__).parent))

from config import get_config  # noqa: E402
from facebook_client import get_ad_spend, get_daily_spend  # noqa: E402
from supabase_client import get_contactos, get_sales  # noqa: E402

SIN_NOMBRE = "(sin datos en Facebook)"
NO_AD_LABEL = "Sin anuncio (no atribuido)"
NO_CAMPAIGN_LABEL = "Sin campaña"
NO_ACCOUNT_LABEL = "Sin cuenta"
# Sentinelas usados como id ficticio para las filas "sin atribuir".
# Empiezan por "_" para no chocar con ningún id real de Facebook.
NO_AD_SENTINEL = "_sin_anuncio_"
NO_CAMPAIGN_SENTINEL = "_sin_campana_"
NO_ACCOUNT_SENTINEL = "_sin_cuenta_"

# Cualquier agrupación o cruce por fecha se hace en día calendario de Bogotá.
BOGOTA = "America/Bogota"


# ──────────────────────────────────────────────────────────────────────────────
# Semáforo de CPA y ROAS — umbrales vivienen en config.json (UI de Configuración)
# ──────────────────────────────────────────────────────────────────────────────


def cpa_status(cpa: float | None) -> str:
    """Clasifica un CPA. Devuelve 'verde' | 'amarillo' | 'rojo' | 'sin_datos'.

    Los umbrales se leen de `config.json` (gestionado por el panel
    "Configuración" del dashboard).
    """
    if cpa is None or pd.isna(cpa):
        return "sin_datos"
    cfg = get_config()
    if cpa <= cfg["cpa_bueno"]:
        return "verde"
    if cpa <= cfg["cpa_maximo"]:
        return "amarillo"
    return "rojo"


def roas_status(roas: float | None) -> str:
    """Clasifica un ROAS. Devuelve 'verde' | 'amarillo' | 'rojo' | 'sin_datos'."""
    if roas is None or pd.isna(roas):
        return "sin_datos"
    cfg = get_config()
    if roas < cfg["roas_minimo"]:
        return "rojo"
    if roas < cfg["roas_bueno"]:
        return "amarillo"
    return "verde"


def _conversaciones_por_ad(contactos_df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Cuenta contactos por `primer_ad_id`. Devuelve (df_por_ad, n_sin_atribuir).

    `df_por_ad` tiene columnas: ad_id (string), conversaciones (int). Solo
    incluye contactos CON `primer_ad_id`. `n_sin_atribuir` es el conteo de
    contactos con `primer_ad_id` nulo.
    """
    if contactos_df.empty:
        return pd.DataFrame(columns=["ad_id", "conversaciones"]), 0

    sin_atribuir = int(contactos_df["primer_ad_id"].isna().sum())
    atribuidos = contactos_df.dropna(subset=["primer_ad_id"]).copy()
    if atribuidos.empty:
        return pd.DataFrame(columns=["ad_id", "conversaciones"]), sin_atribuir

    atribuidos["ad_id"] = atribuidos["primer_ad_id"].astype(str)
    agg = (
        atribuidos.groupby("ad_id", as_index=False)
        .size()
        .rename(columns={"size": "conversaciones"})
    )
    return agg, sin_atribuir


def get_ad_performance(since: str, until: str) -> pd.DataFrame:
    """Devuelve un DataFrame por anuncio con todas las métricas del embudo.

    Columnas (en este orden):
      ad_id, ad_name, campaign_id, campaign_name, gasto, frequency,
      conversaciones, n_ventas, monto_ventas, utilidad, costo_por_conversacion,
      cpa, tasa_conversion, roas
    Ordenado por monto_ventas descendente, con una fila final extra
    `"Sin anuncio (no atribuido)"` cuando hay contactos con `primer_ad_id` nulo
    (para que las conversaciones de la tabla reconcilien con el total).

    Fuentes (CADA columna sabe de dónde viene):
    - gasto, frequency, campaign_*  → Facebook
    - n_ventas, monto_ventas        → Supabase `compradores`
    - conversaciones                → Supabase `contactos`  (NO Facebook)

    `utilidad` = monto_ventas - gasto (en COP, puede ser negativa).

    Casos borde (evitamos divisiones por cero):
    - Sin conversaciones: costo_por_conversacion = NaN, tasa_conversion = NaN.
    - Sin ventas: cpa = NaN.
    - Sin gasto: roas = NaN.
    - Ventas con ad_id que no está en Facebook: ad_name = "(sin datos en
      Facebook)", campaign vacía → caerá en "Sin campaña" al agregar.
    """
    spend_df = get_ad_spend(since, until).copy()
    sales_df = get_sales(since, until)
    contactos_df = get_contactos(since, until)

    # Normalizamos ad_id a string en spend_df, renombramos `spend` y nos
    # quedamos solo con las columnas que aporta Facebook (descartamos su
    # estimación de conversaciones; las verdaderas vienen de `contactos`).
    spend_df["ad_id"] = spend_df["ad_id"].astype(str)
    spend_df = spend_df.rename(columns={"spend": "gasto"})
    spend_df = spend_df[
        [
            "ad_id",
            "ad_name",
            "account_id",
            "account_name",
            "campaign_id",
            "campaign_name",
            "gasto",
            "frequency",
            "impressions",
            "reach",
            "clicks",
        ]
    ]

    # Ventas agrupadas por ad_id (ignorando filas sin ad_id).
    if sales_df.empty:
        sales_agg = pd.DataFrame(columns=["ad_id", "n_ventas", "monto_ventas"])
    else:
        sales_with_ad = sales_df.dropna(subset=["ad_id"]).copy()
        sales_with_ad["ad_id"] = sales_with_ad["ad_id"].astype(str)
        sales_agg = sales_with_ad.groupby("ad_id", as_index=False).agg(
            n_ventas=("valor", "size"),
            monto_ventas=("valor", "sum"),
        )

    # Conversaciones (solo las atribuidas a un ad_id).
    contactos_agg, n_sin_atribuir = _conversaciones_por_ad(contactos_df)

    # Outer merge de las tres fuentes por ad_id.
    merged = (
        spend_df.merge(sales_agg, on="ad_id", how="outer")
        .merge(contactos_agg, on="ad_id", how="outer")
    )

    merged["gasto"] = merged["gasto"].fillna(0.0)
    merged["frequency"] = merged["frequency"].fillna(0.0)
    merged["impressions"] = merged["impressions"].fillna(0).astype(int)
    merged["reach"] = merged["reach"].fillna(0).astype(int)
    merged["clicks"] = merged["clicks"].fillna(0).astype(int)
    merged["conversaciones"] = merged["conversaciones"].fillna(0).astype(int)
    merged["n_ventas"] = merged["n_ventas"].fillna(0).astype(int)
    merged["monto_ventas"] = merged["monto_ventas"].fillna(0.0)
    merged["ad_name"] = merged["ad_name"].fillna(SIN_NOMBRE)
    # No tocamos campaign_id/campaign_name; el NaN lo trataremos en
    # `get_campaign_performance` para mandarlo a "Sin campaña".

    merged = merged.sort_values("monto_ventas", ascending=False, ignore_index=True)

    # Fila extra para los contactos sin `primer_ad_id`. Va al final, fuera del
    # ordenamiento normal, para que totales reconcilien:
    #   total_conversaciones_dashboard = atribuidas + sin_atribuir
    if n_sin_atribuir > 0:
        merged = pd.concat(
            [
                merged,
                pd.DataFrame(
                    [
                        {
                            "ad_id": NO_AD_SENTINEL,
                            "ad_name": NO_AD_LABEL,
                            "account_id": None,
                            "account_name": None,
                            "campaign_id": None,
                            "campaign_name": None,
                            "gasto": 0.0,
                            "frequency": 0.0,
                            "impressions": 0,
                            "reach": 0,
                            "clicks": 0,
                            "conversaciones": n_sin_atribuir,
                            "n_ventas": 0,
                            "monto_ventas": 0.0,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )

    # Utilidad = ingresos - gasto (puede ser negativa).
    merged["utilidad"] = merged["monto_ventas"] - merged["gasto"]

    # Métricas derivadas — todas evitan dividir por cero.
    merged["roas"] = np.where(
        merged["gasto"] > 0,
        merged["monto_ventas"] / merged["gasto"].replace(0, np.nan),
        np.nan,
    )
    merged["cpa"] = np.where(
        merged["n_ventas"] > 0,
        merged["gasto"] / merged["n_ventas"].replace(0, np.nan),
        np.nan,
    )
    merged["costo_por_conversacion"] = np.where(
        merged["conversaciones"] > 0,
        merged["gasto"] / merged["conversaciones"].replace(0, np.nan),
        np.nan,
    )
    merged["tasa_conversion"] = np.where(
        merged["conversaciones"] > 0,
        100.0 * merged["n_ventas"] / merged["conversaciones"].replace(0, np.nan),
        np.nan,
    )

    return merged[
        [
            "ad_id",
            "ad_name",
            "account_id",
            "account_name",
            "campaign_id",
            "campaign_name",
            "gasto",
            "frequency",
            "impressions",
            "reach",
            "clicks",
            "conversaciones",
            "n_ventas",
            "monto_ventas",
            "utilidad",
            "costo_por_conversacion",
            "cpa",
            "tasa_conversion",
            "roas",
        ]
    ]


def _aggregate_by(ad_df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """Helper: agrega `ad_df` sumando las columnas de embudo y recalcula
    utilidad, ROAS, CPA, costo/conv y tasa de conversión sin dividir por cero.
    """
    grouped = ad_df.groupby(group_cols, as_index=False).agg(
        n_anuncios=("ad_id", "count"),
        gasto=("gasto", "sum"),
        conversaciones=("conversaciones", "sum"),
        n_ventas=("n_ventas", "sum"),
        monto_ventas=("monto_ventas", "sum"),
    )
    grouped["utilidad"] = grouped["monto_ventas"] - grouped["gasto"]
    grouped["roas"] = np.where(
        grouped["gasto"] > 0,
        grouped["monto_ventas"] / grouped["gasto"].replace(0, np.nan),
        np.nan,
    )
    grouped["cpa"] = np.where(
        grouped["n_ventas"] > 0,
        grouped["gasto"] / grouped["n_ventas"].replace(0, np.nan),
        np.nan,
    )
    grouped["costo_por_conversacion"] = np.where(
        grouped["conversaciones"] > 0,
        grouped["gasto"] / grouped["conversaciones"].replace(0, np.nan),
        np.nan,
    )
    grouped["tasa_conversion"] = np.where(
        grouped["conversaciones"] > 0,
        100.0 * grouped["n_ventas"] / grouped["conversaciones"].replace(0, np.nan),
        np.nan,
    )
    return grouped.sort_values(
        "monto_ventas", ascending=False, ignore_index=True
    )


def get_campaign_performance(since: str, until: str) -> pd.DataFrame:
    """Vista agregada por CAMPAÑA, derivada de `get_ad_performance`.

    Agrupa los anuncios por campaign_id y suma gasto, conversaciones, n_ventas
    y monto_ventas. Calcula utilidad, ROAS, CPA, costo/conversación y tasa de
    conversión a nivel campaña. Las filas sin campaña (anuncios eliminados de
    Facebook, ventas/contactos no atribuidos, fila "Sin anuncio") se agrupan
    bajo "Sin campaña".

    Columnas devueltas (en orden):
      campaign_id, campaign_name, n_anuncios, gasto, conversaciones, n_ventas,
      monto_ventas, utilidad, costo_por_conversacion, cpa, tasa_conversion, roas
    Ordenado por monto_ventas descendente.
    """
    ad_df = get_ad_performance(since, until).copy()
    ad_df["campaign_id"] = ad_df["campaign_id"].fillna(NO_CAMPAIGN_SENTINEL)
    ad_df["campaign_name"] = ad_df["campaign_name"].fillna(NO_CAMPAIGN_LABEL)

    grouped = _aggregate_by(ad_df, ["campaign_id", "campaign_name"])
    return grouped[
        [
            "campaign_id",
            "campaign_name",
            "n_anuncios",
            "gasto",
            "conversaciones",
            "n_ventas",
            "monto_ventas",
            "utilidad",
            "costo_por_conversacion",
            "cpa",
            "tasa_conversion",
            "roas",
        ]
    ]


def get_account_performance(since: str, until: str) -> pd.DataFrame:
    """Vista agregada por CUENTA PUBLICITARIA, derivada de `get_ad_performance`.

    Agrupa los anuncios por account_id y suma gasto, conversaciones, n_ventas y
    monto_ventas. Calcula utilidad, ROAS, CPA, costo/conversación y tasa de
    conversión a nivel cuenta. Las filas sin cuenta (fila "Sin anuncio" y
    cualquier venta/contacto cuyo ad_id no aparezca en Facebook) se agrupan
    bajo "Sin cuenta".

    Columnas devueltas (en orden):
      account_id, account_name, n_anuncios, gasto, conversaciones, n_ventas,
      monto_ventas, utilidad, costo_por_conversacion, cpa, tasa_conversion, roas
    Ordenado por monto_ventas descendente.
    """
    ad_df = get_ad_performance(since, until).copy()
    ad_df["account_id"] = ad_df["account_id"].fillna(NO_ACCOUNT_SENTINEL)
    ad_df["account_name"] = ad_df["account_name"].fillna(NO_ACCOUNT_LABEL)

    grouped = _aggregate_by(ad_df, ["account_id", "account_name"])
    return grouped[
        [
            "account_id",
            "account_name",
            "n_anuncios",
            "gasto",
            "conversaciones",
            "n_ventas",
            "monto_ventas",
            "utilidad",
            "costo_por_conversacion",
            "cpa",
            "tasa_conversion",
            "roas",
        ]
    ]


def get_daily_totals(since: str, until: str) -> pd.DataFrame:
    """Totales diarios agrupados por día de Bogotá.

    Devuelve un DataFrame con una fila por día del rango y columnas:
    `date`, `gasto`, `monto_ventas`, `n_ventas`, `conversaciones`.

    - `gasto` viene de Facebook (en COP).
    - `monto_ventas` y `n_ventas` vienen de `compradores`.
    - `conversaciones` viene de `contactos` y cuenta **TODOS los contactos**
      del día (incluidos los que tienen `primer_ad_id` nulo), agrupados por
      `primer_contacto_at` convertido a día de Bogotá.

    Incluye los días sin actividad (con ceros) para que la gráfica no salte
    huecos. NO se usa `tz_localize(None)`; UTC se convierte explícitamente
    a Bogotá antes de tomar el día.
    """
    spend = get_daily_spend(since, until).rename(columns={"spend": "gasto"})
    sales_df = get_sales(since, until)
    contactos_df = get_contactos(since, until)

    # Ventas por día de Bogotá (monto + conteo).
    if not sales_df.empty:
        sales_df = sales_df.copy()
        sales_df["date"] = (
            pd.to_datetime(sales_df["fecha_compra"], utc=True)
            .dt.tz_convert(BOGOTA)
            .dt.date
        )
        sales = sales_df.groupby("date", as_index=False).agg(
            monto_ventas=("valor", "sum"),
            n_ventas=("valor", "size"),
        )
    else:
        sales = pd.DataFrame(columns=["date", "monto_ventas", "n_ventas"])

    # Conversaciones por día de Bogotá → TODOS los contactos (con y sin ad_id).
    if not contactos_df.empty:
        contactos_df = contactos_df.copy()
        contactos_df["date"] = (
            pd.to_datetime(contactos_df["primer_contacto_at"], utc=True)
            .dt.tz_convert(BOGOTA)
            .dt.date
        )
        contactos_daily = (
            contactos_df.groupby("date", as_index=False)
            .size()
            .rename(columns={"size": "conversaciones"})
        )
    else:
        contactos_daily = pd.DataFrame(columns=["date", "conversaciones"])

    if not spend.empty:
        # Facebook ya entrega `date_start` como "YYYY-MM-DD" (día sin hora).
        spend["date"] = pd.to_datetime(spend["date"]).dt.date

    # Esqueleto con todos los días del rango como objetos `date`.
    all_days = pd.DataFrame(
        {"date": [d.date() for d in pd.date_range(since, until, freq="D")]}
    )

    merged = (
        all_days.merge(spend, on="date", how="left")
        .merge(sales, on="date", how="left")
        .merge(contactos_daily, on="date", how="left")
    )
    merged["gasto"] = merged["gasto"].fillna(0.0)
    merged["monto_ventas"] = merged["monto_ventas"].fillna(0.0)
    merged["n_ventas"] = merged["n_ventas"].fillna(0).astype(int)
    merged["conversaciones"] = merged["conversaciones"].fillna(0).astype(int)
    return merged.sort_values("date", ignore_index=True)


if __name__ == "__main__":
    today = date.today()
    since = (today - timedelta(days=7)).isoformat()
    until = today.isoformat()

    pd.options.display.float_format = "{:,.2f}".format
    pd.options.display.max_colwidth = 60
    pd.options.display.width = 200

    print(f"Rendimiento por anuncio del {since} al {until}\n")
    df = get_ad_performance(since, until)

    n_total = len(df)
    n_con_ventas = int((df["n_ventas"] > 0).sum())
    n_con_gasto_sin_ventas = int(((df["gasto"] > 0) & (df["n_ventas"] == 0)).sum())
    n_con_ventas_sin_gasto = int(((df["n_ventas"] > 0) & (df["gasto"] == 0)).sum())

    print(f"Anuncios totales:              {n_total}")
    print(f"  Con ventas:                  {n_con_ventas}")
    print(f"  Con gasto pero sin ventas:   {n_con_gasto_sin_ventas}")
    print(f"  Con ventas sin gasto en FB:  {n_con_ventas_sin_gasto}")

    gasto_total = df["gasto"].sum()
    ventas_total = df["monto_ventas"].sum()
    n_ventas_total = int(df["n_ventas"].sum())
    conv_total = int(df["conversaciones"].sum())
    conv_atribuidas = int(
        df.loc[df["ad_id"] != NO_AD_SENTINEL, "conversaciones"].sum()
    )
    conv_sin_atribuir = int(
        df.loc[df["ad_id"] == NO_AD_SENTINEL, "conversaciones"].sum()
    )

    print(f"\nGasto total:        {gasto_total:,.2f} COP")
    print(f"Monto ventas total: {ventas_total:,.2f} COP")
    print(f"Conversaciones:     {conv_total}  "
          f"(atribuidas: {conv_atribuidas}  ·  sin atribuir: {conv_sin_atribuir})")
    print(f"Ventas totales:     {n_ventas_total}")

    if gasto_total > 0:
        print(f"ROAS global:        {ventas_total / gasto_total:.2f}")
    if n_ventas_total > 0:
        print(f"CPA global:         {gasto_total / n_ventas_total:,.2f} COP")
    if conv_total > 0:
        print(f"Costo/conversación: {gasto_total / conv_total:,.2f} COP")
        print(f"Tasa conv global:   {100 * n_ventas_total / conv_total:.1f}%")

    print("\n" + "=" * 80)
    print("Top anuncios por monto de ventas:")
    print("=" * 80)
    print(df.head(20).to_string(index=False))

    print("\n" + "=" * 80)
    _cfg = get_config()
    print(
        f"Top campañas (umbrales de config.json: "
        f"CPA_BUENO={_cfg['cpa_bueno']:.0f}, CPA_MAXIMO={_cfg['cpa_maximo']:.0f}, "
        f"ROAS_MINIMO={_cfg['roas_minimo']:.2f}, ROAS_BUENO={_cfg['roas_bueno']:.2f}):"
    )
    print("=" * 80)
    camp = get_campaign_performance(since, until)
    print(camp.to_string(index=False))
