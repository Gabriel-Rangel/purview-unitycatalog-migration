# Registro de decisões

Cada decisão traz o contexto, a escolha, o porquê e o que foi descartado. Use este documento para responder ao
"por que vocês fizeram assim?" de arquitetos e segurança do cliente.

---

## D1. REST direto no Purview, sem SDK

- **Escolha:** `requests` sobre a API do Data Map (`api-version=2023-09-01`).
  - **Busca:** `POST /datamap/api/search/query`.
  - **Leitura:** `GET /datamap/api/atlas/v2/entity/bulk`.
  - **Termos:** `GET /glossary/term/{guid}`.
- **Por quê:**
  - Três chamadas não justificam uma dependência.
  - Cada chamada fica visível no código, o que facilita revisões de segurança.
  - Os endpoints são GA.
- **Descartado:**
  - *PyApacheAtlas*, usado no artigo de referência: é comunitário e sem manutenção ativa.
  - *azure-purview-datamap*: é preview e adiciona dependências.

## D2. A busca só fornece GUIDs; o conteúdo vem da API Atlas

- **Por quê:** o índice de busca não traz `userDescription` e reflete mudanças com minutos de atraso. A API Atlas é a fonte da verdade.
- **Colunas:** são lidas num segundo bulk. As cópias em `referredEntities` vêm incompletas, sem os termos de glossário das colunas.

## D3. Managed identity via service credential do UC

- **Escolha:** um Access Connector dedicado, com papel **Data Reader** no Purview, exposto como service credential (`purview-reader`). O notebook usa `dbutils.credentials.getServiceCredentialsProvider(...)`.
- **Por quê:**
  - Não há secret para guardar, rotacionar ou vazar.
  - Quem pode usar a identidade é controlado por `GRANT ACCESS` no UC, que é auditado.
  - Funciona em serverless.
- **Por que um connector dedicado:** quem tem `ACCESS` a uma credencial pode pedir tokens para *qualquer* serviço em que a identidade tenha permissão. Uma identidade por finalidade limita o raio de impacto.
- **Fallback:** service principal em secret scope (`auth.method: secret_scope`).

## D4. Escrita com `spark.sql` no notebook (serverless), não via SQL warehouse

- **Por quê:**
  - Service credentials não funcionam em SQL warehouse.
  - Fica um único compute para ler o Purview e escrever no UC.
- **Paralelismo:**
  - Tabelas diferentes rodam em paralelo.
  - Os statements de uma mesma tabela rodam em sequência, para evitar commits Delta concorrentes na mesma tabela.
  - Os comentários de todas as colunas de uma tabela vão num único `ALTER TABLE … ALTER COLUMN a COMMENT …, b COMMENT …`, ou seja, um commit.

## D5. Plan → revisão → apply, com tabela de controle append-only

- **Escolha:** `mode=plan` grava as ações em `sync_actions`. `mode=apply` executa **o plano salvo** e grava o resultado como novas linhas.
- **Por quê:**
  - Separa a decisão (humana) da execução.
  - A tabela vira trilha de auditoria da migração: quem, quando, de qual valor para qual.
  - Ser append-only evita `MERGE` e preserva o histórico.
- **Drift:** antes de cada ação, o apply relê o UC. Se o valor atual não é mais o que o plano viu, a ação vira `DRIFTED`. O revisor aprovou uma mudança sobre um estado específico.

## D6. Conservador por padrão, mas reexecutável

- **Comentários:** `on_conflict: fill_empty`. Se o UC já tem um comentário diferente, por exemplo gerado por IA ou escrito por alguém, a ação vira `CONFLICT`.
- **Tags:**
  - São só aditivas; nunca usamos `UNSET TAGS`.
  - Uma chave existente com outro valor vira `CONFLICT`.
- **Atualizações:** se o valor atual no UC é exatamente o último que **esta automação** aplicou (histórico `APPLIED` em `sync_actions`), ninguém editou à mão. Nesse caso a mudança vinda do Purview é propagada (`reason = PURVIEW_UPDATED`).
- **Por quê:** durante a migração, o Purview continua sendo editado pelos stewards. Sem essa regra, cada atualização viraria um conflito eterno; com ela, edições manuais no UC continuam protegidas.

