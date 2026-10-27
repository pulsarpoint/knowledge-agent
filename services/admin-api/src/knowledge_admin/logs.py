"""Logging for admin-api and its workers, configured once from LOG_LEVEL (debug, info or warning; default info)."""

import logging
import os

LEVELS = {'debug': logging.DEBUG, 'info': logging.INFO, 'warning': logging.WARNING}
FORMAT = '%(asctime)s %(levelname)s %(name)s %(message)s'


def parse_level(value: str | None) -> str:
    """'DEBUG' -> 'debug'; None or blank -> 'info'; any other name is refused with a clear message."""
    name = (value or '').strip().lower() or 'info'
    if name not in LEVELS:
        raise ValueError(f'LOG_LEVEL must be debug, info or warning, not "{value}"')
    return name


def configure(level: str | None) -> str:
    """Log lines with time, level, logger and message. The application's loggers (knowledge_admin.*) use the level;
    libraries stay at info at most, because their debug output can carry request bodies and credentials."""
    name = parse_level(level)
    logging.basicConfig(level=max(LEVELS[name], logging.INFO), format=FORMAT, force=True)
    logging.getLogger('knowledge_admin').setLevel(LEVELS[name])
    return name


def configure_from_env() -> str:
    """Workers: configure from LOG_LEVEL; an invalid value stops the process at start with the message."""
    try:
        return configure(os.environ.get('LOG_LEVEL'))
    except ValueError as exc:
        raise SystemExit(f'error: {exc}') from None
