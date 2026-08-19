"""Closed registry for typed tool adapters."""

from __future__ import annotations

from .protocol import Tool


class ToolRegistry:
    def __init__(self, tools: tuple[Tool, ...] = ()) -> None:
        self._tools: dict[str, list[Tool]] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        tool.spec.validate()
        existing = self._tools.setdefault(tool.spec.name, [])
        for registered in existing:
            if (
                registered.spec.schema_version != tool.spec.schema_version
                or registered.spec.risk_class is not tool.spec.risk_class
            ):
                raise ValueError("same-name tool routes must share schema and risk class")
            if set(registered.spec.allowed_roots) & set(tool.spec.allowed_roots):
                raise ValueError(
                    f"tool {tool.spec.name!r} already has an overlapping root route"
                )
        existing.append(tool)

    def require(self, name: str, *, root: str | None = None) -> Tool:
        tool = self.get(name, root=root)
        if tool is None:
            scope = "" if root is None else f" for root {root!r}"
            raise KeyError(f"unknown, unsupported, or ambiguous tool {name!r}{scope}")
        return tool

    def get(self, name: str, *, root: str | None = None) -> Tool | None:
        candidates = self._tools.get(name, [])
        if root is None:
            return candidates[0] if len(candidates) == 1 else None
        matches = [tool for tool in candidates if root in tool.spec.allowed_roots]
        return matches[0] if len(matches) == 1 else None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def specifications(self) -> tuple[object, ...]:
        return tuple(
            tool.spec
            for name in sorted(self._tools)
            for tool in sorted(self._tools[name], key=lambda item: item.spec.allowed_roots)
        )
