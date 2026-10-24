"""Safe SQL builders for Unity Catalog comments and tags.

Databricks SQL treats 'It''s' as two adjacent literals ('It' 's' -> "Its"), so quotes are
escaped with a backslash instead. Backslashes are escaped first so that text such as
"ERP\\SAP" or "\\n" written by a steward survives unchanged.
"""

from __future__ import annotations

VIEW_TYPES = {"VIEW", "METRIC_VIEW"}


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def quote_full_name(full_name: str) -> str:
    """catalog.schema.table -> `catalog`.`schema`.`table` (names from information_schema have no dots)."""
    parts = full_name.split(".")
    if len(parts) != 3:
        raise ValueError(f"Nome UC inválido (esperado catalog.schema.table): {full_name!r}")
    return ".".join(quote_ident(p) for p in parts)


def comment_on_table(full_name: str, comment: str) -> str:
    # COMMENT ON TABLE também aceita views.
    return f"COMMENT ON TABLE {quote_full_name(full_name)} IS {sql_string(comment)}"


def column_comments(full_name: str, table_type: str, comments: dict[str, str]) -> list[tuple[str, list[str]]]:
    """[(statement, columns it changes)]. Tables: one ALTER TABLE for all columns (a single Delta
    commit). Views don't support ALTER COLUMN ... COMMENT, so one COMMENT ON COLUMN per column."""
    if not comments:
        return []
    target = quote_full_name(full_name)
    if table_type in VIEW_TYPES:
        return [
            (f"COMMENT ON COLUMN {target}.{quote_ident(col)} IS {sql_string(text)}", [col])
            for col, text in comments.items()
        ]
    clauses = ", ".join(f"{quote_ident(col)} COMMENT {sql_string(text)}" for col, text in comments.items())
    return [(f"ALTER TABLE {target} ALTER COLUMN {clauses}", list(comments))]


def set_tags(full_name: str, tags: dict[str, str], column: str | None = None) -> str:
    # ALTER TABLE também funciona para views (ALTER VIEW ... ALTER COLUMN não existe).
    pairs = ", ".join(f"{sql_string(k)} = {sql_string(v)}" for k, v in tags.items())
    column_clause = f" ALTER COLUMN {quote_ident(column)}" if column else ""
    return f"ALTER TABLE {quote_full_name(full_name)}{column_clause} SET TAGS ({pairs})"
