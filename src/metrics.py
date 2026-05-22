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

from facebook_client import get_ad_spend, get_daily_spend  # noqa: E402
from supabase_client import get_contactos, get_sales  # noqa: E402

SIN_NOMBRE = "(sin datos en Facebook)"
NO_AD_LABEL = "Sin anuncio (no atribuido)"
# Sentinela usada como ad_id para la fila de contactos sin `primer_ad_id`.
# Empieza por "_" para no chocar con ningún ad_id real de Facebook.
NO_AD_SENTINEL = "_sin_anuncio_"

# Cualquier agrupación o cruce por fecha se hace en día calendario de Bogotá.
BOGOTA = "America/Bogota"


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
      ad_id, ad_name, gasto, frequency, conversaciones, n_ventas, monto_ventas,
      costo_por_conversacion, cpa, tasa_conversion, roas
    Ordenado por monto_ventas descendente, con una fila final extra
    `"Sin anuncio (no atribuido)"` cuando hay contactos con `primer_ad_id` nulo
    (para que las conversaciones de la tabla reconcilien con el total).

    Fuentes (CADA columna sabe de dónde viene):
    - gasto, frequency       → Facebook
    - n_ventas, monto_ventas → Supabase `compradores`
    - conversaciones         → Supabase `contactos`  (NO Facebook)

    Casos borde (evitamos divisiones por cero):
    - Sin conversaciones: costo_por_conversacion = NaN, tasa_conversion = NaN.
    - Sin ventas: cpa = NaN.
    - Sin gasto: roas = NaN.
    - Ventas con ad_id que no está en Facebook: ad_name = "(sin datos en Facebook)".
    """
    spend_df = get_ad_spend(since, until).copy()
    sales_df = get_sales(since, until)
    contactos_df = get_contactos(since, until)

    # Normalizamos ad_id a string en spend_df, renombramos `spend` y nos
    # quedamos solo con las columnas que aporta Facebook (descartamos su
    # estimación de conversaciones; las verdaderas vienen de `contactos`).
    spend_df["ad_id"] = spend_df["ad_id"].astype(str)
    spend_df = spend_df.rename(columns={"spend": "gasto"})
    spend_df = spend_df[["ad_id", "ad_name", "gasto", "frequency"]]

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
    merged["conversaciones"] = merged["conversaciones"].fillna(0).astype(int)
    merged["n_ventas"] = merged["n_ventas"].fillna(0).astype(int)
    merged["monto_ventas"] = merged["monto_ventas"].fillna(0.0)
    merged["ad_name"] = merged["ad_name"].fillna(SIN_NOMBRE)

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
                            "gasto": 0.0,
                            "frequency": 0.0,
                            "conversaciones": n_sin_atribuir,
                            "n_ventas": 0,
                            "monto_ventas": 0.0,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )

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
            "gasto",
            "frequency",
            "conversaciones",
            "n_ventas",
            "monto_ventas",
            "costo_por_conversacion",
            "cpa",
            "tasa_conversion",
            "roas",
        ]
    ]


def get_daily_totals(since: str, until: str) -> pd.DataFrame:
    """Totales diarios agrupados por día de Bogotá.

    Devuelve un DataFrame con una fila por día del rango y columnas:
    `date`, `gasto`, `monto_ventas`, `conversaciones`.

    - `gasto` viene de Facebook (en COP).
    - `monto_ventas` viene de `compradores`.
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

    # Ventas por día de Bogotá.
    if not sales_df.empty:
        sales_df = sales_df.copy()
        sales_df["date"] = (
            pd.to_datetime(sales_df["fecha_compra"], utc=True)
            .dt.tz_convert(BOGOTA)
            .dt.date
        )
        sales = (
            sales_df.groupby("date", as_index=False)["valor"]
            .sum()
            .rename(columns={"valor": "monto_ventas"})
        )
    else:
        sales = pd.DataFrame(columns=["date", "monto_ventas"])

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
