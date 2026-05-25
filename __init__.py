try:
    from .adapter import register
    __all__ = ["register"]
except ImportError:
    pass  # running outside the hermes install (e.g. unit tests)
