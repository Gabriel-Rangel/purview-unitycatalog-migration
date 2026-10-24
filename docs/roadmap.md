# Roadmap

A Fase 1 foi desenhada para receber as próximas sem refatoração:
- **Tipos de asset:** são configuráveis (`purview.entity_types`).
- **Origens de tag:** são plugáveis (`transform.derive_tags`).
- **Tabela `sync_actions`:** já serve de base para dashboard e fluxo de aprovação.

## A. Da documentação ao controle de acesso *(maior valor)*

- **Políticas ABAC sobre as tags migradas.**
  - **O que muda:** no Purview a classificação só *documenta*; no UC ela passa a *aplicar* o controle.
  - **Como:** com o preset `databricks_class`, as colunas já chegam com `class.*`. O próximo passo é criar column masks e row filters por tag (`has_tag('class.br_cpf')`), que valem para toda coluna classificada, inclusive as futuras.
- **Reconciliação com o Data Classification do Databricks.** Rodar a classificação automática do UC nas tabelas migradas e comparar com o que veio do Purview. O relatório aponta:
  - colunas PII que o Purview não marcou;
  - falsos positivos herdados.

## B. IA para fechar lacunas

- **Matching assistido.**
  - **Problema:** tabelas e colunas renomeadas na migração hoje aparecem como `UC_TABLE_NOT_FOUND` / `COLUMN_NOT_IN_UC`.
  - **Proposta:** sugerir linhas para o `table_overrides.csv` (e um futuro de-para de colunas) por similaridade de nomes e do conjunto de colunas.
  - **Fonte alternativa:** reaproveitar o de-para do **Lakebridge**, se o cliente usá-lo para converter o código Synapse.
- **Descrições para o que o Purview não documentou.** Propor comentários com os comentários gerados por IA do UC ou com `ai_query`. Entram no plano como `PLANNED` com `source = AI`, para um steward aprovar.

## C. Mais cobertura de metadados

- **ADLS Gen2 resource sets** (`azure_datalake_gen2_resource_set`): casar pelo caminho de storage com tabelas EXTERNAL e Volumes do UC. Exige um matcher por caminho; `00_inventario` mostra se o cliente tem esses assets.
- **Contatos (Owner/Expert) do Purview:** levar para o owner do UC ou para tags `data_steward`. Exige mapear usuários do Entra para principals do Databricks.
- **Managed attributes e sensitivity labels (MIP):** a derivação já existe (`managed_attributes: true`); falta validar com dados reais do cliente.
- **Glossário do Unified Catalog (portal novo do Purview):** outra API. Os termos podem virar uma tabela de referência no UC mais tags.
- **Classificações em nível de schema/database:** levar para tags de schema/catálogo.

## D. Operação e escala

- **Agendamento:** Lakeflow Job (e Asset Bundle) rodando o mesmo notebook, por exemplo `plan` diário, com `apply` após aprovação.
- **Sync incremental:** filtrar por `updateTime` desde a última execução. Em tempo quase real, usar as notificações Atlas no Event Hub do Purview.
- **Dashboard AI/BI sobre `sync_actions`:** % de tabelas e colunas documentadas, PII tagueado, conflitos pendentes. Vira KPI da migração.
- **Write-back no Purview:** marcar o asset de origem como "migrado → `catalog.schema.table`" para orientar os stewards.
- **Tags via REST `entity-tag-assignments`:** alternativa ao SQL, sem escaping e com menos statements.
- **CI:** `pytest` a cada mudança e suporte a vários metastores/workspaces.
