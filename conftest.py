"""
Root conftest.py — stubs the hermes gateway so the package __init__.py can
be imported during test collection without a full gateway install.
"""
import sys
from types import ModuleType
from unittest.mock import MagicMock


def _stub(name: str) -> None:
    if name not in sys.modules:
        sys.modules[name] = MagicMock()


for _mod in (
    "gateway",
    "gateway.config",
    "gateway.platforms",
    "gateway.platforms.base",
):
    _stub(_mod)
