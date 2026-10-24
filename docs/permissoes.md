# Permissões e configuração inicial

Princípio: **a automação só lê o Purview** e só escreve metadados (comentários e tags) no UC.

## Visão geral

| Onde | Quem | Permissão | Para quê |
|---|---|---|---|
| Purview (collection) | Managed identity do Access Connector | **Data Reader** | Ler assets, classificações e termos |
| Unity Catalog | Quem roda o notebook | `ACCESS` na service credential | Obter o token do Purview |
| Unity Catalog | Quem roda o notebook | `USE CATALOG` / `USE SCHEMA` + `SELECT` ou `BROWSE` | Ler o estado atual (`information_schema`) |
| Unity Catalog | Quem roda o notebook | `MODIFY` na tabela (ou ser owner); em **views**, ser owner | `COMMENT ON` |
| Unity Catalog | Quem roda o notebook | `APPLY TAG` (ou owner) | `SET TAGS` |
| Unity Catalog | Quem roda o notebook | `ASSIGN` na tag policy | Governed tags (ex.: `class.*`) |
| Unity Catalog | Quem roda o notebook | `USE CATALOG` + `CREATE SCHEMA` no catálogo de controle | Tabelas `sync_actions` / `purview_snapshot` |

> `MODIFY` também permite alterar **dados**. Conceda a um service principal dedicado apenas durante a janela de
> migração e revogue depois.

## 1. Access Connector dedicado

Use um connector exclusivo para o Purview: quem tem `ACCESS` à credencial pode usar a identidade em qualquer serviço
Azure em que ela tenha permissão.

```bash
az rest --method put \
  --url "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Databricks/accessConnectors/p2uc-purview-connector?api-version=2024-05-01" \
  --body '{"location":"<regiao>","identity":{"type":"SystemAssigned"},"properties":{}}'

# object id (principalId) da managed identity, usado no passo 3
az resource show -g <rg> -n p2uc-purview-connector --resource-type Microsoft.Databricks/accessConnectors --query identity.principalId -o tsv
```

## 2. Service credential no Unity Catalog

Exige o privilégio `CREATE SERVICE CREDENTIAL` no metastore (o admin do metastore já tem).

```bash
databricks credentials create-credential --json '{
  "name": "purview-reader",
  "purpose": "SERVICE",
  "comment": "Leitura do Purview para a migração de metadados",
  "azure_managed_identity": {"access_connector_id": "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Databricks/accessConnectors/p2uc-purview-connector"}
}'
```

```sql
GRANT ACCESS ON SERVICE CREDENTIAL `purview-reader` TO `grupo-migracao`;
```

> Em notebooks serverless o nome da credencial precisa ser informado explicitamente (`auth.service_credential`).
> O provider retornado implementa a interface `azure.core.credentials.TokenCredential`, por isso o `requirements.txt` inclui `azure-core`.

## 3. Data Reader no Purview

**Pelo portal:** *Data Map → Collections → (collection) → Role assignments → Data readers →* adicione a managed identity
`p2uc-purview-connector`.

**Por script** (metadata policy API; requer Collection Admin):

```bash
python3 demo/grant_purview_reader.py --account <conta> --principal-id <principalId> [--collection <nome-técnico>]
```

Permissões são herdadas pelas sub-collections. Para migrar só parte do Purview, conceda nas collections certas e/ou
use `purview.collection_ids` no config.

## 4. Privilégios no UC (exemplo)

```sql
GRANT USE CATALOG, USE SCHEMA, SELECT, MODIFY, APPLY TAG ON CATALOG prod TO `grupo-migracao`;
GRANT USE CATALOG, CREATE SCHEMA ON CATALOG prod TO `grupo-migracao`;  -- catálogo de controle
-- governed tags: na página da tag policy (Catalog > Governed tags), conceda ASSIGN ao grupo
```

## 5. Rede

O notebook (serverless) chama `https://<conta>.purview.azure.com`.

- **Purview com acesso público:** nada a fazer.
- **Purview com private endpoint / acesso público desativado:** use um cluster clássico em VNet injetada que alcance o private endpoint. Alternativa: configure private endpoints a partir do serverless via *Network Connectivity Configuration (NCC)*.
