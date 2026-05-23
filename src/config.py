"""Configuración del dashboard. Lee y escribe `config.json` en la raíz.

Aquí viven **solo los parámetros del negocio**: umbrales del semáforo, margen,
metas. Las credenciales (API keys, tokens) NUNCA pasan por este módulo — ésas
siguen en el `.env` y se leen directamente desde los clientes (facebook_client,
supabase_client, etc.).

Si `config.json` no existe, lo creamos con un bootstrap: tomamos los valores
del `.env` (compatibilidad con la versión previa) y, si faltan, usamos los
defaults razonables definidos abajo.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

# Estructura canónica + valores por defecto si nunca configuraste nada.
DEFAULTS: dict[str, float] = {
    "margen_porcentaje": 0.30,
    "cpa_bueno": 80000.0,
    "cpa_maximo": 120000.0,
    "roas_minimo": 2.0,
    "roas_bueno": 3.0,
    "meta_ganancia_diaria": 100000.0,
}

# Mapeo para sembrar config.json desde el .env la primera vez (migración suave
# desde la versión anterior del proyecto donde estos vivían en .env).
_ENV_BOOTSTRAP_MAP = {
    "margen_porcentaje": "MARGEN_PORCENTAJE",
    "cpa_bueno": "CPA_BUENO",
    "cpa_maximo": "CPA_MAXIMO",
    "roas_minimo": "ROAS_MINIMO",
    "roas_bueno": "ROAS_BUENO",
    "meta_ganancia_diaria": "META_GANANCIA_DIARIA",
}


def _env_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _bootstrap_from_env() -> dict[str, float]:
    """Construye un dict de config combinando defaults con cualquier valor que
    encuentre en el .env (versión antigua del proyecto)."""
    cfg = dict(DEFAULTS)
    for key, env_name in _ENV_BOOTSTRAP_MAP.items():
        val = _env_float(env_name)
        if val is not None:
            cfg[key] = val
    return cfg


def load_config() -> dict[str, float]:
    """Lee `config.json`. Si no existe o está corrupto, lo regenera desde
    bootstrap. Garantiza que todas las claves de DEFAULTS están presentes
    (relleno por si el archivo en disco es de una versión anterior)."""
    if not CONFIG_PATH.exists():
        cfg = _bootstrap_from_env()
        save_config(cfg)
        return cfg
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        cfg = _bootstrap_from_env()
        save_config(cfg)
        return cfg
    # Merge con defaults: claves desconocidas se descartan; claves faltantes
    # toman el default.
    merged = dict(DEFAULTS)
    for k, v in raw.items():
        if k in DEFAULTS:
            try:
                merged[k] = float(v)
            except (TypeError, ValueError):
                pass  # mantenemos el default si el valor es basura
    return merged


def save_config(cfg: dict[str, float]) -> None:
    """Escribe `config.json`. Solo persiste claves conocidas (escudo contra
    meter secretos por accidente). Convierte todo a float para consistencia."""
    safe = {}
    for k in DEFAULTS:
        if k in cfg:
            safe[k] = float(cfg[k])
        else:
            safe[k] = DEFAULTS[k]
    CONFIG_PATH.write_text(
        json.dumps(safe, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def get_config() -> dict[str, float]:
    """Acceso conveniente. Releé desde disco cada vez (archivo pequeño y se
    llama pocas veces por render — preferimos simplicidad a microoptimización)."""
    return load_config()
