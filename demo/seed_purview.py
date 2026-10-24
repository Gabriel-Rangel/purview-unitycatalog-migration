"""Popula um Purview de teste com um Synapse fictício (sem provisionar nem escanear um Synapse real).

Uso (da raiz do repo, logado com `az login` e com papel Data Curator na collection raiz):
    python3 demo/seed_purview.py --account <nome-da-conta-purview>

Idempotente: pode ser executado várias vezes (entity/bulk faz upsert por qualifiedName).
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from purview2uc.auth import _from_azure_cli  # noqa: E402
from purview2uc.purview import PurviewClient  # noqa: E402

WS = "p2ucdemo"
DEDICATED = f"mssql://{WS}.sql.azuresynapse.net/sqlpool01"
SERVERLESS = f"mssql://{WS}-ondemand.sql.azuresynapse.net/lakehouse"

CUSTOM_CLASSIFICATIONS = {
    "LGPD.DADOS_PESSOAIS": "Asset contém dados pessoais sujeitos à LGPD",
    "CONFIDENCIAL.SALARIO": "Informação salarial confidencial",
}
TERMS = {
    "Cliente": "Pessoa física ou jurídica com ao menos uma compra concluída.",
    "CPF": "Cadastro de Pessoas Físicas (Receita Federal).",
    "Receita Líquida": "Receita bruta menos impostos, descontos e devoluções.",
}

# Cada coluna: (nome, data_type, descrição, [classificações], [termos])
TABLES = [
    {
        "db": DEDICATED, "schema": "sales", "name": "DimCustomer",
        "userDescription": "<p>Cadastro <strong>mestre</strong> de clientes ativos.</p>"
                           "<ul><li>Fonte: CRM Dynamics</li><li>Atualização: diária às 02h</li></ul>",
        "classifications": ["LGPD.DADOS_PESSOAIS"], "labels": ["camada_gold", "lgpd"], "terms": ["Cliente"],
        "columns": [
            ("CustomerID", "int", "Identificador único do cliente (chave substituta).", [], []),
            ("FullName", "nvarchar", "Nome completo do cliente.", ["MICROSOFT.PERSONAL.NAME"], []),
            ("Email", "nvarchar", "E-mail principal do cliente.", ["MICROSOFT.PERSONAL.EMAIL"], []),
            ("CPF", "char", "CPF do titular, sem pontuação.", ["MICROSOFT.GOVERNMENT.BRAZIL.CPF_NUMBER"], ["CPF"]),
            ("BirthDate", "date", "Data de nascimento.", ["MICROSOFT.PERSONAL.DATE_OF_BIRTH"], []),
            ("City", "nvarchar", "Cidade de residência.", [], []),
        ],
    },
    {
        "db": DEDICATED, "schema": "sales", "name": "FactSales",
        # Aspas simples e barras invertidas de propósito: testam o escaping do SQL.
        "userDescription": "Vendas consolidadas por item (fonte: 'ERP\\SAP', export em C:\\export\\vendas). Valores em R$.",
        "terms": ["Receita Líquida"],
        "columns": [
            ("SaleID", "bigint", "Identificador da venda.", [], []),
            ("CustomerID", "int", "FK para DimCustomer.", [], []),
            ("ProductID", "int", "FK para DimProduct.", [], []),
            ("SaleDate", "date", "Data da venda (fuso America/Sao_Paulo).", [], []),
            ("NetAmount", "decimal", "Valor líquido em R$, já descontados impostos e devoluções.", [], ["Receita Líquida"]),
            ("CardNumber", "varchar", "Número mascarado do cartão usado no pagamento.", ["MICROSOFT.FINANCIAL.CREDIT_CARD_NUMBER"], []),
        ],
    },
    {
        "db": DEDICATED, "schema": "sales", "name": "DimProduct",
        # Só 'description' (como viria de um scan), sem userDescription: testa o fallback.
        "description": "Catálogo de produtos importado do ERP.",
        "columns": [
            ("ProductID", "int", "Identificador do produto.", [], []),
            ("ProductName", "nvarchar", "Nome comercial do produto.", [], []),
            ("Category", "nvarchar", "Categoria do produto.\nValores possíveis:\n  - Eletrônicos\n  - Casa", [], []),
            ("LegacyCode", "varchar", "Código do sistema antigo (coluna removida).", [], []),
        ],
        "delete_columns": ["LegacyCode"],
    },
    {
        "db": DEDICATED, "schema": "hr", "name": "Employee",
        "userDescription": "Funcionários ativos e desligados nos últimos 5 anos. Acesso restrito ao RH.",
        "classifications": ["LGPD.DADOS_PESSOAIS"], "labels": ["restrito"],
        "columns": [
            ("EmployeeID", "int", "Matrícula do funcionário.", [], []),
            ("Name", "nvarchar", "Nome completo.", ["MICROSOFT.PERSONAL.NAME"], []),
            ("Email", "nvarchar", "E-mail corporativo.", ["MICROSOFT.PERSONAL.EMAIL"], []),
            ("Salary", "decimal", "Salário base mensal (R$).", ["CONFIDENCIAL.SALARIO"], []),
            ("HireDate", "date", "Data de admissão.", [], []),
            ("BadgeLegacy", "varchar", "Número do crachá no sistema antigo (não migrado).", [], []),
        ],
    },
    {
        "db": DEDICATED, "schema": "hr", "name": "Department",
        "userDescription": "Departamentos da empresa.",
        "columns": [
            ("DepartmentID", "int", "Identificador do departamento.", [], []),
            ("DepartmentName", "nvarchar", "Nome do departamento.", [], []),
        ],
    },
    {
        # Existe só no Purview: não foi migrada -> UC_TABLE_NOT_FOUND.
        "db": DEDICATED, "schema": "hr", "name": "LegacyPayroll",
        "userDescription": "Folha de pagamento do sistema legado (descontinuada).",
        "columns": [("PayrollID", "int", "Identificador da folha.", [], [])],
    },
    {
        "db": DEDICATED, "schema": "hr", "name": "vw_EmployeeDirectory", "view": True,
        "userDescription": "Diretório de funcionários sem dados sensíveis.",
        "columns": [
            ("EmployeeID", "int", "Matrícula do funcionário.", [], []),
            ("Name", "nvarchar", "Nome completo.", ["MICROSOFT.PERSONAL.NAME"], []),
            ("DepartmentName", "nvarchar", "Nome do departamento.", [], []),
        ],
    },
    {
        # Serverless + renomeada na migração -> resolvida pelo table_overrides.csv.
        "db": SERVERLESS, "schema": "dbo", "name": "web_events", "serverless": True,
        "userDescription": "Eventos de navegação do site (clickstream), particionados por dia.",
        "columns": [
            ("event_id", "varchar", "Identificador do evento.", [], []),
            ("session_id", "varchar", "Sessão do navegador.", [], []),
            ("ip_address", "varchar", "IP de origem.", ["MICROSOFT.PERSONAL.IPADDRESS"], []),
            ("event_ts", "datetime2", "Momento do evento (UTC).", [], []),
            ("page_url", "varchar", "URL visitada.", [], []),
        ],
    },
]


def ensure_classifications(pv: PurviewClient):
    missing = []
    for name, desc in CUSTOM_CLASSIFICATIONS.items():
        try:
            pv.request("GET", f"/datamap/api/atlas/v2/types/classificationdef/name/{name}")
        except RuntimeError:
            missing.append({"name": name, "description": desc, "attributeDefs": []})
    if missing:
        pv.request("POST", "/datamap/api/atlas/v2/types/typedefs", json={"classificationDefs": missing})
    print(f"classificações customizadas: {len(missing)} criadas, {len(CUSTOM_CLASSIFICATIONS) - len(missing)} já existiam")


def ensure_glossary_terms(pv: PurviewClient) -> dict[str, str]:
    glossaries = pv.request("GET", "/datamap/api/atlas/v2/glossary")
    if glossaries:
        glossary = glossaries[0]
    else:
        glossary = pv.request("POST", "/datamap/api/atlas/v2/glossary", json={"name": "Glossary", "shortDescription": "Glossário corporativo"})
    existing = {t["name"]: t["guid"] for t in pv.request("GET", f"/datamap/api/atlas/v2/glossary/{glossary['guid']}/terms", params=[("limit", "1000")]) or []}
    for name, desc in TERMS.items():
        if name not in existing:
            term = pv.request("POST", "/datamap/api/atlas/v2/glossary/term",
                              json={"name": name, "longDescription": desc, "status": "Approved", "anchor": {"glossaryGuid": glossary["guid"]}})
            existing[name] = term["guid"]
    print(f"glossário '{glossary['name']}': termos {sorted(TERMS)}")
    return existing


def build_entities():
    entities, counter = [], [0]

    def add(type_name, qn, name, attrs=None, rels=None):
        counter[0] -= 1
        guid = str(counter[0])
        entities.append({"typeName": type_name, "guid": guid,
                         "attributes": {"qualifiedName": qn, "name": name, **(attrs or {})},
                         "relationshipAttributes": rels or {}})
        return guid

    ws = add("azure_synapse_workspace", f"https://{WS}.dev.azuresynapse.net", WS)
    dbs, schemas, specs = {}, {}, {}
    for t in TABLES:
        kind = "serverless" if t.get("serverless") else "dedicated"
        if t["db"] not in dbs:
            dbs[t["db"]] = add(f"azure_synapse_{kind}_sql_db", t["db"], t["db"].rsplit("/", 1)[1], rels={"workspace": {"guid": ws}})
        schema_qn = f"{t['db']}/{t['schema']}"
        if schema_qn not in schemas:
            schemas[schema_qn] = add(f"azure_synapse_{kind}_sql_schema", schema_qn, t["schema"], rels={"db": {"guid": dbs[t["db"]]}})
        table_qn = f"{schema_qn}/{t['name']}"
        attrs = {k: t[k] for k in ("userDescription", "description") if k in t}
        if t.get("view"):
            table_guid = add("azure_synapse_dedicated_sql_view", table_qn, t["name"], attrs, {"dbSchema": {"guid": schemas[schema_qn]}})
            col_type, col_rel = "azure_synapse_dedicated_sql_view_column", "view"
        else:
            table_guid = add(f"azure_synapse_{kind}_sql_table", table_qn, t["name"], attrs, {"dbSchema": {"guid": schemas[schema_qn]}})
            col_type, col_rel = f"azure_synapse_{kind}_sql_column", "table"
        specs[table_qn] = (t, table_guid)
        for name, dtype, desc, _c, _t in t["columns"]:
            add(col_type, f"{table_qn}#{name}", name, {"data_type": dtype, "userDescription": desc}, {col_rel: {"guid": table_guid}})
    return entities, specs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="nome da conta Purview (ex.: gabrielrangel-purview)")
    args = ap.parse_args()
    pv = PurviewClient(f"https://{args.account}.purview.azure.com", _from_azure_cli())

    ensure_classifications(pv)
    term_guids = ensure_glossary_terms(pv)

    entities, specs = build_entities()
    resp = pv.request("POST", "/datamap/api/atlas/v2/entity/bulk", json={"entities": entities})
    assigned = resp.get("guidAssignments", {})
    real = {e["attributes"]["qualifiedName"]: assigned.get(e["guid"]) for e in entities}
    print(f"entidades: {len(entities)} enviadas (upsert)")

    current = pv.get_entities([g for g in real.values() if g], chunk_size=50)

    def decorate(qn, classifications, labels, terms):
        guid = real[qn]
        entity = current.get(guid, {})
        have = {c["typeName"] for c in entity.get("classifications") or []}
        todo = [{"typeName": c, "propagate": False} for c in classifications if c not in have]
        if todo:
            pv.request("POST", f"/datamap/api/atlas/v2/entity/guid/{guid}/classifications", json=todo, ok_status=(200, 204))
        if labels:
            pv.request("POST", f"/datamap/api/atlas/v2/entity/guid/{guid}/labels", json=labels, ok_status=(200, 204))
        meanings = {m["guid"] for m in (entity.get("relationshipAttributes") or {}).get("meanings") or [] if m.get("relationshipStatus") == "ACTIVE"}
        for term in terms:
            if term_guids[term] not in meanings:
                pv.request("POST", f"/datamap/api/atlas/v2/glossary/terms/{term_guids[term]}/assignedEntities",
                           json=[{"guid": guid}], ok_status=(200, 204))

    for table_qn, (t, _guid) in specs.items():
        decorate(table_qn, t.get("classifications", []), t.get("labels", []), t.get("terms", []))
        for name, _d, _desc, classes, terms in t["columns"]:
            decorate(f"{table_qn}#{name}", classes, [], terms)
        for name in t.get("delete_columns", []):
            pv.request("DELETE", f"/datamap/api/atlas/v2/entity/guid/{real[f'{table_qn}#{name}']}", ok_status=(200, 204))
    print("classificações, labels, termos e coluna removida aplicados")
    print("Aguarde ~5 min para o índice de busca do Purview refletir as entidades.")


if __name__ == "__main__":
    main()
