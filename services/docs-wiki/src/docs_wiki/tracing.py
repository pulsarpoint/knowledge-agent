"""Env-gated Phoenix/OpenInference tracing. All phoenix/openinference
imports live inside functions: with DOCS_WIKI_PHOENIX_ENDPOINT unset,
none of them execute and the app behaves exactly as before. Setup
failure is a warning, never an error — observability must not take the
chat down with it."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_enabled = False


def enabled() -> bool:
    return _enabled


def setup(endpoint: str | None, api_key: str | None) -> bool:
    """Register the OTel tracer provider against Phoenix. Idempotent; returns whether tracing is on."""
    global _enabled
    if _enabled:
        return True
    if not endpoint:
        return False
    try:
        import phoenix.otel

        phoenix.otel.register(
            endpoint=endpoint, project_name="docs-wiki", batch=True,
            api_key=api_key or None, auto_instrument=False,
            set_global_tracer_provider=True, verbose=False)
    except Exception:
        logger.warning("tracing setup failed — continuing untraced",
                       exc_info=True)
        return False
    _enabled = True
    logger.info("phoenix tracing enabled (endpoint %s)", endpoint)
    return True
