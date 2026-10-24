"""Unity Catalog side (needs a SparkSession): read current state, run statements, control tables.

spark.sql in the notebook instead of a SQL warehouse: service credentials do not work on SQL
warehouses, and this way there is no extra compute to manage.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import sql
from .config import Config
from .planner import UcTable

log = logging.getLogger(__name__)

ACTION_COLUMNS = [
    ("run_id", "STRING"),
    ("mode", "STRING"),
    ("created_at", "TIMESTAMP"),
    ("action_id", "INT"),
    ("purview_guid", "STRING"),
    ("purview_type", "STRING"),
    ("purview_qualified_name", "STRING"),
    ("uc_object", "STRING"),
    ("column_name", "STRING"),
    ("action", "STRING"),
    ("tag_key", "STRING"),
    ("current_value", "STRING"),
    ("desired_value", "STRING"),
    ("source", "STRING"),
    ("status", "STRING"),
    ("reason", "STRING"),
    ("sql_statement", "STRING"),
    ("error", "STRING"),
]
ACTIONS_DDL = ", ".join(f"{n} {t}" for n, t in ACTION_COLUMNS)
SNAPSHOT_DDL = "run_id STRING, extracted_at TIMESTAMP, purview_guid STRING, qualified_name STRING, type_name STRING, asset_json STRING"


def existing_catalogs(spark) -> set[str]:
    return {r[0].lower() for r in spark.sql("SHOW CATALOGS").collect()}


def read_uc_state(spark, catalogs: list[str], exclude_schemas: list[str]) -> dict[str, UcTable]:
    """One pass over <catalog>.information_schema. It only shows what the running principal can see."""
    state: dict[str, UcTable] = {}
    excluded = ", ".join(sql.sql_string(s.lower()) for s in exclude_schemas) or "''"
    for cat in catalogs:
        c = sql.quote_ident(cat)
        where = f"lower(table_schema) NOT IN ({excluded})"
        for r in spark.sql(f"SELECT table_catalog, table_schema, table_name, table_type, comment FROM {c}.information_schema.tables WHERE {where}").collect():
            full = f"{r.table_catalog}.{r.table_schema}.{r.table_name}".lower()
            state[full] = UcTable(full, r.table_type, r.comment or "")
        for r in spark.sql(f"SELECT table_catalog, table_schema, table_name, column_name, comment FROM {c}.information_schema.columns WHERE {where}").collect():
            t = state.get(f"{r.table_catalog}.{r.table_schema}.{r.table_name}".lower())
            if t:
                t.columns[r.column_name.lower()] = {"name": r.column_name, "comment": r.comment or ""}
        where_tags = f"lower(schema_name) NOT IN ({excluded})"
        for r in spark.sql(f"SELECT catalog_name, schema_name, table_name, tag_name, tag_value FROM {c}.information_schema.table_tags WHERE {where_tags}").collect():
            t = state.get(f"{r.catalog_name}.{r.schema_name}.{r.table_name}".lower())
            if t:
                t.tags[r.tag_name] = r.tag_value or ""
        for r in spark.sql(f"SELECT catalog_name, schema_name, table_name, column_name, tag_name, tag_value FROM {c}.information_schema.column_tags WHERE {where_tags}").collect():
            t = state.get(f"{r.catalog_name}.{r.schema_name}.{r.table_name}".lower())
            if t:
                t.column_tags.setdefault(r.column_name.lower(), {})[r.tag_name] = r.tag_value or ""
    return state


def read_tag_policies(workspace_client=None) -> dict[str, set[str]] | None:
    """Governed tags of the account: {tag_key: allowed values} (empty set = key-only).
    Returns None if they can't be read — the plan then skips this validation."""
    try:
        if workspace_client is None:
            from databricks.sdk import WorkspaceClient

            workspace_client = WorkspaceClient()
        policies, token = {}, None
        while True:
            query = {"page_size": 1000, **({"page_token": token} if token else {})}
            page = workspace_client.api_client.do("GET", "/api/2.1/tag-policies", query=query)
            for p in page.get("tag_policies", []):
                policies[p["tag_key"]] = {v["name"] for v in p.get("values", [])}
            token = page.get("next_page_token")
            if not token:
                return policies
    except Exception as e:  # noqa: BLE001
        log.warning("Não foi possível ler as tag policies (governed tags): %s", e)
        return None


