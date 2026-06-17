"""Cliente de solo lectura para Facebook Marketing API.

Soporta MÚLTIPLES tokens (uno por Business Manager). Cada token descubre sus
cuentas vía `/me/adaccounts` y cada cuenta queda asociada al `FacebookAdsApi`
del token que la descubrió — por eso los insights por cuenta siempre usan el
token correcto sin pisar el singleton global de la SDK.

Métricas por anuncio (nivel `ad`): spend (COP), frequency, impressions, reach,
clicks, conversaciones (mensajes iniciados de WhatsApp via `actions`).
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd
from dotenv import load_dotenv
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.user import User
from facebook_business.api import FacebookAdsApi
from facebook_business.session import FacebookSession
from pathlib import Path
from supabase import create_client

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

from currency import CURRENCY_RATES, currency_multiplier  # noqa: E402

FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")
# Filtro OPCIONAL. Si está vacío, descubrimos todas las cuentas que el token
# pueda ver vía /me/adaccounts. Si está definido, usamos solo esas.
FB_AD_ACCOUNT_ID = os.getenv("FB_AD_ACCOUNT_ID", "")
FB_APP_ID = os.getenv("FB_APP_ID")
FB_APP_SECRET = os.getenv("FB_APP_SECRET")

# Supabase — usados SOLO por `sync_ads_to_supabase` para escribir en `anuncios`.
# El resto del proyecto lee desde supabase_client.py.
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ANUNCIOS_TABLE = os.getenv("ANUNCIOS_TABLE", "anuncios")

# ──────────────────────────────────────────────────────────────────────────────
# Tokens (multi-Business-Manager) y construcción de APIs por token
# ──────────────────────────────────────────────────────────────────────────────

_TOKEN_PATTERN = re.compile(r"^FB_TOKEN_(\d+)$")


@lru_cache(maxsize=1)
def _load_tokens() -> list[tuple[str, str]]:
    """Descubre todos los tokens del entorno con el patrón `FB_TOKEN_<N>`.

    Por cada `FB_TOKEN_<N>` mira si existe `FB_LABEL_<N>` para etiquetarlo;
    si no, usa `token_<N>` como label. Devuelve `[(label, token), ...]`
    ordenado por N.

    Fallback retrocompatible: si NO encuentra ningún `FB_TOKEN_<N>` pero existe
    el viejo `FB_ACCESS_TOKEN`, devuelve `[("default", FB_ACCESS_TOKEN)]`.

    Cacheado en proceso — el .env se lee al importar el módulo.
    """
    found: list[tuple[int, str, str]] = []
    for key, value in os.environ.items():
        m = _TOKEN_PATTERN.match(key)
        if not m or not value.strip():
            continue
        n = int(m.group(1))
        label = (os.getenv(f"FB_LABEL_{n}") or f"token_{n}").strip()
        found.append((n, label, value))

    if not found:
        if FB_ACCESS_TOKEN:
            return [("default", FB_ACCESS_TOKEN)]
        return []

    found.sort(key=lambda x: x[0])
    return [(label, token) for _, label, token in found]


def _make_api(token: str) -> FacebookAdsApi:
    """Crea una instancia de FacebookAdsApi enlazada a un token concreto.

    Usamos `FacebookSession` directamente para NO modificar el singleton global
    (el patrón habitual `FacebookAdsApi.init()` lo pisa y rompe entre llamadas
    con distintos tokens). Toda llamada al SDK debe pasar `api=` explícitamente
    al constructor de `AdAccount(...)`, `User(fbid="me", api=...)`, etc.
    """
    session = FacebookSession(
        app_id=FB_APP_ID,
        app_secret=FB_APP_SECRET,
        access_token=token,
    )
    return FacebookAdsApi(session)


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
def _account_metadata() -> dict[str, dict]:
    """`{account_id: {"api", "label", "name", "currency"}}` cacheado en proceso.

    Itera todos los tokens devueltos por `_load_tokens()`. Por cada uno crea su
    propia instancia de `FacebookAdsApi` (vía `_make_api`), consulta
    `/me/adaccounts` y registra cada cuenta con el `api` específico de ESE
    token. Eso es lo que permite que `get_ad_spend` y `get_daily_spend`
    pasen `api=` correcto a `AdAccount(...)` sin tocar el singleton global.

    Reglas:
    - Si una `account_id` aparece bajo dos tokens, se conserva la PRIMERA
      descubierta y se imprime un aviso por stderr.
    - Si un token falla (token expirado, sin permisos, etc.), se omite con
      aviso y se continúa con los demás. Un token caído NO debe tumbar el
      dashboard.
    - Si `FB_AD_ACCOUNT_ID` fija cuentas que NO aparecieron en
      `/me/adaccounts` de ningún token, se intentan leer individualmente
      probando los tokens en orden.
    """
    metadata: dict[str, dict] = {}
    tokens = _load_tokens()

    for label, token in tokens:
        try:
            api = _make_api(token)
            for acc in User(fbid="me", api=api).get_ad_accounts(
                fields=["id", "name", "currency"]
            ):
                acc_id = acc["id"]
                if acc_id in metadata:
                    print(
                        f"[WARN] cuenta {acc_id} accesible por múltiples "
                        f"tokens, usando label {metadata[acc_id]['label']}",
                        file=sys.stderr,
                    )
                    continue
                metadata[acc_id] = {
                    "api": api,
                    "label": label,
                    "name": acc.get("name"),
                    "currency": acc["currency"],
                }
        except Exception as exc:  # noqa: BLE001
            print(
                f"[WARN] token con label {label} falló: {exc}",
                file=sys.stderr,
            )
            continue

    # Cuentas explícitas en FB_AD_ACCOUNT_ID que no salieron por /me/adaccounts:
    # las pedimos individualmente probando cada token en orden.
    configured = [a.strip() for a in FB_AD_ACCOUNT_ID.split(",") if a.strip()]
    for acc_id in configured:
        if acc_id in metadata:
            continue
        found_via = None
        for label, token in tokens:
            try:
                api = _make_api(token)
                acc = AdAccount(acc_id, api=api).api_get(
                    fields=["name", "currency"]
                )
                metadata[acc_id] = {
                    "api": api,
                    "label": label,
                    "name": acc.get("name"),
                    "currency": acc["currency"],
                }
                found_via = label
                break
            except Exception:  # noqa: BLE001
                continue
        if not found_via:
            print(
                f"[WARN] no se pudo leer metadata de {acc_id} con ningún token",
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


def clear_account_cache() -> None:
    """Limpia el caché de descubrimiento de tokens y cuentas.

    `_load_tokens` y `_account_metadata` usan `@lru_cache` (viven por todo el
    proceso). El botón "Refrescar datos" del dashboard solo borra el caché de
    Streamlit, no este. Llamar a esto fuerza a re-descubrir `/me/adaccounts`
    en la próxima consulta — necesario cuando se agrega una cuenta nueva al
    Business Manager sin reiniciar la app.
    """
    _load_tokens.cache_clear()
    _account_metadata.cache_clear()


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

    Usa el `api` específico de la cuenta (el token bajo el que se descubrió)
    para que las llamadas siempre usen el token correcto incluso con varios
    Business Managers cargados.

    Devuelve columnas: ad_id, ad_name, campaign_id, campaign_name, spend,
    frequency, impressions, reach, clicks, conversaciones, account_id,
    account_name, account_currency, cuenta_perfil.
    """
    meta = _account_metadata().get(account_id)
    if meta is None:
        raise RuntimeError(
            f"Cuenta {account_id} no accesible por ningún token cargado."
        )

    api = meta["api"]
    account_name = meta["name"]
    currency = meta["currency"]
    label = meta["label"]

    account = AdAccount(account_id, api=api)
    insights = account.get_insights(
        fields=AD_INSIGHT_FIELDS,
        params={
            "level": "ad",
            "time_range": {"since": since, "until": until},
            "limit": 500,
        },
    )

    multiplier = currency_multiplier(currency)

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
            "cuenta_perfil": label,
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
            "cuenta_perfil",
        ],
    )


