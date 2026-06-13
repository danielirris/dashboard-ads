"""Tasas de cambio a COP — módulo compartido.

Lee del entorno todas las variables `<CCY>_TO_COP` (USD_TO_COP, MXN_TO_COP,
PEN_TO_COP, …) y expone helpers para convertir importes a pesos colombianos.

Usado por `facebook_client.py` (gasto de FB) y `supabase_client.py` (ventas).
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()

TARGET_CURRENCY = "COP"


def load_currency_rates() -> dict[str, float]:
    """Lee del entorno todas las variables `<CCY>_TO_COP` y construye
    `{moneda: tasa_a_COP}`.
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


CURRENCY_RATES = load_currency_rates()


def currency_multiplier(currency: str) -> float:
    """Devuelve cuánto hay que multiplicar para pasar `currency` a COP.

    Si la cuenta ya está en COP devuelve 1.0. Si no hay tasa configurada,
    imprime un aviso y devuelve 1.0 (el importe se queda en su moneda original).
    Define `<CCY>_TO_COP` en el `.env`.
    """
    if currency == TARGET_CURRENCY:
        return 1.0
    rate = CURRENCY_RATES.get(currency)
    if rate is None:
        print(
            f"[WARN] sin tasa configurada para {currency}; el importe NO se "
            f"convertirá. Define {currency}_TO_COP en .env.",
            file=sys.stderr,
        )
        return 1.0
    return rate
