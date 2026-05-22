"""Cruce de Facebook Ads + Supabase por `ad_id`. El corazón del proyecto.

Une el gasto por anuncio (Facebook, ya en COP) con las ventas (Supabase) y calcula,
por anuncio, número de ventas, monto, ROAS (ventas / gasto) y CPA (gasto / ventas).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Permite ejecutar `python src/metrics.py` desde la raíz del proyecto.
sys.path.insert(0, str(Path(__file__).parent))

from facebook_client import get_ad_spend  # noqa: E402
from supabase_client import get_sales  # noqa: E402

SIN_NOMBRE = "(sin datos en Facebook)"


def get_ad_performance(since: str, until: str) -> pd.DataFrame:
    """Devuelve un DataFrame por anuncio con gasto, ventas, ROAS y CPA.

    Columnas: ad_id, ad_name, gasto, n_ventas, monto_ventas, roas, cpa.
    Ordenado por monto_ventas descendente.

    Casos borde:
    - Anuncios con gasto pero sin ventas: n_ventas = 0, monto_ventas = 0, roas = 0,
      cpa = NaN (no se divide entre cero).
    - Ventas cuyo ad_id no está en Facebook: gasto = 0, ad_name marcado como
      "(sin datos en Facebook)", roas = NaN, cpa = 0.
    """
    spend_df = get_ad_spend(since, until).copy()
    sales_df = get_sales(since, until)

    # Normalizamos ad_id a string en ambos lados antes del merge.
    spend_df["ad_id"] = spend_df["ad_id"].astype(str)
    spend_df = spend_df.rename(columns={"spend": "gasto"})

    # Agrupamos ventas por ad_id (ignorando filas sin ad_id).
    if sales_df.empty:
        sales_agg = pd.DataFrame(columns=["ad_id", "n_ventas", "monto_ventas"])
    else:
        sales_with_ad = sales_df.dropna(subset=["ad_id"]).copy()
        sales_with_ad["ad_id"] = sales_with_ad["ad_id"].astype(str)
        sales_agg = sales_with_ad.groupby("ad_id", as_index=False).agg(
            n_ventas=("valor", "size"),
            monto_ventas=("valor", "sum"),
        )

    # Outer merge → incluimos anuncios con gasto sin ventas y ventas sin gasto.
    merged = spend_df.merge(sales_agg, on="ad_id", how="outer")

    merged["gasto"] = merged["gasto"].fillna(0.0)
    merged["n_ventas"] = merged["n_ventas"].fillna(0).astype(int)
    merged["monto_ventas"] = merged["monto_ventas"].fillna(0.0)
    merged["ad_name"] = merged["ad_name"].fillna(SIN_NOMBRE)

    # ROAS y CPA evitando divisiones por cero.
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

    merged = merged.sort_values("monto_ventas", ascending=False, ignore_index=True)
    return merged[
        ["ad_id", "ad_name", "gasto", "n_ventas", "monto_ventas", "roas", "cpa"]
    ]


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

    print(f"\nGasto total:        {gasto_total:,.2f} COP")
    print(f"Monto ventas total: {ventas_total:,.2f} COP")
    print(f"Ventas totales:     {n_ventas_total}")

    if gasto_total > 0:
        print(f"ROAS global:        {ventas_total / gasto_total:.2f}")
    if n_ventas_total > 0:
        print(f"CPA global:         {gasto_total / n_ventas_total:,.2f} COP")

    print("\n" + "=" * 80)
    print("Top anuncios por monto de ventas:")
    print("=" * 80)
    print(df.head(20).to_string(index=False))
