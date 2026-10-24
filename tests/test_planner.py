"""Planner against the real Purview snapshot and a UC state that mirrors demo/seed_uc.sql."""

import copy

import pytest

from purview2uc.matching import match_assets
from purview2uc.planner import UcTable, applied_key, build_plan, build_statements, check_drift, tag_problem

GOVERNED = {"class.email_address": set(), "class.name": set(), "class.date_of_birth": set(), "class.br_cpf": set(),
            "class.ip_address": set(), "class.credit_card": set(), "class.compensation": set(), "pii": {"EMAIL", "SSN"}}


def table(full, cols, table_type="MANAGED", comment="", tags=None, column_tags=None):
    return UcTable(full, table_type, comment, {c.lower(): {"name": c, "comment": ""} for c in cols}, tags or {}, column_tags or {})


def uc_state():
    t = [
        table("p2uc_demo.sales.dimcustomer", ["CustomerID", "FullName", "Email", "CPF", "BirthDate", "City"], tags={"owner_team": "vendas"}),
        table("p2uc_demo.sales.factsales", ["saleid", "customerid", "productid", "saledate", "netamount", "cardnumber"],
              comment="Tabela de vendas (comentário gerado por IA)"),
        table("p2uc_demo.sales.dimproduct", ["ProductID", "ProductName", "Category"]),
        table("p2uc_demo.sales.daily_targets", ["target_date", "store_id", "target_amount"]),
        table("p2uc_demo.hr.employee", ["EmployeeID", "Name", "Email", "Salary", "HireDate", "DepartmentID"],
              column_tags={"salary": {"sensibilidade": "interna"}}),
        table("p2uc_demo.hr.department", ["DepartmentID", "DepartmentName"]),
        table("p2uc_demo.hr.vw_employeedirectory", ["EmployeeID", "Name", "DepartmentName"], table_type="VIEW"),
        table("p2uc_demo.web.clickstream_events", ["event_id", "session_id", "ip_address", "event_ts", "page_url"]),
    ]
    return {x.full_name: x for x in t}


def plan(cfg, assets, state, applied=None):
    match = match_assets(assets, cfg.matching, set(state), set(state))
    return build_plan(match, state, cfg, GOVERNED, applied)


def find(rows, **kw):
    return [r for r in rows if all(r[k] == v for k, v in kw.items())]


def apply_planned(rows, state):
    """Simula o efeito do apply no estado do UC."""
    for r in rows:
        if r["status"] != "PLANNED":
            continue
        t = state[r["uc_object"]]
        col = (r["column_name"] or "").lower()
        if r["action"] == "SET_TABLE_COMMENT":
            t.comment = r["desired_value"]
        elif r["action"] == "SET_COLUMN_COMMENT":
            t.columns[col]["comment"] = r["desired_value"]
        elif r["action"] == "SET_TABLE_TAG":
            t.tags[r["tag_key"]] = r["desired_value"]
        elif r["action"] == "SET_COLUMN_TAG":
            t.column_tags.setdefault(col, {})[r["tag_key"]] = r["desired_value"]


