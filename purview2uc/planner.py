"""Diff desired metadata (from Purview) against current Unity Catalog state -> list of actions.

Pure Python on purpose (no Spark): this is the logic that must be right, so it is unit-tested.

Action rows are what lands in the sync_actions control table:
  action  : SET_TABLE_COMMENT | SET_COLUMN_COMMENT | SET_TABLE_TAG | SET_COLUMN_TAG | NO_MATCH | SKIP_OBJECT
  status  : PLANNED | CONFLICT | SKIPPED  (plan)   APPLIED | FAILED | DRIFTED | SKIPPED  (apply)
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field

from . import sql
from .config import Config
from .matching import MatchResult
from .transform import derive_tags, normalize_comment, pick_description

TABLE_COMMENT = "SET_TABLE_COMMENT"
COLUMN_COMMENT = "SET_COLUMN_COMMENT"
TABLE_TAG = "SET_TABLE_TAG"
COLUMN_TAG = "SET_COLUMN_TAG"
NO_MATCH = "NO_MATCH"
SKIP_OBJECT = "SKIP_OBJECT"

# Chaves comuns não aceitam estes caracteres; governed tags (ex.: class.email_address) aceitam.
_RESERVED_KEY_CHARS = re.compile(r"[.,\-=/:]")


@dataclass
class UcTable:
    full_name: str
    table_type: str
    comment: str = ""
    columns: dict[str, dict] = field(default_factory=dict)  # lower name -> {"name", "comment"}
    tags: dict[str, str] = field(default_factory=dict)
    column_tags: dict[str, dict[str, str]] = field(default_factory=dict)  # lower col -> {k: v}


def _row(asset, uc_object, action, status, reason=None, column=None, tag_key=None, current=None, desired=None, source=None):
    return {
        "purview_guid": asset["guid"] if asset else None,
        "purview_type": asset["type_name"] if asset else None,
        "purview_qualified_name": asset["qualified_name"] if asset else None,
        "uc_object": uc_object,
        "column_name": column,
        "action": action,
        "tag_key": tag_key,
        "current_value": current,
        "desired_value": desired,
        "source": source,
        "status": status,
        "reason": reason,
        "sql_statement": None,
    }


def applied_key(uc_object, column, action, tag_key=None):
    return (uc_object, (column or "").lower(), action, tag_key or "")


def _plan_comment(rows, asset, uc_object, action, desired, source, current, cfg, applied, column=None):
    current = normalize_comment(current)
    if not desired or desired == current:
        return
    if current and cfg.comments.on_conflict == "fill_empty":
        # O valor no UC é o último que esta automação gravou: ninguém editou à mão, então
        # é seguro propagar a nova descrição do Purview.
        if applied.get(applied_key(uc_object, column, action)) == current:
            rows.append(_row(asset, uc_object, action, "PLANNED", "PURVIEW_UPDATED", column, None, current, desired, source))
        else:
            rows.append(_row(asset, uc_object, action, "CONFLICT", "UC_ALREADY_HAS_DIFFERENT_COMMENT", column, None, current, desired, source))
    else:
        rows.append(_row(asset, uc_object, action, "PLANNED", None, column, None, current or None, desired, source))


def tag_problem(key: str, value: str, governed: dict[str, set[str]] | None) -> str | None:
    """Validate against governed tag policies at plan time instead of failing at apply time.
    governed: {tag_key: allowed values} (empty set = key-only tag); None = policies unknown."""
    if governed is None:
        return None
    if key in governed:
        allowed = governed[key]
        if allowed and value not in allowed:
            return f"GOVERNED_TAG_VALUE_NOT_ALLOWED (permitidos: {', '.join(sorted(allowed)[:15])})"
        if not allowed and value != "":
            return "GOVERNED_TAG_IS_KEY_ONLY"
        return None
    if _RESERVED_KEY_CHARS.search(key):
        return "INVALID_TAG_KEY (. , - = / : só são aceitos em governed tags existentes)"
    return None


def _plan_tags(rows, asset, uc_object, action, desired_tags, collisions, current_tags, budget, cfg, governed, applied, column=None):
    """budget: dict with 'object' (remaining slots on this object) and 'table_columns' (remaining
    column-tag slots on the table, shared by all columns)."""
    for tag, reason in collisions:
        rows.append(_row(asset, uc_object, action, "SKIPPED", reason, column, tag.key, current_tags.get(tag.key), tag.value, tag.source))
    for tag in desired_tags:
        current = current_tags.get(tag.key)
        if current is not None and current == tag.value:
            continue
        problem = tag_problem(tag.key, tag.value, governed)
        if problem:
            rows.append(_row(asset, uc_object, action, "SKIPPED", problem, column, tag.key, current, tag.value, tag.source))
            continue
        reason = None
        if current is not None and cfg.tags.on_conflict == "skip":
            if applied.get(applied_key(uc_object, column, action, tag.key)) != current:
                rows.append(_row(asset, uc_object, action, "CONFLICT", "UC_TAG_HAS_DIFFERENT_VALUE", column, tag.key, current, tag.value, tag.source))
                continue
            reason = "PURVIEW_UPDATED"
        if current is None:
            # Uma tag nova consome espaço; sobrescrever valor de chave existente não.
            if budget["object"] <= 0 or (column is not None and budget["table_columns"] <= 0):
                rows.append(_row(asset, uc_object, action, "SKIPPED", "TAG_LIMIT_REACHED", column, tag.key, None, tag.value, tag.source))
                continue
            budget["object"] -= 1
            if column is not None:
                budget["table_columns"] -= 1
        rows.append(_row(asset, uc_object, action, "PLANNED", reason, column, tag.key, current, tag.value, tag.source))


def plan_table(asset: dict, uc: UcTable, cfg: Config, governed: dict[str, set[str]] | None = None,
               applied: dict | None = None) -> list[dict]:
    """applied: last value written by this tool per object ({applied_key: value}, from sync_actions)."""
    applied = applied or {}
    rows: list[dict] = []
    if uc.table_type in cfg.unity_catalog.skip_table_types:
        # MV/Streaming Tables são gerenciadas por pipeline: o próximo update desfaz edições externas.
        rows.append(_row(asset, uc.full_name, SKIP_OBJECT, "SKIPPED", f"TABLE_TYPE_{uc.table_type}"))
        return rows

    if cfg.comments.table_comments:
        desired, source = pick_description(asset, cfg.comments)
        _plan_comment(rows, asset, uc.full_name, TABLE_COMMENT, desired, source, uc.comment, cfg, applied)

    table_tags, collisions = derive_tags(asset, cfg.tags)
    budget = {
        "object": cfg.tags.max_tags_per_object - len(uc.tags),
        "table_columns": cfg.tags.max_column_tags_per_table - sum(len(t) for t in uc.column_tags.values()),
    }
    _plan_tags(rows, asset, uc.full_name, TABLE_TAG, table_tags, collisions, uc.tags, budget, cfg, governed, applied)

    for col in asset.get("columns", []):
        uc_col = uc.columns.get((col["name"] or "").lower())
        if uc_col is None:
            rows.append(_row(asset, uc.full_name, NO_MATCH, "SKIPPED", "COLUMN_NOT_IN_UC", col["name"]))
            continue
        name = uc_col["name"]
        if cfg.comments.column_comments:
            desired, source = pick_description(col, cfg.comments)
            _plan_comment(rows, asset, uc.full_name, COLUMN_COMMENT, desired, source, uc_col.get("comment"), cfg, applied, name)
        col_current = uc.column_tags.get(name.lower(), {})
        col_tags, col_collisions = derive_tags(col, cfg.tags)
        col_budget = {"object": cfg.tags.max_tags_per_object - len(col_current), "table_columns": budget["table_columns"]}
        _plan_tags(rows, asset, uc.full_name, COLUMN_TAG, col_tags, col_collisions, col_current, col_budget, cfg, governed, applied, name)
        budget["table_columns"] = col_budget["table_columns"]
    return rows


def build_plan(match: MatchResult, uc_state: dict[str, UcTable], cfg: Config,
               governed: dict[str, set[str]] | None = None, applied: dict | None = None) -> list[dict]:
    rows: list[dict] = []
    for asset, uc_name, _how in sorted(match.matched, key=lambda m: m[1]):
        rows.extend(plan_table(asset, uc_state[uc_name], cfg, governed, applied))
    for asset, reason, target in match.purview_unmatched:
        rows.append(_row(asset, target, NO_MATCH, "SKIPPED", reason))
    for asset, target in match.ambiguous:
        rows.append(_row(asset, target, NO_MATCH, "SKIPPED", "AMBIGUOUS_MULTIPLE_PURVIEW_ASSETS"))
    for uc_name in match.uc_unmatched:
        rows.append(_row(None, uc_name, NO_MATCH, "SKIPPED", "UC_TABLE_WITHOUT_PURVIEW_ASSET"))
    for i, r in enumerate(rows):
        r["action_id"] = i
    attach_statements(rows, uc_state)
    return rows


# -------------------------------------------------------------------- statements
def build_statements(rows: list[dict], uc_state: dict[str, UcTable]) -> "OrderedDict[str, list[tuple[str, list[int]]]]":
    """PLANNED rows -> {table: [(sql, [action_ids]), ...]} in a fixed order per table:
    table comment, column comments (one statement), table tags, column tags (one per column)."""
    by_table: OrderedDict[str, list[dict]] = OrderedDict()
    for r in rows:
        if r["status"] == "PLANNED" and r["action"] in (TABLE_COMMENT, COLUMN_COMMENT, TABLE_TAG, COLUMN_TAG):
            by_table.setdefault(r["uc_object"], []).append(r)

    out: OrderedDict[str, list[tuple[str, list[int]]]] = OrderedDict()
    for table, trows in by_table.items():
        table_type = uc_state[table].table_type
        stmts: list[tuple[str, list[int]]] = []
        for r in trows:
            if r["action"] == TABLE_COMMENT:
                stmts.append((sql.comment_on_table(table, r["desired_value"]), [r["action_id"]]))
        col_rows = {r["column_name"]: r for r in trows if r["action"] == COLUMN_COMMENT}
        comments = {col: r["desired_value"] for col, r in col_rows.items()}
        for stmt, cols in sql.column_comments(table, table_type, comments):
            stmts.append((stmt, [col_rows[c]["action_id"] for c in cols]))
        tag_rows = [r for r in trows if r["action"] == TABLE_TAG]
        if tag_rows:
            stmts.append((sql.set_tags(table, {r["tag_key"]: r["desired_value"] for r in tag_rows}), [r["action_id"] for r in tag_rows]))
        per_column: OrderedDict[str, list[dict]] = OrderedDict()
        for r in trows:
            if r["action"] == COLUMN_TAG:
                per_column.setdefault(r["column_name"], []).append(r)
        for column, crow in per_column.items():
            stmts.append((sql.set_tags(table, {r["tag_key"]: r["desired_value"] for r in crow}, column), [r["action_id"] for r in crow]))
        out[table] = stmts
    return out


def attach_statements(rows: list[dict], uc_state: dict[str, UcTable]) -> None:
    by_id = {r["action_id"]: r for r in rows}
    for stmts in build_statements(rows, uc_state).values():
        for stmt, ids in stmts:
            for i in ids:
                by_id[i]["sql_statement"] = stmt


# -------------------------------------------------------------------- apply
def current_value(row: dict, uc: UcTable | None):
    if uc is None:
        return "__TABLE_DROPPED__"
    action, col = row["action"], (row["column_name"] or "").lower()
    if action == TABLE_COMMENT:
        return normalize_comment(uc.comment) or None
    if action == COLUMN_COMMENT:
        c = uc.columns.get(col)
        return (normalize_comment(c.get("comment")) or None) if c else "__COLUMN_DROPPED__"
    if action == TABLE_TAG:
        return uc.tags.get(row["tag_key"])
    if action == COLUMN_TAG:
        return uc.column_tags.get(col, {}).get(row["tag_key"])
    return None


def check_drift(planned: list[dict], uc_state: dict[str, UcTable]) -> list[dict]:
    """Re-validate a saved plan against the UC state right before applying it.
    Never apply on top of a change someone made after the plan was reviewed."""
    rows = []
    for r in planned:
        r = dict(r)
        now = current_value(r, uc_state.get(r["uc_object"]))
        if now == r["desired_value"]:
            r["status"], r["reason"] = "SKIPPED", "ALREADY_APPLIED"
        elif now != r["current_value"]:
            r["status"], r["reason"] = "DRIFTED", f"UC_CHANGED_SINCE_PLAN (agora: {now!r})"
        rows.append(r)
    return rows
