"""Cliente de solo lectura para Facebook Marketing API.

Devuelve, por rango de fechas, el gasto y el nombre de cada anuncio (nivel `ad`).
Soporta varias cuentas publicitarias en `FB_AD_ACCOUNT_ID` separadas por coma. El gasto se
devuelve siempre en pesos colombianos (COP): las cuentas que estén en otra moneda se
convierten usando la tasa `USD_TO_COP` del `.env`.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pandas as pd
from dotenv import load_dotenv
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.api import FacebookAdsApi

load_dotenv()

FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")
FB_AD_ACCOUNT_ID = os.getenv("FB_AD_ACCOUNT_ID", "")
FB_APP_ID = os.getenv("FB_APP_ID")
FB_APP_SECRET = os.getenv("FB_APP_SECRET")

# Tasa de conversión por defecto si no se define en .env. Solo se aplica a cuentas
# cuya moneda NO sea COP (típicamente USD).
USD_TO_COP = float(os.getenv("USD_TO_COP") or "4000")

TARGET_CURRENCY = "COP"


def _account_ids() -> list[str]:
    """Lista de cuentas publicitarias configuradas en FB_AD_ACCOUNT_ID."""
    return [acc.strip() for acc in FB_AD_ACCOUNT_ID.split(",") if acc.strip()]


def _spend_for_account(account_id: str, since: str, until: str) -> pd.DataFrame:
    """Insights de un anuncio para una cuenta, con gasto convertido a COP.

    Devuelve columnas: ad_id, ad_name, spend, account_id, account_currency.
    """
    account = AdAccount(account_id)
    account_data = account.api_get(fields=["currency"])
    currency = account_data["currency"]

    insights = account.get_insights(
        fields=["ad_id", "ad_name", "spend"],
        params={
            "level": "ad",
            "time_range": {"since": since, "until": until},
            "limit": 500,
        },
    )

    multiplier = 1.0 if currency == TARGET_CURRENCY else USD_TO_COP

    rows = [
        {
            "ad_id": str(item.get("ad_id")),
            "ad_name": item.get("ad_name"),
            "spend": float(item.get("spend", 0) or 0) * multiplier,
            "account_id": account_id,
            "account_currency": currency,
        }
        for item in insights
    ]
    return pd.DataFrame(
        rows,
        columns=["ad_id", "ad_name", "spend", "account_id", "account_currency"],
    )


def _try_spend_for_account(
    account_id: str, since: str, until: str
) -> tuple[pd.DataFrame | None, str | None]:
    """Variante tolerante a fallos: si la cuenta falla, devuelve (None, mensaje_error)."""
    try:
        return _spend_for_account(account_id, since, until), None
    except Exception as exc:  # noqa: BLE001 — queremos atrapar cualquier fallo de API
        return None, str(exc)


def get_ad_spend(since: str, until: str) -> pd.DataFrame:
    """Devuelve un DataFrame combinado con: ad_id, ad_name, spend (en COP).

    Recorre todas las cuentas listadas en `FB_AD_ACCOUNT_ID` (separadas por coma) y
    devuelve el gasto unificado en COP. Si una cuenta falla, se omite y se imprime
    un aviso por stderr; las demás siguen funcionando. `since` y `until` en YYYY-MM-DD.
    """
    FacebookAdsApi.init(FB_APP_ID, FB_APP_SECRET, FB_ACCESS_TOKEN)

    parts = []
    for acc in _account_ids():
        df_acc, err = _try_spend_for_account(acc, since, until)
        if df_acc is not None:
            parts.append(df_acc)
        else:
            print(f"[WARN] cuenta {acc} omitida: {err}", file=sys.stderr)

    if not parts:
        return pd.DataFrame(columns=["ad_id", "ad_name", "spend"])

    df = pd.concat(parts, ignore_index=True)
    return df[["ad_id", "ad_name", "spend"]]


if __name__ == "__main__":
    today = date.today()
    since = (today - timedelta(days=7)).isoformat()
    until = today.isoformat()

    accounts = _account_ids()
    print(f"Insights de Facebook Ads del {since} al {until}")
    print(f"Cuentas configuradas: {len(accounts)}")
    print(f"Tasa USD→COP: {USD_TO_COP}\n")

    FacebookAdsApi.init(FB_APP_ID, FB_APP_SECRET, FB_ACCESS_TOKEN)

    successes: list[tuple[str, pd.DataFrame]] = []
    failures: list[tuple[str, str]] = []

    for acc in accounts:
        df_acc, err = _try_spend_for_account(acc, since, until)
        if df_acc is not None:
            cur = df_acc["account_currency"].iloc[0] if not df_acc.empty else "?"
            total = df_acc["spend"].sum()
            print(f"  ✓ {acc} ({cur}): {len(df_acc)} anuncios, gasto {total:,.2f} COP")
            successes.append((acc, df_acc))
        else:
            print(f"  ✗ {acc}: ERROR")
            failures.append((acc, err or "error desconocido"))

    print("\n" + "=" * 60)
    print(f"Resumen: {len(successes)} OK, {len(failures)} con error (de {len(accounts)} cuentas)")
    print("=" * 60)

    if failures:
        print("\nCuentas con error:")
        for acc, err in failures:
            print(f"\n  ✗ {acc}")
            print(f"    {err}")

    if successes:
        df = pd.concat([d for _, d in successes], ignore_index=True)[
            ["ad_id", "ad_name", "spend"]
        ]
        print(
            f"\nTotal combinado (solo cuentas OK): {len(df)} anuncios, "
            f"gasto {df['spend'].sum():,.2f} COP\n"
        )
        print(df.to_string(index=False))
    elif not accounts:
        print("\nNo hay cuentas configuradas en FB_AD_ACCOUNT_ID.")
    else:
        print("\nNinguna cuenta devolvió datos.")