def test_demo_scenario(cfg, assets):
    rows = plan(cfg, assets, uc_state())

    # Comentário com HTML virou markdown.
    [c] = find(rows, uc_object="p2uc_demo.sales.dimcustomer", action="SET_TABLE_COMMENT")
    assert c["status"] == "PLANNED" and c["desired_value"].startswith("Cadastro **mestre**")
    # Comentário já existente no UC não é sobrescrito.
    [c] = find(rows, uc_object="p2uc_demo.sales.factsales", action="SET_TABLE_COMMENT")
    assert c["status"] == "CONFLICT" and c["current_value"] == "Tabela de vendas (comentário gerado por IA)"
    # Fallback para 'description' quando não há userDescription.
    [c] = find(rows, uc_object="p2uc_demo.sales.dimproduct", action="SET_TABLE_COMMENT")
    assert c["source"] == "description"
    # Match de coluna case-insensitive: o nome gravado é o do UC.
    assert find(rows, uc_object="p2uc_demo.sales.factsales", action="SET_COLUMN_COMMENT", column_name="netamount")
    # Coluna que não foi migrada.
    assert find(rows, uc_object="p2uc_demo.hr.employee", action="NO_MATCH", column_name="BadgeLegacy", reason="COLUMN_NOT_IN_UC")
    # Preset class.* + alias com duas tags; a tag pré-existente diferente vira CONFLICT.
    assert find(rows, column_name="CPF", tag_key="class.br_cpf", status="PLANNED")
    assert find(rows, column_name="Salary", tag_key="class.compensation", status="PLANNED")
    [s] = find(rows, column_name="Salary", tag_key="sensibilidade")
    assert s["status"] == "CONFLICT" and s["current_value"] == "interna"
    # Termo de glossário com acento.
    [t] = find(rows, uc_object="p2uc_demo.sales.factsales", action="SET_TABLE_TAG")
    assert (t["tag_key"], t["desired_value"], t["source"]) == ("Receita Líquida", "", "term:Receita Líquida@Glossary")
    # Override de tabela renomeada e tabelas sem par nos dois sentidos.
    assert find(rows, uc_object="p2uc_demo.web.clickstream_events", tag_key="class.ip_address", status="PLANNED")
    assert find(rows, action="NO_MATCH", reason="UC_TABLE_NOT_FOUND", uc_object="p2uc_demo.hr.legacypayroll")
    assert find(rows, action="NO_MATCH", reason="UC_TABLE_WITHOUT_PURVIEW_ASSET", uc_object="p2uc_demo.sales.daily_targets")
    # Toda ação PLANNED tem o SQL que será executado.
    assert all(r["sql_statement"] for r in rows if r["status"] == "PLANNED")


def test_statements_are_grouped_per_table(cfg, assets):
    state = uc_state()
    rows = plan(cfg, assets, state)
    stmts = build_statements(rows, state)["p2uc_demo.sales.dimcustomer"]
    assert stmts[0][0].startswith("COMMENT ON TABLE")
    # Um único ALTER (um commit Delta) com os comentários das 6 colunas.
    [(alter, ids)] = [(s, ids) for s, ids in stmts if " COMMENT '" in s and s.startswith("ALTER TABLE")]
    assert len(ids) == 6 and all(f"`{c}` COMMENT" in alter for c in ["CustomerID", "FullName", "Email", "CPF", "BirthDate", "City"])
    # Na view, cada COMMENT ON COLUMN aponta só para a ação da sua coluna.
    view = build_statements(rows, state)["p2uc_demo.hr.vw_employeedirectory"]
    by_id = {r["action_id"]: r for r in rows}
    col_stmts = [(s, ids) for s, ids in view if s.startswith("COMMENT ON COLUMN")]
    assert len(col_stmts) == 3
    assert all(len(ids) == 1 and f"`{by_id[ids[0]]['column_name']}` IS" in s for s, ids in col_stmts)


def test_second_plan_after_apply_is_empty(cfg, assets):
    state = uc_state()
    apply_planned(plan(cfg, assets, state), state)
    again = plan(cfg, assets, state)
    assert find(again, status="PLANNED") == []


def test_purview_update_propagates_only_over_values_we_wrote(cfg, assets):
    state = uc_state()
    first = plan(cfg, assets, state)
    apply_planned(first, state)
    applied = {applied_key(r["uc_object"], r["column_name"], r["action"], r["tag_key"]): r["desired_value"]
               for r in first if r["status"] == "PLANNED"}

    changed = copy.deepcopy(assets)
    dept = next(a for a in changed if a["name"] == "Department")
    dept["userDescription"] = "Departamentos da empresa (estrutura 2026)."
    sales = next(a for a in changed if a["name"] == "FactSales")
    sales["userDescription"] = "Nova descrição no Purview."
    state["p2uc_demo.hr.employee"].comment = "Editado à mão no UC"
    emp = next(a for a in changed if a["name"] == "Employee")
    emp["userDescription"] = "Outra descrição no Purview."

    rows = plan(cfg, changed, state, applied)
    assert [(r["uc_object"], r["status"], r["reason"]) for r in rows if r["status"] in ("PLANNED", "CONFLICT")] == [
        ("p2uc_demo.hr.department", "PLANNED", "PURVIEW_UPDATED"),            # valor nosso -> atualiza
        ("p2uc_demo.hr.employee", "CONFLICT", "UC_ALREADY_HAS_DIFFERENT_COMMENT"),  # editado à mão -> protege
        ("p2uc_demo.hr.employee", "CONFLICT", "UC_TAG_HAS_DIFFERENT_VALUE"),  # sensibilidade=interna (manual)
        ("p2uc_demo.sales.factsales", "CONFLICT", "UC_ALREADY_HAS_DIFFERENT_COMMENT"),  # comentário de IA nunca foi nosso
    ]


