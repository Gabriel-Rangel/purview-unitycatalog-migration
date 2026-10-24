"""Map Purview qualifiedNames to Unity Catalog full names.

Explicit regex rules + an overrides CSV instead of fuzzy name matching: a wrong match would copy
the wrong PII classification onto the wrong table, so every mapping must be reviewable.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .config import MatchingConfig


@dataclass
class MatchResult:
    matched: list[tuple[dict, str, str]] = field(default_factory=list)  # (asset, uc_full_name, how)
    purview_unmatched: list[tuple[dict, str, str | None]] = field(default_factory=list)  # (asset, reason, target)
    ambiguous: list[tuple[dict, str]] = field(default_factory=list)  # (asset, uc_full_name)
    uc_unmatched: list[str] = field(default_factory=list)


def resolve_target(qualified_name: str, entity_type: str, cfg: MatchingConfig) -> tuple[str | None, str]:
    """Return (uc_full_name, how). uc_full_name None means 'no rule' or 'ignored by override'."""
    if qualified_name in cfg.overrides:
        target = cfg.overrides[qualified_name]
        return (target or None), ("override" if target else "override:ignore")

    for i, rule in enumerate(cfg.rules):
        if rule.entity_types and entity_type not in rule.entity_types:
            continue
        m = rule.pattern.match(qualified_name)
        if not m:
            continue
        values = {}
        for group, raw in m.groupdict().items():
            value = rule.maps.get(group, {}).get((raw or "").lower(), raw or "")
            values[group] = value.lower() if rule.normalize == "lower" else value
        return rule.uc_target.format(**values), f"rule:{i}"
    return None, "no_rule"


def match_assets(assets: list[dict], cfg: MatchingConfig, uc_tables: set[str], uc_scope: set[str] | None = None) -> MatchResult:
    """uc_tables: lowercase full names that exist in UC. uc_scope: tables that should have a Purview
    counterpart (used for the 'only in UC' report)."""
    result = MatchResult()
    by_target: dict[str, list[tuple[dict, str]]] = defaultdict(list)

    for asset in assets:
        target, how = resolve_target(asset["qualified_name"], asset["type_name"], cfg)
        if how == "override:ignore":
            continue
        if target is None:
            result.purview_unmatched.append((asset, "NO_MATCHING_RULE", None))
            continue
        target = target.lower()
        if target not in uc_tables:
            result.purview_unmatched.append((asset, "UC_TABLE_NOT_FOUND", target))
            continue
        by_target[target].append((asset, how))

    for target, candidates in by_target.items():
        if len(candidates) > 1:
            # Dois assets do Purview apontando para a mesma tabela: não escolhemos por conta própria.
            result.ambiguous.extend((asset, target) for asset, _ in candidates)
        else:
            asset, how = candidates[0]
            result.matched.append((asset, target, how))

    targeted = set(by_target)
    result.uc_unmatched = sorted((uc_scope or set()) - targeted)
    return result
