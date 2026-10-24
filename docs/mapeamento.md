# Mapeamento: de-para de tabelas, comentários e tags

Referência das opções de [config/config.yaml](../config/config.yaml).

## 1. Quais assets do Purview entram

```yaml
purview:
  entity_types: [azure_synapse_dedicated_sql_table, azure_synapse_dedicated_sql_view, azure_synapse_serverless_sql_table, azure_sql_dw_table]
  collection_ids: []                         # vazio = tudo que a identidade enxerga
  include_propagated_classifications: false  # classificações herdadas via lineage são ignoradas por padrão
```

O notebook `00_inventario` lista os tipos existentes no Purview do cliente e quantos assets há de cada um.

Formatos de `qualifiedName` mais comuns:

| Tipo | qualifiedName |
|---|---|
| Synapse dedicated (tabela/view) | `mssql://<ws>.sql.azuresynapse.net/<pool>/<schema>/<tabela>` |
| Synapse serverless | `mssql://<ws>-ondemand.sql.azuresynapse.net/<db>/<schema>/<tabela>` |
| Dedicated pool "standalone" (antigo SQL DW) | `mssql://<servidor>.database.windows.net/<db>/<schema>/<tabela>` |
| Coluna | `<qualifiedName da tabela>#<coluna>` |

## 2. De-para de tabelas

```yaml
matching:
  rules:
    - entity_types: [azure_synapse_dedicated_sql_table]          # opcional: restringe a regra a estes tipos
      purview_qn_regex: '^mssql://(?P<ws>[^/]+)\.sql\.azuresynapse\.net/(?P<db>[^/]+)/(?P<schema>[^/]+)/(?P<table>[^/]+)$'
      uc_target: '{db}.{schema}.{table}'                          # usa os grupos nomeados da regex
      maps:                                                       # traduções por grupo (origem case-insensitive)
        db: {sqlpool01: prod}
        schema: {dbo: core}
      normalize: lower                                            # lower | none
  overrides_csv: table_overrides.csv
```

- **Ordem de avaliação:** primeiro o CSV de overrides, depois as regras, na ordem em que aparecem. Vale a primeira que casar.
- **CSV:** colunas `purview_qualified_name,uc_full_name`. Deixe `uc_full_name` vazio para **ignorar** o asset.
- **Colunas:** casadas pelo nome, sem diferenciar maiúsculas. O nome gravado é o do UC.

Situações reportadas no plano (`action = NO_MATCH`):

| `reason` | Significado | O que fazer |
|---|---|---|
| `NO_MATCHING_RULE` | Nenhuma regra/override casou com o qualifiedName | Nova regra ou linha no CSV |
| `UC_TABLE_NOT_FOUND` | A regra calculou um nome que não existe no UC | Tabela não migrada, ou regra/map errado |
| `AMBIGUOUS_MULTIPLE_PURVIEW_ASSETS` | Dois assets apontam para a mesma tabela | Use o CSV para decidir (ignore um deles) |
| `UC_TABLE_WITHOUT_PURVIEW_ASSET` | Tabela do UC (em `unity_catalog.catalogs`) sem documentação no Purview | Informativo |
| `COLUMN_NOT_IN_UC` | Coluna documentada no Purview não existe na tabela do UC | Coluna renomeada ou removida na migração |

## 3. Comentários

```yaml
comments:
  source_priority: [userDescription, description]  # curada no portal > vinda do scan
  on_conflict: fill_empty                          # fill_empty | overwrite
```

- **HTML do editor do Purview:** é convertido para markdown, que o Catalog Explorer renderiza. `<strong>` vira `**negrito**`, listas viram `- item`.
- **Espaços e quebras de linha:** são normalizados, para que a comparação com o UC seja estável.
- **`fill_empty`:** se o UC já tem um comentário diferente, a ação vira `CONFLICT`. A exceção é quando esse comentário foi gravado por esta automação; aí ele é atualizado, com `reason = PURVIEW_UPDATED`.

## 4. Tags

```yaml
tags:
  classifications: true
  terms: true
  labels: true
  managed_attributes: false
  include: ["*"]
  exclude: []                       # ex.: ["MICROSOFT.PERSONAL.AGE"] (falsos positivos do auto-scan)
  aliases_preset: databricks_class  # null para desligar
  aliases:                          # sobrepõe/estende o preset; uma tag ou lista de tags
    CONFIDENCIAL.SALARIO:
      - {key: class.compensation, value: ""}
      - {key: sensibilidade, value: restrita}
  on_conflict: skip                 # skip | overwrite
```