def discover_action_types(since: str, until: str) -> dict[str, float]:
    """Devuelve un dict {action_type: suma_total_de_valores} con todos los
    action_types vistos en los insights del rango, sumados sobre todas las cuentas.

    Útil para identificar el nombre exacto del action que corresponde a
    'conversación iniciada en WhatsApp' (p. ej. messaging_conversation_started_7d
    vs total_messaging_connection vs onsite_conversion.messaging_first_reply…).
    """
    counts: dict[str, float] = {}
    metadata = _account_metadata()
    for acc_id in _account_ids():
        meta = metadata.get(acc_id)
        if meta is None:
            print(
                f"[WARN] {acc_id} omitida en inspección: no accesible por "
                f"ningún token",
                file=sys.stderr,
            )
            continue
        try:
            insights = AdAccount(acc_id, api=meta["api"]).get_insights(
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
            print(
                f"[WARN] {acc_id} omitida en inspección: {exc}",
                file=sys.stderr,
            )
    return counts


def _try_spend_for_account(
    account_id: str, since: str, until: str
) -> tuple[pd.DataFrame | None, str | None]:
    """Variante tolerante a fallos: si la cuenta falla, devuelve (None, mensaje_error)."""
    try:
        return _spend_for_account(account_id, since, until), None
    except Exception as exc:  # noqa: BLE001 — queremos atrapar cualquier fallo de API
        return None, str(exc)


# ──────────────────────────────────────────────────────────────────────────────
# Estado del anuncio (ACTIVE / PAUSED / etc.) — endpoint Ads, NO Insights
# ──────────────────────────────────────────────────────────────────────────────

_STATUS_TTL_SECONDS = 300  # 5 minutos
_status_cache: tuple[float, dict[str, str]] | None = None


def _normalize_status(raw: str) -> str:
    """Normaliza `effective_status` de FB a 3 categorías para el dashboard:
    - 'prendido'  → ACTIVE
    - 'apagado'   → cualquier PAUSED (PAUSED, ADSET_PAUSED, CAMPAIGN_PAUSED)
    - 'otro'      → DELETED, ARCHIVED, DISAPPROVED, PENDING_REVIEW, IN_PROCESS, etc.
    """
    if not raw:
        return "otro"
    if raw == "ACTIVE":
        return "prendido"
    if "PAUSED" in raw:
        return "apagado"
    return "otro"


def get_ad_statuses() -> dict[str, str]:
    """Devuelve `{ad_id: effective_status_raw}` para TODOS los anuncios de todas
    las cuentas accesibles (todos los tokens).

    Caché en proceso con TTL manual de 5 minutos (timestamp guardado y comparado
    explícitamente) — no usamos `@lru_cache` porque no soporta TTL nativo.
    Llama a `AdAccount.get_ads()` (endpoint Ads, NO Insights). Si una cuenta
    falla, se omite con un aviso por stderr; las demás siguen.
    """
    global _status_cache
    now = time.time()
    if _status_cache is not None and (now - _status_cache[0]) < _STATUS_TTL_SECONDS:
        return _status_cache[1]

    result: dict[str, str] = {}
    metadata = _account_metadata()
    accounts = list(metadata.items())
    for i, (acc_id, meta) in enumerate(accounts):
        try:
            ads = AdAccount(acc_id, api=meta["api"]).get_ads(
                fields=["id", "effective_status", "status"],
                params={"limit": 500},
            )
            for ad in ads:
                result[str(ad["id"])] = ad.get("effective_status", "")
        except Exception as exc:  # noqa: BLE001
            print(
                f"[WARN] no se pudo traer status de cuenta {acc_id}: {exc}",
                file=sys.stderr,
            )
        # Rate limit guard entre cuentas distintas (excepto después de la última).
        if i < len(accounts) - 1:
            time.sleep(0.5)

    _status_cache = (now, result)
    return result


def _daily_spend_for_account(
    account_id: str, since: str, until: str
) -> pd.DataFrame:
    """Gasto diario de una cuenta (nivel cuenta, time_increment=1), convertido a COP.

    Usa el `api` específico de la cuenta (multi-token safe).
    Devuelve columnas: date, spend.
    """
    meta = _account_metadata().get(account_id)
    if meta is None:
        raise RuntimeError(
            f"Cuenta {account_id} no accesible por ningún token cargado."
        )

    api = meta["api"]
    currency = meta["currency"]

    account = AdAccount(account_id, api=api)
    insights = account.get_insights(
        fields=["spend"],
        params={
            "level": "account",
            "time_increment": 1,
            "time_range": {"since": since, "until": until},
        },
    )

    multiplier = currency_multiplier(currency)

    rows = [
        {
            "date": item.get("date_start"),
            "spend": float(item.get("spend", 0) or 0) * multiplier,
        }
        for item in insights
    ]
    return pd.DataFrame(rows, columns=["date", "spend"])


def get_daily_spend(since: str, until: str) -> pd.DataFrame:
    """Gasto diario combinado de todas las cuentas (todos los tokens), en COP.

    Devuelve columnas: date (datetime), spend (float). Las cuentas que fallen se omiten
    con un aviso por stderr.
    """
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


def get_daily_spend_by_ad(since: str, until: str) -> pd.DataFrame:
    """Gasto diario por anuncio de todas las cuentas, en COP.

    Devuelve columnas: date (str YYYY-MM-DD), ad_id (str), spend (float COP).
    Usa `level=ad` con `time_increment=1` para desglosar por anuncio y día.

    NO cachear con `@lru_cache` aquí: ese caché vive por todo el proceso y no
    lo limpia el botón "Refrescar datos" del dashboard (que solo borra
    `st.cache_data`), dejando la gráfica diaria congelada. El cacheo se hace
    en `app.py` con `@st.cache_data` (respeta el TTL y el botón Refrescar).
    """
    parts = []
    for acc in _account_ids():
        try:
            meta = _account_metadata().get(acc)
            if meta is None:
                continue
            api = meta["api"]
            currency = meta["currency"]
            multiplier = currency_multiplier(currency)

            account = AdAccount(acc, api=api)
            insights = account.get_insights(
                fields=["ad_id", "spend"],
                params={
                    "level": "ad",
                    "time_increment": 1,
                    "time_range": {"since": since, "until": until},
                    "limit": 5000,
                },
            )
            for item in insights:
                parts.append(
                    {
                        "date": item.get("date_start"),
                        "ad_id": str(item.get("ad_id")),
                        "spend": float(item.get("spend", 0) or 0) * multiplier,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[WARN] cuenta {acc} omitida (daily by ad): {exc}",
                file=sys.stderr,
            )

    if not parts:
        return pd.DataFrame(columns=["date", "ad_id", "spend"])

    df = pd.DataFrame(parts)
    df = df.groupby(["date", "ad_id"], as_index=False)["spend"].sum()
    return df


def get_ad_spend(since: str, until: str) -> pd.DataFrame:
    """Devuelve un DataFrame combinado con métricas por anuncio.

    Columnas: ad_id, ad_name, account_id, account_name, campaign_id,
    campaign_name, spend (COP), frequency, impressions, reach, clicks,
    conversaciones, cuenta_perfil, estado, estado_raw.

    Recorre todas las cuentas devueltas por `_account_ids()` (todas las
    descubiertas por todos los tokens, o el filtro de `FB_AD_ACCOUNT_ID`).
    Cada llamada usa el `api` específico del token al que pertenece la cuenta.
    Si una cuenta falla, se omite con un aviso por stderr; las demás siguen.

    El `estado` (prendido / apagado / otro) viene del endpoint Ads de FB
    (`effective_status`), no de Insights. Cacheado con TTL de 5 min.
    `estado_raw` lleva el valor original de FB para debug.
    """
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
        "cuenta_perfil",
        "estado",
        "estado_raw",
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

    # Mergear estado (endpoint Ads, no Insights) por ad_id.
    statuses = get_ad_statuses()
    df["estado_raw"] = df["ad_id"].map(statuses).fillna("")
    df["estado"] = df["estado_raw"].apply(_normalize_status)

    return df[out_cols]


if __name__ == "__main__":
    from collections import Counter

    tokens = _load_tokens()
    metadata = _account_metadata()

    print("=" * 72)
    print(" Multi-token Facebook client — resumen de descubrimiento")
    print("=" * 72)
    print()

    # ── 1) Tokens cargados ───────────────────────────────────────────────
    print(f"Tokens cargados: {len(tokens)}")
    if not tokens:
        print(
            "  (ninguno — define FB_TOKEN_1, FB_TOKEN_2, … en .env, o "
            "FB_ACCESS_TOKEN como fallback retrocompatible)"
        )
    else:
        for label, _token in tokens:
            # No imprimimos el token en claro.
            print(f"  - {label}")

    # ── 2) Cuentas por label ─────────────────────────────────────────────
    print(f"\nCuentas descubiertas: {len(metadata)}")
    if metadata:
        counts_by_label = Counter(m["label"] for m in metadata.values())
        for label, n in counts_by_label.most_common():
            print(f"  - {label}: {n} cuenta(s)")

    # ── 3) Detalle por cuenta ────────────────────────────────────────────
    if metadata:
        print("\nDetalle por cuenta:")
        header = (
            f"{'account_id':<22} | {'label':<20} | "
            f"{'name':<40} | currency"
        )
        print(header)
        print("-" * len(header))
        for acc_id, meta in metadata.items():
            name = (meta["name"] or "?")[:40]
            print(
                f"{acc_id:<22} | {meta['label']:<20} | "
                f"{name:<40} | {meta['currency']}"
            )

    # ── 4) Filtro y tasas ────────────────────────────────────────────────
    print(f"\nTasas a COP configuradas: {CURRENCY_RATES}")

    configured = [a.strip() for a in FB_AD_ACCOUNT_ID.split(",") if a.strip()]
    if configured:
        print(
            f"\nFB_AD_ACCOUNT_ID activo: solo se procesarán "
            f"{len(configured)} cuenta(s) filtrada(s):"
        )
        for acc_id in configured:
            if acc_id in metadata:
                print(f"  ✓ {acc_id}  (label: {metadata[acc_id]['label']})")
            else:
                print(f"  ✗ {acc_id}  (NO accesible por ningún token)")
    else:
        print(
            f"\nFB_AD_ACCOUNT_ID vacío → modo auto-discovery: se usarán las "
            f"{len(metadata)} cuenta(s) descubierta(s)."
        )


# ──────────────────────────────────────────────────────────────────────────────
# Sincronización a Supabase (única escritura del proyecto)
# ──────────────────────────────────────────────────────────────────────────────


def sync_ads_to_supabase(since: str, until: str) -> dict:
    """Sincroniza los anuncios del rango hacia la tabla `anuncios` de Supabase.

    Comportamiento: **INSERT-ONLY**. Si el `ad_id` ya existe en `anuncios`,
    esa fila NO se toca — se preservan `notas`, `producto_id`, `ad_headline`
    y cualquier `activo=False` puesto manualmente. Solo inserta filas nuevas.

    Para cada anuncio NUEVO escribe:
        ad_id, ad_nombre, campaign (= campaign_name de Facebook),
        activo=True, updated_at (UTC ahora).

    Para escribir prefiere `SUPABASE_SERVICE_KEY` del `.env` (service_role,
    bypassea RLS). Si no está definida, hace fallback a `SUPABASE_KEY` —
    eso puede fallar si tu RLS no permite INSERT con la anon key.

    Devuelve:
        {"requested": int, "new": int, "errors": list[str]}
        - requested: anuncios devueltos por Facebook en el rango
        - new:       anuncios realmente insertados (los demás ya existían)
        - errors:    mensajes de error si la API falló
    """
    ads_df = get_ad_spend(since, until)
    ads_df = (
        ads_df.dropna(subset=["ad_id"]).drop_duplicates(subset=["ad_id"])
    )

    now_utc = datetime.now(timezone.utc).isoformat()
    records = [
        {
            "ad_id": str(row["ad_id"]),
            "ad_nombre": row["ad_name"],
            "campaign": row["campaign_name"],
            "activo": True,
            "updated_at": now_utc,
        }
        for _, row in ads_df.iterrows()
    ]

    if not records:
        return {"requested": 0, "new": 0, "errors": []}

    # Preferimos service key para escritura (bypassea RLS); si no, anon.
    write_key = os.getenv("SUPABASE_SERVICE_KEY") or SUPABASE_KEY

    try:
        client = create_client(SUPABASE_URL, write_key)
        # ignore_duplicates=True → ON CONFLICT (ad_id) DO NOTHING.
        # PostgREST devuelve en `data` SOLO las filas realmente insertadas.
        result = (
            client.table(ANUNCIOS_TABLE)
            .upsert(records, on_conflict="ad_id", ignore_duplicates=True)
            .execute()
        )
        inserted = len(result.data) if result.data else 0
        return {"requested": len(records), "new": inserted, "errors": []}
    except Exception as exc:  # noqa: BLE001
        return {
            "requested": len(records),
            "new": 0,
            "errors": [str(exc)],
        }
