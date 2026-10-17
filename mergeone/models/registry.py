"""Decision-model registry: prompts/models.yaml → model instances.

A model is a row in the YAML, not a class. `build("pplx-flash")` or `build("clef")` works the day the
entry is added. Three wire dialects are implemented:

  systemone   {state, model, questions{noul|choice|score}} → {answers{noul|probabilities}, usage, model}
  decide      same questions, `/v1/decide` envelope (AutoTrust, AnyJev); answers may be under `decisions`
  anthropic   Claude Messages API with a JSON schema (mergeone.models.haiku)

Environment expansion: `${VAR}` and `${VAR:-default}` in endpoint / model_id. `key_optional: true` lets a
local server run with no key.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "prompts" / "models.yaml"
_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")
BUILTIN = ("rules", "gbm", "mock")  # not API models; built in cli.build_model


def expand_env(s: str | None) -> str | None:
    if not s:
        return s
    return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), s)


@dataclass
class ModelSpec:
    name: str
    dialect: str
    endpoint: str
    key_env: str = ""
    key_optional: bool = False
    model_id: str = ""
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    hosted: str = ""
    weights: str = ""
    license: str = ""
    params_b: float | None = None
    rps: int | None = None
    context: int | None = None
    notes: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, name: str, d: dict) -> "ModelSpec":
        known = {k for k in cls.__dataclass_fields__ if k not in ("name", "extra")}
        kw = {k: d[k] for k in known if k in d}
        kw["endpoint"] = expand_env(kw.get("endpoint", "")) or ""
        kw["model_id"] = expand_env(kw.get("model_id", "")) or ""
        return cls(name=name, extra={k: v for k, v in d.items() if k not in known}, **kw)

    @property
    def open_weights(self) -> bool:
        return bool(self.weights)

    @property
    def self_hosted(self) -> bool:
        return "127.0.0.1" in self.endpoint or "localhost" in self.endpoint


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, ModelSpec]:
    d = yaml.safe_load(Path(path).read_text()) or {}
    return {k: ModelSpec.from_yaml(k, v or {}) for k, v in (d.get("models") or {}).items()}


def names(path: Path = REGISTRY_PATH) -> list[str]:
    return list(BUILTIN) + list(load_registry(path))


def build(name: str, prices: dict | None = None, version: str = "v1", path: Path = REGISTRY_PATH):
    """Instantiate a registry model. Raises KeyError for unknown names (built-ins are handled by the caller)."""
    reg = load_registry(path)
    spec = reg[name]
    if spec.dialect == "anthropic":
        from .haiku import HaikuModel
        return HaikuModel(prices, version=version, model=spec.model_id, spec=spec)
    if spec.dialect in ("systemone", "decide"):
        from .jev import SystemOneModel
        return SystemOneModel(spec, prices, version=version)
    raise ValueError(f"model {name}: unknown dialect {spec.dialect!r}")