## D7. De-para por regex com grupos nomeados + CSV de exceções

- **Por quê:**
  - É explícito e revisável.
  - Um match heurístico (por nome parecido) que erre copia a classificação PII errada para a tabela errada.
  - Regex com grupos nomeados permite traduzir partes (`maps: db: {sqlpool01: prod}`).
  - O CSV cobre renomeações pontuais e permite ignorar assets.
- **Proteções:**
  - Dois assets do Purview apontando para a mesma tabela viram `AMBIGUOUS` e nenhum é aplicado.
  - Assets sem par e tabelas só no UC são reportados.

## D8. Uma tag por conceito, com preset para as governed tags `class.*`

- **Preset `databricks_class`:** as classificações nativas da Microsoft viram as governed tags de sistema usadas pelo **Databricks Data Classification**, por exemplo `MICROSOFT.PERSONAL.EMAIL → class.email_address` e `MICROSOFT.GOVERNMENT.BRAZIL.CPF_NUMBER → class.br_cpf`.
  - **Por quê:** a coluna migrada fica idêntica a uma classificada pelo próprio Databricks. Políticas ABAC, filtros do Catalog Explorer e relatórios escritos para `class.*` passam a valer para os dados migrados.
- **Classificações sem equivalente, termos de glossário e labels:** viram tags com o **nome exato do Purview**, sem prefixo (`Receita Líquida`, `camada_gold`). Só muda o que o UC rejeita: `.` e `=` viram `_` (`LGPD.DADOS_PESSOAIS` → `LGPD_DADOS_PESSOAIS`).
  - **Por que sem prefixo:** a tag deve ser reconhecível por quem já usa o Purview. A origem não precisa estar no nome: ela fica em `sync_actions.source`.
  - **Colisões:** um termo chamado `pii`, por exemplo, colidiria com uma governed tag existente. Isso é detectado no plano e vira `SKIPPED`.
- **Por que não uma chave única `Purview = <classificação>`, como no artigo:** uma tabela ou coluna aceita uma só tag por chave, então a 2ª classificação sobrescreveria a 1ª. Uma tag por conceito também permite `has_tag(...)` no ABAC.
- **Validação no plano:**
  - Chaves com `.` ou `=` só são aceitas se forem governed tags existentes. Na prática, testado no UC, só esses dois caracteres são rejeitados; espaços, acentos, `-` e `/` são aceitos.
  - Governed tags com lista de valores exigem um valor da lista; as sem lista (`class.*`) só aceitam valor vazio.
  - O limite de 50 tags por objeto e 1000 tags de coluna por tabela é respeitado.

## D9. MV e Streaming Tables são puladas

- **Por quê:** objetos gerenciados por pipeline (Lakeflow Declarative Pipelines) têm comentários e tags redefinidos no próximo update. O lugar certo para esses metadados é o código do pipeline.

## D10. SQL com escaping por barra invertida

- **Por quê:** no Databricks SQL, `'It''s'` vira dois literais adjacentes (`Its`), corrompendo o texto em silêncio.
- **Como escapamos:** primeiro `\` vira `\\`, depois `'` vira `\'`. Identificadores vão entre crases, com crases internas duplicadas.
- **Validação:** testado contra o UC real com aspas, barras, `\n` literal, quebras de linha e acentos.

## D11. Ambiente de teste semeado via API, não escaneado

- **Escolha:** `demo/seed_purview.py` cria um Synapse fictício (workspace → pool → schema → tabelas/views → colunas) com os tipos nativos do Purview, via `entity/bulk`.
- **Por quê:**
  - Provisionar e escanear um Synapse custa caro e é lento.
  - Os tipos e o formato de `qualifiedName` são os mesmos que um scan real produz.
  - O cenário é reproduzível e serve de demo para o cliente.
- **Ressalva:** antes de produção, valide com o notebook `00_inventario` no Purview real do cliente.
