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

## C. Glossário de negócio → Pages do Unity Catalog

**Hoje:** a Fase 1 migra só o **vínculo termo → dado**. Cada termo atribuído a uma tabela ou coluna vira uma tag com o nome
exato do termo, e a origem (`Termo@Glossário`) fica em `sync_actions.source`. **O glossário em si não é migrado:**
definições, status, sinônimos, hierarquia, experts e termos sem asset atribuído.

**Destino:** **Pages** (*Discover → Pages*), o glossário nativo do Unity Catalog semantics. Em out/2026 está em **Beta**
e precisa ser habilitado pelo account admin em *Account console → Previews*. Complementam as Pages:
- **governed tags:** o termo aplicado ao dado, inclusive em coluna;
- **metric views:** termos que são KPIs;
- **domínios do Discover:** organização por área de negócio.

| Purview (glossário) | Databricks |
|---|---|
| Glossário / governance domain | Domínio ou subdomínio do Discover |
| Termo: nome e definição | Page: título, descrição e corpo (texto rico) |
| Sinônimos e acrônimos | Sinônimos da Page |
| Status Draft / Approved / Expired | Rascunho / publicado / despublicado |
| Experts e stewards | Owner da Page mais curadores do domínio (`MANAGE`). Os demais experts entram no corpo |
| Hierarquia e termos relacionados | Related assets (Pages pai/filho) |
| Termo atribuído a uma tabela | Tabela como related asset da Page, mais a tag (já feito na Fase 1) |
| Termo atribuído a uma coluna | Tag na coluna (já feito na Fase 1). As Pages linkam assets no nível da Page |
| Termo que é um KPI (ex.: Receita Líquida) | Metric view com o cálculo oficial, ligada à Page |
| Atributos customizados de termo | Seção no corpo da Page |

**Como implementar, no mesmo espírito plan → revisão → apply:**
1. **Extrair** o glossário pela API do Purview. No glossário clássico: `/glossary/{guid}/terms`, com definição, status, sinônimos, termos relacionados, hierarquia e contatos. Termos do Unified Catalog (portal novo) usam outra API.
2. **Gerar** o arquivo de importação e um relatório do de-para: domínio, Page, sinônimos e assets relacionados.
3. **Importar como rascunho.** A documentação não descreve API pública para criar Pages; o caminho de carga em massa documentado é o *bulk import* via Genie Code, que cria as Pages como **rascunhos**.
4. **Revisar e publicar:** o curador do domínio aprova. Pages publicadas passam a ser contexto do Genie.
5. **Automatizar** o passo 3 quando houver API, como já é feito com comentários e tags.

**Pontos de atenção:**
- **Dados das Pages:** ficam no nível da conta e podem ser replicadas globalmente. Não suportam chave gerenciada pelo cliente (CMK), e a documentação pede para não colocar dados sensíveis em nome, descrição, sinônimos ou corpo.
- **Promover termos a governed tags:** as tags de termo da Fase 1 não têm valor, então já são compatíveis com governed tags sem lista de valores. Ressalva: governed tags aceitam menos caracteres nas chaves (ex.: `%`, `/`), e o planner sinaliza os casos.
- **Enquanto o Beta não estiver habilitado:** use uma tabela Delta de glossário (termos, definições, sinônimos, relações) como fonte consultável e contexto do Genie.

## D. Mais cobertura de metadados

- **ADLS Gen2 resource sets** (`azure_datalake_gen2_resource_set`): casar pelo caminho de storage com tabelas EXTERNAL e Volumes do UC. Exige um matcher por caminho; `00_inventario` mostra se o cliente tem esses assets.
- **Contatos (Owner/Expert) do Purview:** levar para o owner do UC ou para tags `data_steward`. Exige mapear usuários do Entra para principals do Databricks.
- **Managed attributes e sensitivity labels (MIP):** a derivação já existe (`managed_attributes: true`); falta validar com dados reais do cliente.
- **Classificações em nível de schema/database:** levar para tags de schema/catálogo.

## E. Operação e escala

- **Agendamento:** Lakeflow Job (e Asset Bundle) rodando o mesmo notebook, por exemplo `plan` diário, com `apply` após aprovação.
- **Sync incremental:** filtrar por `updateTime` desde a última execução. Em tempo quase real, usar as notificações Atlas no Event Hub do Purview.
- **Dashboard AI/BI sobre `sync_actions`:** % de tabelas e colunas documentadas, PII tagueado, conflitos pendentes. Vira KPI da migração.
- **Write-back no Purview:** marcar o asset de origem como "migrado → `catalog.schema.table`" para orientar os stewards.
- **Tags via REST `entity-tag-assignments`:** alternativa ao SQL, sem escaping e com menos statements.
- **CI:** `pytest` a cada mudança e suporte a vários metastores/workspaces.
