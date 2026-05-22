"""Cliente de solo lectura para la tabla de ventas en Supabase."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SALES_TABLE = os.getenv("SALES_TABLE", "compradores")

PAGE_SIZE = 1000


def get_sales(since: str, until: str) -> pd.DataFrame:
    """Devuelve un DataFrame con las ventas en el rango dado.

    Columnas: ad_id, valor, fecha_compra.
    `since` y `until` son fechas en formato YYYY-MM-DD (ambas inclusivas).
    """
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Límite superior exclusivo: día siguiente a `until`. Así funciona igual de bien
    # si `fecha_compra` es `date` o `timestamptz`, sin recortar el último día.
    upper_exclusive = (date.fromisoformat(until) + timedelta(days=1)).isoformat()

    rows: list[dict] = []
    offset = 0
    while True:
        result = (
            client.table(SALES_TABLE)
            .select("ad_id, valor, fecha_compra")
            .gte("fecha_compra", since)
            .lt("fecha_compra", upper_exclusive)
            .order("fecha_compra")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    df = pd.DataFrame(rows, columns=["ad_id", "valor", "fecha_compra"])
    if not df.empty:
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        df["fecha_compra"] = pd.to_datetime(df["fecha_compra"])
    return df


if __name__ == "__main__":
    today = date.today()
    since = (today - timedelta(days=7)).isoformat()
    until = today.isoformat()

    print(f"Ventas en Supabase del {since} al {until}\n")
    df = get_sales(since, until)
    print(f"Filas devueltas: {len(df)}")
    if not df.empty:
        print(f"Monto total: {df['valor'].sum():.2f}")
        print(f"Ventas con ad_id no nulo: {df['ad_id'].notna().sum()}\n")
        print(df.head(20).to_string(index=False))