def run_statements(spark, statements_by_table, max_workers: int) -> dict[int, str | None]:
    """Run tables in parallel, statements of the same table sequentially (avoids concurrent
    Delta commits on one table). Returns {action_id: error or None}."""

    def run_table(item):
        table, stmts = item
        outcome = {}
        for stmt, ids in stmts:
            try:
                spark.sql(stmt)
                err = None
            except Exception as e:  # noqa: BLE001 — registramos e seguimos com as outras ações
                err = str(e).split("\n")[0][:1000]
                log.error("Falhou em %s: %s", table, err)
            for i in ids:
                outcome[i] = err
        return outcome

    results: dict[int, str | None] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for outcome in pool.map(run_table, statements_by_table.items()):
            results.update(outcome)
    return results


# -------------------------------------------------------------------- control tables
def ensure_control_tables(spark, cfg: Config) -> None:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {sql.quote_ident(cfg.control.catalog)}.{sql.quote_ident(cfg.control.schema)} "
              "COMMENT 'Controle da migração de metadados Purview -> Unity Catalog'")
    spark.sql(f"CREATE TABLE IF NOT EXISTS {cfg.control.actions_table} ({ACTIONS_DDL}) "
              "COMMENT 'Plano e auditoria (append-only) da sincronização Purview -> UC'")
    spark.sql(f"CREATE TABLE IF NOT EXISTS {cfg.control.snapshot_table} ({SNAPSHOT_DDL}) "
              "COMMENT 'Snapshot bruto dos assets lidos do Purview por execução (confidencial)'")


def write_actions(spark, cfg: Config, run_id: str, mode: str, rows: list[dict]) -> None:
    if not rows:
        return
    meta = {"run_id": run_id, "mode": mode, "created_at": datetime.now(timezone.utc)}
    records = [tuple(meta[n] if n in meta else r.get(n) for n, _ in ACTION_COLUMNS) for r in rows]
    spark.createDataFrame(records, schema=ACTIONS_DDL).write.mode("append").saveAsTable(cfg.control.actions_table.replace("`", ""))


def write_snapshot(spark, cfg: Config, run_id: str, assets: list[dict]) -> None:
    if not assets:
        return
    now = datetime.now(timezone.utc)
    records = [(run_id, now, a["guid"], a["qualified_name"], a["type_name"], json.dumps(a, ensure_ascii=False)) for a in assets]
    spark.createDataFrame(records, schema=SNAPSHOT_DDL).write.mode("append").saveAsTable(cfg.control.snapshot_table.replace("`", ""))


def load_applied_values(spark, cfg: Config) -> dict:
    """Last value this tool successfully wrote per object/column/tag ({applied_key: value}).
    Lets the planner tell "still ours, safe to update" from "edited by someone in UC"."""
    rows = spark.sql(f"""
        SELECT uc_object, lower(coalesce(column_name, '')) AS col, action, coalesce(tag_key, '') AS tag_key, desired_value
        FROM {cfg.control.actions_table}
        WHERE mode = 'apply' AND status = 'APPLIED'
        QUALIFY row_number() OVER (PARTITION BY uc_object, lower(coalesce(column_name, '')), action, coalesce(tag_key, '')
                                   ORDER BY created_at DESC) = 1
    """).collect()
    return {(r.uc_object, r.col, r.action, r.tag_key): r.desired_value for r in rows}


def load_plan(spark, cfg: Config, run_id: str | None) -> tuple[str, list[dict]]:
    table = cfg.control.actions_table
    if not run_id:
        latest = spark.sql(f"SELECT run_id FROM {table} WHERE mode = 'plan' ORDER BY created_at DESC LIMIT 1").collect()
        if not latest:
            raise RuntimeError("Nenhum plano encontrado. Rode o notebook com mode=plan primeiro.")
        run_id = latest[0].run_id
    applied = spark.sql(f"SELECT count(*) AS n FROM {table} WHERE run_id = {sql.sql_string(run_id)} AND mode = 'apply'").collect()[0].n
    if applied:
        raise RuntimeError(f"O plano {run_id} já foi aplicado. Gere um novo plano (mode=plan).")
    rows = spark.sql(f"SELECT * FROM {table} WHERE run_id = {sql.sql_string(run_id)} AND mode = 'plan' ORDER BY action_id").collect()
    if not rows:
        raise RuntimeError(f"run_id {run_id} não encontrado em {table}")
    return run_id, [r.asDict() for r in rows]
