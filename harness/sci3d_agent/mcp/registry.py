from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

ToolHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]
Category = Literal["A", "B", "C", "D", "E", "F"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: ToolHandler
    category: Category


REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in REGISTRY:
        raise ValueError(f"duplicate tool: {spec.name}")
    REGISTRY[spec.name] = spec
    return spec


def all_tools() -> list[ToolSpec]:
    return list(REGISTRY.values())
