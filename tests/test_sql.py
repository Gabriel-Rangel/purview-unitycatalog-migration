import pytest

from purview2uc import sql


@pytest.mark.parametrize(
    "text, expected",
    [
        ("simples", "'simples'"),
        ("it's", "'it\\'s'"),
        ("ERP\\SAP", "'ERP\\\\SAP'"),
        ("literal \\n não é quebra", "'literal \\\\n não é quebra'"),
        ("linha 1\nlinha 2", "'linha 1\nlinha 2'"),
        ("\\'", "'\\\\\\''"),
    ],
)
def test_sql_string_escapes_backslash_then_quote(text, expected):
    assert sql.sql_string(text) == expected


def test_quote_identifiers():
    assert sql.quote_ident("we`ird") == "`we``ird`"
    assert sql.quote_full_name("cat.sch.tbl") == "`cat`.`sch`.`tbl`"
    with pytest.raises(ValueError):
        sql.quote_full_name("sch.tbl")


def test_column_comments_batched_for_tables_and_per_column_for_views():
    stmts = sql.column_comments("c.s.t", "MANAGED", {"a": "x", "b": "y"})
    assert stmts == [("ALTER TABLE `c`.`s`.`t` ALTER COLUMN `a` COMMENT 'x', `b` COMMENT 'y'", ["a", "b"])]
    stmts = sql.column_comments("c.s.v", "VIEW", {"a": "x", "b": "y"})
    assert stmts == [
        ("COMMENT ON COLUMN `c`.`s`.`v`.`a` IS 'x'", ["a"]),
        ("COMMENT ON COLUMN `c`.`s`.`v`.`b` IS 'y'", ["b"]),
    ]


def test_set_tags_table_and_column():
    assert sql.set_tags("c.s.t", {"k": "v", "class.name": ""}) == "ALTER TABLE `c`.`s`.`t` SET TAGS ('k' = 'v', 'class.name' = '')"
    assert sql.set_tags("c.s.t", {"k": "it's"}, column="Email") == "ALTER TABLE `c`.`s`.`t` ALTER COLUMN `Email` SET TAGS ('k' = 'it\\'s')"
