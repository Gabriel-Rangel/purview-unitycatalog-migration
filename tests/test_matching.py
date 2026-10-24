from purview2uc.config import ConfigError, MatchingConfig, MatchingRule, parse_config
from purview2uc.matching import match_assets, resolve_target

import pytest

DED = "azure_synapse_dedicated_sql_table"
SRV = "azure_synapse_serverless_sql_table"


def test_rules_maps_and_lowercase(cfg):
    m = cfg.matching
    assert resolve_target("mssql://p2ucdemo.sql.azuresynapse.net/sqlpool01/sales/DimCustomer", DED, m) == ("p2uc_demo.sales.dimcustomer", "rule:0")
    # Maps são case-insensitive na origem.
    assert resolve_target("mssql://p2ucdemo.sql.azuresynapse.net/SQLPOOL01/hr/Employee", DED, m)[0] == "p2uc_demo.hr.employee"
    # Sem entrada no map, o valor passa como está (minúsculo).
    assert resolve_target("mssql://p2ucdemo.sql.azuresynapse.net/outropool/dbo/T", DED, m)[0] == "outropool.dbo.t"


def test_rule_restricted_by_entity_type(cfg):
    qn = "mssql://p2ucdemo-ondemand.sql.azuresynapse.net/lakehouse/dbo/page_views"
    assert resolve_target(qn, SRV, cfg.matching) == ("p2uc_demo.web.page_views", "rule:1")


def test_override_wins_and_can_ignore():
    m = MatchingConfig(
        rules=[MatchingRule(purview_qn_regex=r"^x/(?P<t>\w+)$", uc_target="c.s.{t}")],
        overrides={"x/a": "c.s.renamed", "x/b": ""},
    )
    assert resolve_target("x/a", DED, m) == ("c.s.renamed", "override")
    assert resolve_target("x/b", DED, m) == (None, "override:ignore")
    assert resolve_target("y/a", DED, m) == (None, "no_rule")


def test_match_assets_reports_every_bucket():
    m = MatchingConfig(rules=[MatchingRule(purview_qn_regex=r"^(?P<s>\w+)/(?P<t>\w+)$", uc_target="cat.{s}.{t}")])
    asset = lambda qn: {"guid": qn, "type_name": DED, "qualified_name": qn}  # noqa: E731
    assets = [asset("s/ok"), asset("s/missing"), asset("nope"), asset("S/Dup"), asset("s/dup")]
    uc = {"cat.s.ok", "cat.s.dup", "cat.s.only_uc"}
    r = match_assets(assets, m, uc, uc_scope=uc)
    assert [(a["qualified_name"], t) for a, t, _ in r.matched] == [("s/ok", "cat.s.ok")]
    assert {(a["qualified_name"], why) for a, why, _ in r.purview_unmatched} == {("s/missing", "UC_TABLE_NOT_FOUND"), ("nope", "NO_MATCHING_RULE")}
    assert sorted(a["qualified_name"] for a, _ in r.ambiguous) == ["S/Dup", "s/dup"]
    assert r.uc_unmatched == ["cat.s.only_uc"]


def test_invalid_rule_is_rejected_early():
    with pytest.raises(ConfigError):
        MatchingRule(purview_qn_regex=r"^(?P<a>\w+)$", uc_target="c.s.{missing}")
    with pytest.raises(ConfigError):
        parse_config({"purview": {"account": "x"}, "control": {"catalog": "c"}})
