"""Cliente de solo lectura para Facebook Marketing API.

Devuelve, por rango de fechas, métricas por anuncio (nivel `ad`):
- spend (gasto, convertido a COP)
- frequency (frecuencia)
- conversaciones (mensajes iniciados de WhatsApp, extraídos del campo `actions`)

Soporta varias cuentas publicitarias en `FB_AD_ACCOUNT_ID` separadas por coma.
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

# Action type que cuenta una conversación iniciada en WhatsApp/Messenger (CTWA).
# Si tu cuenta usa otro tipo (lo verás en la inspección del bloque __main__),
# basta con cambiar este string.
MESSAGING_CONVO_ACTION = "onsite_conversion.messaging_conversation_started_7d"

# Campos que pedimos a la API de insights a nivel anuncio.
AD_INSIGHT_FIELDS = ["ad_id", "ad_name", "spend", "frequency", "actions"]


def _account_ids() -> list[str]:
    """Lista de cuentas publicitarias configuradas en FB_AD_ACCOUNT_ID."""
    return [acc.strip() for acc in FB_AD_ACCOUNT_ID.split(",") if acc.strip()]


def _extract_action_value(actions, action_type: str) -> float:
    """Suma los valores de un `action_type` concreto dentro del campo `actions`.

    `actions` es la lista de dicts {action_type, value, ...} que devuelve la API
    de insights. Devuelve 0.0 si no hay coincidencias.
    """
    if not actions:
        return 0.0
    return sum(
        float(a.get("value", 0) or 0)
        for a in actions
        if a.get("action_type") == action_type
    )


def _spend_for_account(account_id: str, since: str, until: str) -> pd.DataFrame:
    """Insights por anuncio para una cuenta, con gasto convertido a COP.

    Devuelve columnas: ad_id, ad_name, spend, frequency, conversaciones,
    account_id, account_currency.
    """
    account = AdAccount(account_id)
    account_data = account.api_get(fields=["currency"])
    currency = account_data["currency"]

    insights = account.get_insights(
        fields=AD_INSIGHT_FIELDS,
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
            "frequency": float(item.get("frequency", 0) or 0),
            "conversaciones": _extract_action_value(
                item.get("actions"), MESSAGING_CONVO_ACTION
            ),
            "account_id": account_id,
            "account_currency": currency,
        }
        for item in insights
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "ad_id",
            "ad_name",
            "spend",
            "frequency",
            "conversaciones",
            "account_id",
            "account_currency",
        ],
    )


def discover_action_types(since: str, until: str) -> dict[str, float]:
    """Devuelve un dict {action_type: suma_total_de_valores} con todos los
    action_types vistos en los insights del rango, sumados sobre todas las cuentas.

    Útil para identificar el nombre exacto del action que corresponde a
    'conversación iniciada en WhatsApp' (p. ej. messaging_conversation_started_7d
    vs total_messaging_connection vs onsite_conversion.messaging_first_reply…).
    """
    FacebookAdsApi.init(FB_APP_ID, FB_APP_SECRET, FB_ACCESS_TOKEN)
    counts: dict[str, float] = {}
    for acc in _account_ids():
        try:
            account = AdAccount(acc)
            insights = account.get_insights(
                fields=["actions"],
                params={
                    "level": "ad",
                    "time_range": {"since": since, "until": until},
                    "limit": 500,
                },
            )
            for item in insights:
                for a in (item.get("actions") or []):
                    atype = a.get("action_type")
                    if not atype:
                        continue
                    counts[atype] = counts.get(atype, 0.0) + float(
                        a.get("value", 0) or 0
                    )
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] {acc} omitida en inspección: {exc}", file=sys.stderr)
    return counts


def _try_spend_for_account(
    account_id: str, since: str, until: str
) -> tuple[pd.DataFrame | None, str | None]:
    """Variante tolerante a fallos: si la cuenta falla, devuelve (None, mensaje_error)."""
    try:
        return _spend_for_account(account_id, since, until), None
    except Exception as exc:  # noqa: BLE001 — queremos atrapar cualquier fallo de API
        return None, str(exc)


def _daily_spend_for_account(
    account_id: str, since: str, until: str
) -> pd.DataFrame:
    """Gasto diario de una cuenta (nivel cuenta, time_increment=1), convertido a COP.

    Devuelve columnas: date, spend.
    """
    account = AdAccount(account_id)
    account_data = account.api_get(fields=["currency"])
    currency = account_data["currency"]

    insights = account.get_insights(
        fields=["spend"],
        params={
            "level": "account",
            "time_increment": 1,
            "time_range": {"since": since, "until": until},
        },
    )

    multiplier = 1.0 if currency == TARGET_CURRENCY else USD_TO_COP

    rows = [
        {
            "date": item.get("date_start"),
            "spend": float(item.get("spend", 0) or 0) * multiplier,
        }
        for item in insights
    ]
    return pd.DataFrame(rows, columns=["date", "spend"])


def get_daily_spend(since: str, until: str) -> pd.DataFrame:
    """Gasto diario combinado de todas las cuentas, en COP.

    Devuelve columnas: date (datetime), spend (float). Las cuentas que fallen se omiten
    con un aviso por stderr.
    """
    FacebookAdsApi.init(FB_APP_ID, FB_APP_SECRET, FB_ACCESS_TOKEN)

    parts = []
    for acc in _account_ids():
        try:
            parts.append(_daily_spend_for_account(acc, since, until))
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] cuenta {acc} omitida (daily): {exc}", file=sys.stderr)

    if not parts:
        return pd.DataFrame(columns=["date", "spend"])

    df = pd.concat(parts, ignore_index=True)
    df = df.groupby("date", as_index=False)["spend"].sum()
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date", ignore_index=True)


def get_ad_spend(since: str, until: str) -> pd.DataFrame:
    """Devuelve un DataFrame combinado con métricas por anuncio.

    Columnas: ad_id, ad_name, spend (COP), frequency, conversaciones.
    Recorre todas las cuentas listadas en `FB_AD_ACCOUNT_ID`. Si una cuenta falla,
    se omite con un aviso por stderr; las demás siguen funcionando.
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
        return pd.DataFrame(
            columns=["ad_id", "ad_name", "spend", "frequency", "conversaciones"]
        )

    df = pd.concat(parts, ignore_index=True)
    return df[["ad_id", "ad_name", "spend", "frequency", "conversaciones"]]


