# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Inventário: Purview × Unity Catalog
# MAGIC
# MAGIC Rode **antes** de configurar a migração. Somente leitura: nada é alterado no Purview nem no UC.
# MAGIC
# MAGIC | Seção | Para que serve |
# MAGIC |---|---|
# MAGIC | 1. Tipos de asset no Purview | Descobrir quais `entity_types` colocar no `config.yaml` (ex.: o cliente tem Synapse serverless? ADLS?) |
# MAGIC | 2. Prévia do de-para | Validar as regras `matching.rules` antes de gerar um plano |
# MAGIC | 3. Tabelas só no UC | Ver o que foi migrado mas não tem documentação no Purview |

# COMMAND ----------

# MAGIC %pip install -q -r ../requirements.txt

# COMMAND ----------

dbutils.widgets.text("config_path", "../config/config.yaml", "Arquivo de configuração")

# COMMAND ----------

import os
import sys

sys.path.insert(0, os.path.abspath(".."))

from purview2uc.auth import get_token_provider
from purview2uc.config import load_config
from purview2uc.matching import resolve_target
from purview2uc.purview import PurviewClient
from purview2uc.uc import existing_catalogs, read_uc_state

cfg = load_config(dbutils.widgets.get("config_path"))
purview = PurviewClient(cfg.purview.endpoint, get_token_provider(cfg.auth, dbutils))
print(f"Purview: {cfg.purview.endpoint} | autenticação: {cfg.auth.method}")

# COMMAND ----------

# MAGIC %md ## 1. Tipos de asset no Purview

# COMMAND ----------

counts = purview.entity_type_counts()
display(
    spark.createDataFrame(
        [(t, n, t in cfg.purview.entity_types) for t, n in counts],
        "entity_type STRING, assets LONG, configurado_para_migrar BOOLEAN",
    )
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Prévia do de-para (matching)
# MAGIC `uc_target` é o nome que as regras calculam; `existe_no_uc` mostra se a tabela foi encontrada.
# MAGIC Linhas com `NO_MATCHING_RULE` pedem uma regra nova ou uma linha no `table_overrides.csv`.

# COMMAND ----------

hits = purview.search(cfg.purview.entity_types, cfg.purview.collection_ids)
preview = []
for h in hits:
    target, how = resolve_target(h["qualifiedName"], h["entityType"], cfg.matching)
    preview.append((h["entityType"], h["qualifiedName"], target.lower() if target else None, how))

catalogs_in_scope = {c.lower() for c in cfg.unity_catalog.catalogs}
targets_catalogs = {t.split(".")[0] for _, _, t, _ in preview if t}
catalogs = sorted((targets_catalogs | catalogs_in_scope) & existing_catalogs(spark))
uc_state = read_uc_state(spark, catalogs, cfg.unity_catalog.exclude_schemas)

display(
    spark.createDataFrame(
        [(et, qn, t, how, bool(t) and t in uc_state) for et, qn, t, how in preview],
        "entity_type STRING, purview_qualified_name STRING, uc_target STRING, regra STRING, existe_no_uc BOOLEAN",
    ).orderBy("existe_no_uc", "purview_qualified_name")
)

# COMMAND ----------

# MAGIC %md ## 3. Tabelas do UC sem asset correspondente no Purview

# COMMAND ----------

targeted = {t for _, _, t, _ in preview if t}
only_uc = sorted(name for name in uc_state if name.split(".")[0] in catalogs_in_scope and name not in targeted)
display(spark.createDataFrame([(n, uc_state[n].table_type) for n in only_uc] or [(None, None)], "uc_table STRING, table_type STRING"))
