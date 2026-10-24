import copy

from purview2uc.purview import normalize_entity

DIMCUSTOMER = "mssql://p2ucdemo.sql.azuresynapse.net/sqlpool01/sales/DimCustomer"
TERMS = {}


def lookup(guid):
    return TERMS.get(guid)


def test_normalize_real_entity(raw_entities):
    raw = raw_entities[DIMCUSTOMER]
    asset = normalize_entity(raw["entity"], raw["columns"], lookup)
    assert asset["qualified_name"] == DIMCUSTOMER
    assert asset["labels"] == ["camada_gold", "lgpd"]
    assert [c["type_name"] for c in asset["classifications"]] == ["LGPD.DADOS_PESSOAIS"]
    # Sem lookup, o nome do termo vem do displayText do relacionamento.
    assert [t["name"] for t in asset["terms"]] == ["Cliente"]
    cols = {c["name"]: c for c in asset["columns"]}
    assert set(cols) == {"BirthDate", "CPF", "City", "CustomerID", "Email", "FullName"}
    assert [c["type_name"] for c in cols["CPF"]["classifications"]] == ["MICROSOFT.GOVERNMENT.BRAZIL.CPF_NUMBER"]
    assert [t["name"] for t in cols["CPF"]["terms"]] == ["CPF"]


def test_propagated_classification_is_ignored_by_default(raw_entities):
    raw = copy.deepcopy(raw_entities[DIMCUSTOMER])
    raw["entity"]["classifications"].append({"typeName": "MICROSOFT.PERSONAL.NAME", "entityGuid": "outra-entidade", "entityStatus": "ACTIVE"})
    asset = normalize_entity(raw["entity"], raw["columns"], lookup)
    assert [c["type_name"] for c in asset["classifications"]] == ["LGPD.DADOS_PESSOAIS"]
    asset = normalize_entity(raw["entity"], raw["columns"], lookup, include_propagated=True)
    assert {c["type_name"]: c["propagated"] for c in asset["classifications"]} == {"LGPD.DADOS_PESSOAIS": False, "MICROSOFT.PERSONAL.NAME": True}


def test_deleted_column_and_inactive_term_are_ignored(raw_entities):
    raw = copy.deepcopy(raw_entities[DIMCUSTOMER])
    rel = raw["entity"]["relationshipAttributes"]
    email_ref = next(r for r in rel["columns"] if r["displayText"] == "Email")
    email_ref["relationshipStatus"] = "DELETED"
    rel["meanings"][0]["relationshipStatus"] = "DELETED"
    asset = normalize_entity(raw["entity"], raw["columns"], lookup)
    assert "Email" not in {c["name"] for c in asset["columns"]}
    assert asset["terms"] == []


def test_term_lookup_gives_qualified_name(raw_entities):
    raw = raw_entities[DIMCUSTOMER]
    guid = raw["entity"]["relationshipAttributes"]["meanings"][0]["guid"]
    asset = normalize_entity(raw["entity"], raw["columns"], {guid: {"name": "Cliente", "qualifiedName": "Cliente@Glossary"}}.get)
    assert asset["terms"] == [{"guid": guid, "name": "Cliente", "qualified_name": "Cliente@Glossary"}]