### Tags geradas

As tags usam o **nome do Purview, sem prefixo**:

| Origem | Tag no UC (chave, valor vazio) | Exemplo |
|---|---|---|
| Classificação nativa (com preset) | governed tag `class.*` | `MICROSOFT.PERSONAL.EMAIL` → `class.email_address` |
| Classificação sem alias (customizada etc.) | nome da classificação | `LGPD.DADOS_PESSOAIS` → `LGPD_DADOS_PESSOAIS` |
| Termo de glossário | nome do termo | `Receita Líquida` → `Receita Líquida` |
| Label | nome do label | `camada_gold` → `camada_gold` |
| Managed attribute (se ligado) | `<grupo>_<atributo>` = valor | `Negocio_Dono = 'Vendas'` |

- **O que muda no nome:** só o que o UC rejeita. `.` e `=` viram `_`, e os espaços nas pontas são removidos. Maiúsculas, acentos, espaços, `-` e `/` são mantidos. Chaves com mais de 255 caracteres são truncadas e recebem um hash como sufixo.
- **Origem:** o nome exato no Purview (ex.: `term:Receita Líquida@Glossary`) fica registrado na coluna `source` de `sync_actions`, para auditoria.
- **Mesmo nome, origens diferentes:** um termo e um label com o mesmo nome viram uma única tag.

### Preset `databricks_class`

Mapeia classificações nativas da Microsoft para as governed tags de sistema do **Databricks Data Classification**
(chave sem valor). Equivalências incluídas (lista completa em [purview2uc/presets.py](../purview2uc/presets.py)):

| Purview | Databricks |
|---|---|
| `MICROSOFT.PERSONAL.EMAIL` | `class.email_address` |
| `MICROSOFT.PERSONAL.NAME` | `class.name` |
| `MICROSOFT.PERSONAL.DATE_OF_BIRTH` | `class.date_of_birth` |
| `MICROSOFT.PERSONAL.PHYSICALADDRESS` | `class.street_address` |
| `MICROSOFT.PERSONAL.*PHONE_NUMBER` | `class.phone_number` |
| `MICROSOFT.PERSONAL.IPADDRESS` | `class.ip_address` |
| `MICROSOFT.FINANCIAL.CREDIT_CARD_NUMBER` | `class.credit_card` |
| `MICROSOFT.FINANCIAL.INTERNATIONAL.BANK_ACCOUNT_NUMBER` | `class.iban_code` |
| `MICROSOFT.GOVERNMENT.BRAZIL.CPF_NUMBER` | `class.br_cpf` |
| `MICROSOFT.GOVERNMENT.BRAZIL.NATIONAL_ID_CARD` | `class.br_rg` |
| `MICROSOFT.GOVERNMENT.US.SOCIAL_SECURITY_NUMBER` | `class.us_ssn` |

- **Sem equivalência clara:** a classificação recebe o próprio nome (com `.` → `_`). Mapeie você mesmo em `aliases`, por exemplo uma classificação customizada de salário → `class.compensation`.
- **Taxonomia própria:** se a empresa tem governed tags próprias (ex.: `pii` com valores `EMAIL`/`SSN`), use `aliases` para gerar as duas tags, por exemplo `[{key: class.email_address}, {key: pii, value: EMAIL}]`.

### Validações feitas já no plano (`status = SKIPPED`)

| `reason` | Causa |
|---|---|
| `GOVERNED_TAG_VALUE_NOT_ALLOWED` | Governed tag com lista de valores e o valor não está na lista |
| `GOVERNED_TAG_IS_KEY_ONLY` | Governed tag sem lista de valores (ex.: `class.*`) recebendo valor |
| `INVALID_TAG_KEY` | Chave com `.` ou `=` que não é uma governed tag existente (só acontece via `aliases`) |
| `TAG_LIMIT_REACHED` | Excederia 50 tags no objeto ou 1000 tags de coluna na tabela |
| `KEY_COLLISION …` | Duas origens gerariam a mesma chave com valores diferentes (mantém a primeira) |

Tags pré-existentes no UC com outro valor: `CONFLICT` (`on_conflict: skip`), exceto se o valor foi gravado por esta
automação, caso em que é atualizado (`PURVIEW_UPDATED`).
