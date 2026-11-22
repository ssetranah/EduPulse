"""Source connectors and the registry that maps ``type:`` in YAML to a class."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..errors import ConfigError
from ..retry import RetryPolicy
from .base import SourceConnector
from .file_drop import FileDropConnector
from .mock_system import (
    MockAssessmentConnector,
    MockAttendanceInterventionConnector,
    MockLMSConnector,
    MockSISConnector,
)
from .rest_api import RestApiConnector

CONNECTORS: dict[str, type[SourceConnector]] = {
    cls.source_type: cls
    for cls in (
        FileDropConnector,
        RestApiConnector,
        MockSISConnector,
        MockLMSConnector,
        MockAssessmentConnector,
        MockAttendanceInterventionConnector,
    )
}


def build_connector(
    name: str,
    config: Any,
    *,
    retry_policy: RetryPolicy,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    env: Mapping[str, str] | None = None,
    base_dir: Path | None = None,
) -> SourceConnector:
    try:
        cls = CONNECTORS[config.type]
    except KeyError:
        raise ConfigError(f"no connector registered for type '{config.type}'") from None
    return cls(
        name, config, retry_policy=retry_policy, sleep=sleep, rng=rng, env=env, base_dir=base_dir
    )


__all__ = ["CONNECTORS", "SourceConnector", "build_connector"]
