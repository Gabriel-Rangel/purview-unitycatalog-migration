# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Sincronizar metadados Purview → Unity Catalog
# MAGIC
# MAGIC Funciona em duas etapas, como um `terraform plan` / `apply`:
# MAGIC
# MAGIC 1. **`mode = plan`** (padrão, não altera nada): lê o Purview, casa os assets com as tabelas do UC e grava
# MAGIC    em `<control>.sync_actions` cada mudança proposta, com o SQL que será executado.
# MAGIC 2. **Revise** o plano (consultas no fim do notebook ou SQL direto na tabela).
# MAGIC 3. **`mode = apply`**: executa **o plano salvo** (último, ou o `run_id` informado). Antes de cada ação,
# MAGIC    confere se o UC ainda está como no plano; se alguém mudou algo depois, a ação vira `DRIFTED` e não é aplicada.
# MAGIC
# MAGIC Regras de segurança: tags só são adicionadas (nunca removidas); comentários existentes no UC não são
# MAGIC sobrescritos com `on_conflict: fill_empty`; tudo fica registrado (append-only) em `sync_actions`.

# COMMAND ----------

# MAGIC %pip install -q -r ../requirements.txt

# COMMAND ----------

dbutils.widgets.text("config_path", "../config/config.yaml", "Arquivo de configuração")
dbutils.widgets.dropdown("mode", "plan", ["plan", "apply"], "Modo")
dbutils.widgets.text("run_id", "", "run_id a aplicar (vazio = último plano)")

# COMMAND ----------

import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(".."))

from purview2uc import planner
from purview2uc.auth import get_token_provider
from purview2uc.config import load_config
from purview2uc.matching import match_assets, resolve_target
from purview2uc.purview import PurviewClient, extract_assets
from purview2uc.sql import sql_string
from purview2uc.uc import (
    ensure_control_tables, existing_catalogs, load_applied_values, load_plan, read_tag_policies,
    read_uc_state, run_statements, write_actions, write_snapshot,
)

cfg = load_config(dbutils.widgets.get("config_path"))
mode = dbutils.widgets.get("mode")
ensure_control_tables(spark, cfg)
print(f"modo={mode} | controle={cfg.control.actions_table}")

# COMMAND ----------

# MAGIC %md ## Plan

# COMMAND ----------

if mode == "plan":
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]

    # 1. Purview: busca + leitura via Atlas API (somente leitura)
    purview = PurviewClient(cfg.purview.endpoint, get_token_provider(cfg.auth, dbutils))
    assets = extract_assets(purview, cfg.purview)
    write_snapshot(spark, cfg, run_id, assets)
    print(f"Purview: {len(assets)} assets lidos")

    # 2. Unity Catalog: estado atual dos catálogos envolvidos
    scope = {c.lower() for c in cfg.unity_catalog.catalogs}
    targets = {resolve_target(a["qualified_name"], a["type_name"], cfg.matching)[0] for a in assets}
    wanted = {t.lower().split(".")[0] for t in targets if t} | scope
    available = existing_catalogs(spark)
    for missing in sorted(wanted - available):
        print(f"AVISO: catálogo '{missing}' não existe ou não é visível para este usuário")
    uc_state = read_uc_state(spark, sorted(wanted & available), cfg.unity_catalog.exclude_schemas)
    if not uc_state:
        print("AVISO: nenhuma tabela visível no UC. Confira permissões (USE CATALOG/USE SCHEMA/SELECT ou BROWSE).")

    # 3. Match + diff. O histórico de sync_actions diz quais valores no UC foram gravados por esta
    #    automação: esses podem ser atualizados se o Purview mudar; edições manuais viram CONFLICT.
    match = match_assets(assets, cfg.matching, set(uc_state), {t for t in uc_state if t.split(".")[0] in scope})
    rows = planner.build_plan(match, uc_state, cfg, read_tag_policies(), load_applied_values(spark, cfg))
    write_actions(spark, cfg, run_id, "plan", rows)
    print(f"Plano {run_id}: {sum(r['status'] == 'PLANNED' for r in rows)} ações a aplicar, "
          f"{sum(r['status'] == 'CONFLICT' for r in rows)} conflitos, {len(match.matched)} tabelas casadas")

# COMMAND ----------

# MAGIC %md ## Apply

# COMMAND ----------

if mode == "apply":
    run_id, plan_rows = load_plan(spark, cfg, dbutils.widgets.get("run_id").strip() or None)
    planned = [r for r in plan_rows if r["status"] == "PLANNED"]
    catalogs = sorted({r["uc_object"].split(".")[0] for r in planned})
    uc_state = read_uc_state(spark, catalogs, cfg.unity_catalog.exclude_schemas)

    checked = planner.check_drift(planned, uc_state)
    to_run = [r for r in checked if r["status"] == "PLANNED"]
    planner.attach_statements(to_run, uc_state)  # SQL reconstruído só com o que segue válido
    outcome = run_statements(spark, planner.build_statements(to_run, uc_state), cfg.execution.max_workers)
    for r in to_run:
        r["error"] = outcome.get(r["action_id"])
        r["status"] = "FAILED" if r["error"] else "APPLIED"
    write_actions(spark, cfg, run_id, "apply", checked)
    print(f"Apply {run_id}: {sum(r['status'] == 'APPLIED' for r in checked)} aplicadas, "
          f"{sum(r['status'] == 'FAILED' for r in checked)} falhas, {sum(r['status'] == 'DRIFTED' for r in checked)} drift, "
          f"{sum(r['status'] == 'SKIPPED' for r in checked)} já aplicadas")

# COMMAND ----------

# MAGIC %md ## Resultado desta execução

# COMMAND ----------

current = f"run_id = {sql_string(run_id)} AND mode = {sql_string(mode)}"
display(spark.sql(f"""
  SELECT action, status, reason, count(*) AS acoes
  FROM {cfg.control.actions_table} WHERE {current}
  GROUP BY ALL ORDER BY status, action
"""))

# COMMAND ----------

# MAGIC %md ### Mudanças (o que será / foi executado)

# COMMAND ----------

display(spark.sql(f"""
  SELECT uc_object, column_name, action, tag_key, current_value, desired_value, status, error, source, sql_statement
  FROM {cfg.control.actions_table}
  WHERE {current} AND action LIKE 'SET_%' AND status IN ('PLANNED', 'APPLIED', 'FAILED', 'DRIFTED')
  ORDER BY uc_object, action, column_name, tag_key
"""))

# COMMAND ----------

# MAGIC %md ### Conflitos e itens ignorados (exigem decisão humana)

# COMMAND ----------

display(spark.sql(f"""
  SELECT uc_object, column_name, action, tag_key, current_value, desired_value, status, reason, purview_qualified_name
  FROM {cfg.control.actions_table}
  WHERE {current} AND status IN ('CONFLICT', 'SKIPPED')
  ORDER BY status, reason, uc_object, column_name
"""))
