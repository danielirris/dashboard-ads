"""Cliente de solo lectura para Supabase.

Dos tablas:
- `compradores` (ventas): `get_sales`
- `contactos`   (conversaciones reales de WhatsApp): `get_contactos`

Ambas filtran por **día calendario de Bogotá**: convertimos los límites del
rango a UTC antes de mandarlos a Supabase para que las filas de la noche no se
cuelen al día siguiente.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

from currency import CURRENCY_RATES, currency_multiplier  # noqa: E402

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SALES_TABLE = os.getenv("SALES_TABLE", "compradores")
CONTACTS_TABLE = os.getenv("CONTACTS_TABLE", "contactos")

PAGE_SIZE = 1000

# Las columnas timestamptz están guardadas en UTC, pero queremos filtrar por
# día calendario de Colombia.
BOGOTA = ZoneInfo("America/Bogota")


def _bogota_day_range_utc(since: str, until: str) -> tuple[datetime, datetime]:
    """Convierte un rango de días de Bogotá a [start_utc, end_utc_exclusive).

    `since` y `until` son fechas YYYY-MM-DD interpretadas como días de Bogotá
    (ambas inclusivas). Devuelve dos datetimes UTC tz-aware listos para mandar
    al filtro de Supabase contra una columna timestamptz.
    """
    start_utc = datetime.combine(
        date.fromisoformat(since), time.min, tzinfo=BOGOTA
    ).astimezone(timezone.utc)
    end_utc_exclusive = datetime.combine(
        date.fromisoformat(until) + timedelta(days=1), time.min, tzinfo=BOGOTA
    ).astimezone(timezone.utc)
    return start_utc, end_utc_exclusive


def get_sales(since: str, until: str) -> pd.DataFrame:
    """Devuelve un DataFrame con las ventas dentro del rango de días de Bogotá.

    Columnas: ad_id, valor_local, moneda, valor_cop, fecha_compra (tz-aware UTC).

    `valor_local` es el monto original en la moneda del país. `moneda` viene de
    la columna `moneda` de Supabase (si es NULL se asume COP). `valor_cop` es
    el monto convertido a pesos colombianos usando las tasas `*_TO_COP` del .env.
    Si la moneda no tiene tasa configurada, `valor_cop` = NaN.
    """
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    start_utc, end_utc_exclusive = _bogota_day_range_utc(since, until)

    rows: list[dict] = []
    offset = 0
    while True:
        result = (
            client.table(SALES_TABLE)
            .select("ad_id, valor, moneda, pais, fecha_compra")
            .gte("fecha_compra", start_utc.isoformat())
            .lt("fecha_compra", end_utc_exclusive.isoformat())
            .order("fecha_compra")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    df = pd.DataFrame(rows, columns=["ad_id", "valor", "moneda", "pais", "fecha_compra"])
    if not df.empty:
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        df["fecha_compra"] = pd.to_datetime(df["fecha_compra"], utc=True)

        # Moneda NULL → asumir COP.
        monedas_null = df["moneda"].isna()
        if monedas_null.any():
            print(
                f"[WARN] {int(monedas_null.sum())} venta(s) con moneda NULL — "
                f"asumiendo COP.",
                file=sys.stderr,
            )
            df["moneda"] = df["moneda"].fillna("COP")
        df["moneda"] = df["moneda"].str.upper()

        df = df.rename(columns={"valor": "valor_local"})

        def _to_cop(row):
            m = row["moneda"]
            if m == "COP":
                return row["valor_local"]
            rate = CURRENCY_RATES.get(m)
            if rate is None:
                print(
                    f"[WARN] venta sin tasa para moneda {m}",
                    file=sys.stderr,
                )
                return float("nan")
            return row["valor_local"] * rate

        df["valor_cop"] = df.apply(_to_cop, axis=1)
    else:
        df = df.rename(columns={"valor": "valor_local"})
        df["valor_cop"] = pd.Series(dtype=float)
    if "pais" not in df.columns:
        df["pais"] = pd.Series(dtype=str)
    return df[["ad_id", "valor_local", "moneda", "pais", "valor_cop", "fecha_compra"]]


def get_contactos(since: str, until: str) -> pd.DataFrame:
    """Devuelve los contactos (conversaciones reales) en el rango de días Bogotá.

    Columnas: primer_ad_id (puede ser None), primer_contacto_at (tz-aware UTC),
    pais (código ISO-2 crudo, puede ser None).

    Esta es la fuente "real" de conversaciones — más confiable que la métrica de
    Facebook. `primer_ad_id` viene a veces en `null` (contacto no atribuido a un
    anuncio); esas filas se devuelven igualmente, los consumidores deciden cómo
    tratarlas. `pais` permite filtrar las conversaciones por país sin depender
    del cruce por ad_id (los consumidores mapean el código a nombre largo).
    """
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    start_utc, end_utc_exclusive = _bogota_day_range_utc(since, until)

    rows: list[dict] = []
    offset = 0
    while True:
        result = (
            client.table(CONTACTS_TABLE)
            .select("primer_ad_id, primer_contacto_at, pais")
            .gte("primer_contacto_at", start_utc.isoformat())
            .lt("primer_contacto_at", end_utc_exclusive.isoformat())
            .order("primer_contacto_at")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    df = pd.DataFrame(
        rows, columns=["primer_ad_id", "primer_contacto_at", "pais"]
    )
    if not df.empty:
        df["primer_contacto_at"] = pd.to_datetime(df["primer_contacto_at"], utc=True)
    return df


if __name__ == "__main__":
    today = datetime.now(BOGOTA).date()
    since = (today - timedelta(days=7)).isoformat()
    until = today.isoformat()

    print(f"Datos de Supabase del {since} al {until} (días de Bogotá)\n")

    print("── Ventas ─────────────────────────────────────────────────────")
    ventas = get_sales(since, until)
    print(f"Filas devueltas: {len(ventas)}")
    if not ventas.empty:
        print(f"Monto total (local): {ventas['valor_local'].sum():,.2f}")
        print(f"Monto total (COP):   {ventas['valor_cop'].sum():,.2f}")
        print(f"Monedas presentes:   {ventas['moneda'].value_counts().to_dict()}")
        print(f"Ventas con ad_id no nulo: {ventas['ad_id'].notna().sum()}\n")
        print(ventas.head(10).to_string(index=False))

    print("\n── Contactos (conversaciones reales) ──────────────────────────")
    contactos = get_contactos(since, until)
    print(f"Filas devueltas: {len(contactos)}")
    if not contactos.empty:
        n_atribuidos = int(contactos["primer_ad_id"].notna().sum())
        n_sin_atribuir = int(contactos["primer_ad_id"].isna().sum())
        print(f"  Atribuidos (con primer_ad_id):    {n_atribuidos}")
        print(f"  Sin atribuir (primer_ad_id null): {n_sin_atribuir}")
        print(f"  Total:                            {n_atribuidos + n_sin_atribuir}\n")
        print(contactos.head(10).to_string(index=False))
