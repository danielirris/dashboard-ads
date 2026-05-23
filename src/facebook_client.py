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
from functools import lru_cache

import pandas as pd
from dotenv import load_dotenv
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.user import User
from facebook_business.api import FacebookAdsApi

load_dotenv()

FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")
# Filtro OPCIONAL. Si está vacío, descubrimos todas las cuentas que el token
# pueda ver vía /me/adaccounts. Si está definido, usamos solo esas.
FB_AD_ACCOUNT_ID = os.getenv("FB_AD_ACCOUNT_ID", "")
FB_APP_ID = os.getenv("FB_APP_ID")
FB_APP_SECRET = os.getenv("FB_APP_SECRET")

TARGET_CURRENCY = "COP"


def _load_currency_rates() -> dict[str, float]:
    """Lee del entorno todas las variables `<CCY>_TO_COP` (USD_TO_COP,
    MXN_TO_COP, EUR_TO_COP, …) y construye {moneda: tasa_a_COP}.
    """
    rates: dict[str, float] = {}
    for key, value in os.environ.items():
        if not key.endswith("_TO_COP"):
            continue
        ccy = key[: -len("_TO_COP")]
        if not ccy or not value.strip():
            continue
        try:
            rates[ccy.upper()] = float(value)
        except ValueError:
            print(
                f"[WARN] valor inválido para {key}: {value!r}", file=sys.stderr
            )
    return rates


CURRENCY_RATES = _load_currency_rates()


def _currency_multiplier(currency: str) -> float:
    """Devuelve cuánto hay que multiplicar para pasar `currency` a COP.

    Si la cuenta ya está en COP devuelve 1.0. Si no hay tasa configurada,
    imprime un aviso y devuelve 1.0 (el gasto se queda en su moneda original
    en vez de inventar una conversión). Define `<CCY>_TO_COP` en el `.env`.
    """
    if currency == TARGET_CURRENCY:
        return 1.0
    rate = CURRENCY_RATES.get(currency)
    if rate is None:
        print(
            f"[WARN] sin tasa configurada para {currency}; el gasto NO se "
            f"convertirá. Define {currency}_TO_COP en .env.",
            file=sys.stderr,
        )
        return 1.0
    return rate

# Action type que cuenta una conversación iniciada en WhatsApp/Messenger (CTWA).
# Si tu cuenta usa otro tipo (lo verás en la inspección del bloque __main__),
# basta con cambiar este string.
MESSAGING_CONVO_ACTION = "onsite_conversion.messaging_conversation_started_7d"

# Campos que pedimos a la API de insights a nivel anuncio.
AD_INSIGHT_FIELDS = [
    "ad_id",
    "ad_name",
    "campaign_id",
    "campaign_name",
    "spend",
    "frequency",
    "impressions",
    "reach",
    "clicks",
    "actions",
]


@lru_cache(maxsize=1)
def _account_metadata() -> dict[str, tuple[str | None, str]]:
    """Diccionario `{account_id: (name, currency)}` cacheado en proceso.

    Combina dos fuentes:
    - `/me/adaccounts`: todas las cuentas a las que el token tiene acceso.
    - Cualquier `FB_AD_ACCOUNT_ID` configurado que NO haya aparecido arriba
      (se busca individualmente).

    La caché es a nivel de proceso: si añades una cuenta nueva en Facebook,
    reinicia el dashboard para refrescarla.
    """
    FacebookAdsApi.init(FB_APP_ID, FB_APP_SECRET, FB_ACCESS_TOKEN)

    metadata: dict[str, tuple[str | None, str]] = {}

    try:
        for acc in User(fbid="me").get_ad_accounts(
            fields=["id", "name", "currency"]
        ):
            acc_id = acc["id"]
            metadata[acc_id] = (acc.get("name"), acc["currency"])
    except Exception as exc:  # noqa: BLE001
        print(
            f"[WARN] no se pudo listar /me/adaccounts: {exc}", file=sys.stderr
        )

    # Si el usuario fijó cuentas en .env que no aparecieron en /me/adaccounts
    # (caso raro pero posible), las buscamos individualmente.
    configured = [a.strip() for a in FB_AD_ACCOUNT_ID.split(",") if a.strip()]
    for acc_id in configured:
        if acc_id in metadata:
            continue
        try:
            acc = AdAccount(acc_id).api_get(fields=["name", "currency"])
            metadata[acc_id] = (acc.get("name"), acc["currency"])
        except Exception as exc:  # noqa: BLE001
            print(
                f"[WARN] no se pudo leer metadata de {acc_id}: {exc}",
                file=sys.stderr,
            )

    return metadata