def test_drift_detection(cfg, assets):
    state = uc_state()
    rows = plan(cfg, assets, state)
    planned = find(rows, status="PLANNED")
    later = copy.deepcopy(state)
    later["p2uc_demo.sales.dimcustomer"].comment = "alguém editou depois do plano"
    later["p2uc_demo.hr.department"].comment = next(r["desired_value"] for r in planned if r["uc_object"] == "p2uc_demo.hr.department" and r["action"] == "SET_TABLE_COMMENT")
    checked = {(r["uc_object"], r["action"], r["column_name"], r["tag_key"]): r for r in check_drift(planned, later)}
    assert checked[("p2uc_demo.sales.dimcustomer", "SET_TABLE_COMMENT", None, None)]["status"] == "DRIFTED"
    assert checked[("p2uc_demo.hr.department", "SET_TABLE_COMMENT", None, None)]["reason"] == "ALREADY_APPLIED"
    assert checked[("p2uc_demo.sales.dimproduct", "SET_TABLE_COMMENT", None, None)]["status"] == "PLANNED"


def test_overwrite_policies(cfg, assets):
    cfg.comments.on_conflict = "overwrite"
    cfg.tags.on_conflict = "overwrite"
    rows = plan(cfg, assets, uc_state())
    assert find(rows, uc_object="p2uc_demo.sales.factsales", action="SET_TABLE_COMMENT")[0]["status"] == "PLANNED"
    assert find(rows, column_name="Salary", tag_key="sensibilidade")[0]["status"] == "PLANNED"


def test_skip_pipeline_managed_tables(cfg, assets):
    state = uc_state()
    state["p2uc_demo.hr.department"].table_type = "MATERIALIZED_VIEW"
    rows = plan(cfg, assets, state)
    assert [r["action"] for r in find(rows, uc_object="p2uc_demo.hr.department")] == ["SKIP_OBJECT"]


def test_tag_limit(cfg, assets):
    state = uc_state()
    state["p2uc_demo.sales.dimcustomer"].tags = {f"t{i}": "" for i in range(49)}
    rows = plan(cfg, assets, state)
    table_tags = find(rows, uc_object="p2uc_demo.sales.dimcustomer", action="SET_TABLE_TAG")
    assert [r["status"] for r in table_tags].count("PLANNED") == 1
    assert {r["reason"] for r in table_tags if r["status"] == "SKIPPED"} == {"TAG_LIMIT_REACHED"}


@pytest.mark.parametrize(
    "key, value, problem",
    [
        ("class.email_address", "", None),
        ("class.email_address", "x", "GOVERNED_TAG_IS_KEY_ONLY"),
        ("pii", "EMAIL", None),
        ("pii", "contato", "GOVERNED_TAG_VALUE_NOT_ALLOWED"),
        ("bad.key", "v", "INVALID_TAG_KEY"),
        ("a=b", "v", "INVALID_TAG_KEY"),
        ("Receita Líquida", "", None),
        ("Cliente/Prospect - Ativo", "", None),
    ],
)
def test_tag_validation(key, value, problem):
    result = tag_problem(key, value, GOVERNED)
    if problem is None:
        assert result is None
    else:
        assert result.startswith(problem)
    assert tag_problem("bad.key", "v", None) is None  # políticas desconhecidas: não valida
