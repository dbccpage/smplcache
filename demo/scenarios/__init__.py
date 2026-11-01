# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Deterministic scenario fixtures, committed boundary execution, and independent
native SQLite verification for SmplCache Live.
"""

from __future__ import annotations

from demo.scenarios.runner import (
    ALLOWED_AUX_CAPACITIES,
    ALLOWED_DATASET_SIZES,
    API_VERSION,
    SCENARIO_CATALOG,
    SCENARIO_VERSION,
    ScenarioConfig,
    execute_scenario_run,
    get_rejected_sql_examples,
    get_scenarios_metadata,
    get_version_metadata,
)

__all__ = [
    "ALLOWED_AUX_CAPACITIES",
    "ALLOWED_DATASET_SIZES",
    "API_VERSION",
    "SCENARIO_CATALOG",
    "SCENARIO_VERSION",
    "ScenarioConfig",
    "execute_scenario_run",
    "get_rejected_sql_examples",
    "get_scenarios_metadata",
    "get_version_metadata",
]