def _account_ids() -> list[str]:
    """Lista de cuentas a usar.

    Si `FB_AD_ACCOUNT_ID` está definido (cuentas separadas por coma) usamos
    SOLO esas (modo filtro). Si está vacío usamos todas las cuentas que
    `/me/adaccounts` ve (modo auto-discovery).
    """
    configured = [a.strip() for a in FB_AD_ACCOUNT_ID.split(",") if a.strip()]
    if configured:
        return configured
    return list(_account_metadata().keys())


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

    Devuelve columnas: ad_id, ad_name, campaign_id, campaign_name, spend,
    frequency, conversaciones, account_id, account_name, account_currency.
    """
    account_name, currency = _account_metadata().get(
        account_id, (None, TARGET_CURRENCY)
    )
    account = AdAccount(account_id)

    insights = account.get_insights(
        fields=AD_INSIGHT_FIELDS,
        params={
            "level": "ad",
            "time_range": {"since": since, "until": until},
            "limit": 500,
        },
    )

    multiplier = _currency_multiplier(currency)

    rows = [
        {
            "ad_id": str(item.get("ad_id")),
            "ad_name": item.get("ad_name"),
            "campaign_id": (
                str(item["campaign_id"]) if item.get("campaign_id") else None
            ),
            "campaign_name": item.get("campaign_name"),
            "spend": float(item.get("spend", 0) or 0) * multiplier,
            "frequency": float(item.get("frequency", 0) or 0),
            "impressions": int(item.get("impressions") or 0),
            "reach": int(item.get("reach") or 0),
            "clicks": int(item.get("clicks") or 0),
            "conversaciones": _extract_action_value(
                item.get("actions"), MESSAGING_CONVO_ACTION
            ),
            "account_id": account_id,
            "account_name": account_name,
            "account_currency": currency,
        }
        for item in insights
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "ad_id",
            "ad_name",
            "campaign_id",
            "campaign_name",
            "spend",
            "frequency",
            "impressions",
            "reach",
            "clicks",
            "conversaciones",
            "account_id",
            "account_name",
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
    _, currency = _account_metadata().get(account_id, (None, TARGET_CURRENCY))
    account = AdAccount(account_id)

    insights = account.get_insights(
        fields=["spend"],
        params={
            "level": "account",
            "time_increment": 1,
            "time_range": {"since": since, "until": until},
        },
    )

    multiplier = _currency_multiplier(currency)

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

    Columnas: ad_id, ad_name, account_id, account_name, campaign_id,
    campaign_name, spend (COP), frequency, impressions, reach, clicks,
    conversaciones.
    Recorre todas las cuentas listadas en `FB_AD_ACCOUNT_ID`. Si una cuenta falla,
    se omite con un aviso por stderr; las demás siguen funcionando.
    """
    FacebookAdsApi.init(FB_APP_ID, FB_APP_SECRET, FB_ACCESS_TOKEN)

    out_cols = [
        "ad_id",
        "ad_name",
        "account_id",
        "account_name",
        "campaign_id",
        "campaign_name",
        "spend",
        "frequency",
        "impressions",
        "reach",
        "clicks",
        "conversaciones",
    ]

    parts = []
    for acc in _account_ids():
        df_acc, err = _try_spend_for_account(acc, since, until)
        if df_acc is not None:
            parts.append(df_acc)
        else:
            print(f"[WARN] cuenta {acc} omitida: {err}", file=sys.stderr)

    if not parts:
        return pd.DataFrame(columns=out_cols)

    df = pd.concat(parts, ignore_index=True)
    return df[out_cols]


if __name__ == "__main__":
    today = date.today()
    since = (today - timedelta(days=7)).isoformat()
    until = today.isoformat()

    accounts = _account_ids()
    discovery_mode = (
        "auto-discovery (todas las del token)"
        if not [a.strip() for a in FB_AD_ACCOUNT_ID.split(",") if a.strip()]
        else "filtro FB_AD_ACCOUNT_ID"
    )
    print(f"Insights de Facebook Ads del {since} al {until}")
    print(f"Cuentas a procesar: {len(accounts)}  ({discovery_mode})")
    print(f"Tasas configuradas → COP: {CURRENCY_RATES}\n")

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
            [
                "ad_id",
                "ad_name",
                "campaign_name",
                "spend",
                "frequency",
                "conversaciones",
            ]
        ]
        print(
            f"\nTotal combinado (solo cuentas OK): {len(df)} anuncios, "
            f"gasto {df['spend'].sum():,.0f} COP, "
            f"conversaciones {df['conversaciones'].sum():,.0f}, "
            f"campañas únicas {df['campaign_name'].nunique()}\n"
        )
        print(df.to_string(index=False))
    elif not accounts:
        print("\nNo hay cuentas configuradas en FB_AD_ACCOUNT_ID.")
    else:
        print("\nNinguna cuenta devolvió datos.")