if __name__ == "__main__":
    today = date.today()
    since = (today - timedelta(days=7)).isoformat()
    until = today.isoformat()

    accounts = _account_ids()
    print(f"Insights de Facebook Ads del {since} al {until}")
    print(f"Cuentas configuradas: {len(accounts)}")
    print(f"Tasa USD→COP: {USD_TO_COP}\n")

    # ── 1) Inspección de action_types ─────────────────────────────────────
    print("Inspección de action_types encontrados (top 20 por volumen):")
    discovered = discover_action_types(since, until)
    if discovered:
        for atype, total in sorted(discovered.items(), key=lambda x: -x[1])[:20]:
            marker = "  ← usado para 'conversaciones'" if atype == MESSAGING_CONVO_ACTION else ""
            print(f"  {atype}: {total:,.0f}{marker}")
        if MESSAGING_CONVO_ACTION not in discovered:
            print(
                f"\n  ⚠️  El action_type configurado ({MESSAGING_CONVO_ACTION}) NO apareció.\n"
                f"     Revisa la lista y ajusta MESSAGING_CONVO_ACTION en este archivo."
            )
    else:
        print("  (sin acciones en el rango)")

    print()

    # ── 2) Gasto + métricas por cuenta ────────────────────────────────────
    FacebookAdsApi.init(FB_APP_ID, FB_APP_SECRET, FB_ACCESS_TOKEN)

    successes: list[tuple[str, pd.DataFrame]] = []
    failures: list[tuple[str, str]] = []

    for acc in accounts:
        df_acc, err = _try_spend_for_account(acc, since, until)
        if df_acc is not None:
            cur = df_acc["account_currency"].iloc[0] if not df_acc.empty else "?"
            total_spend = df_acc["spend"].sum()
            total_conv = df_acc["conversaciones"].sum()
            print(
                f"  ✓ {acc} ({cur}): {len(df_acc)} anuncios, "
                f"gasto {total_spend:,.0f} COP, "
                f"conversaciones {total_conv:,.0f}"
            )
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
            ["ad_id", "ad_name", "spend", "frequency", "conversaciones"]
        ]
        print(
            f"\nTotal combinado (solo cuentas OK): {len(df)} anuncios, "
            f"gasto {df['spend'].sum():,.0f} COP, "
            f"conversaciones {df['conversaciones'].sum():,.0f}\n"
        )
        print(df.to_string(index=False))
    elif not accounts:
        print("\nNo hay cuentas configuradas en FB_AD_ACCOUNT_ID.")
    else:
        print("\nNinguna cuenta devolvió datos.")
