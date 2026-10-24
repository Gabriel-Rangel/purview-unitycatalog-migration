from purview2uc.config import CommentsConfig, TagsConfig
from purview2uc.transform import derive_tags, html_to_text, normalize_comment, pick_description, tag_key


def test_html_from_purview_rich_text_editor_becomes_markdown():
    raw = "<p>Cadastro <strong>mestre</strong> de clientes ativos.</p><ul><li>Fonte: CRM Dynamics</li><li>Atualização: diária às 02h</li></ul>"
    assert normalize_comment(html_to_text(raw)) == "Cadastro **mestre** de clientes ativos.\n\n- Fonte: CRM Dynamics\n- Atualização: diária às 02h"


def test_plain_text_is_kept_and_entities_unescaped():
    assert html_to_text("Valores em R$ &amp; centavos") == "Valores em R$ & centavos"
    assert html_to_text("a < b e c > d") == "a < b e c > d"


def test_normalize_comment_is_stable():
    assert normalize_comment("  a  \r\n\r\n\r\n  b  c  ") == "a\n\nb c"
    assert normalize_comment(None) == ""


def test_pick_description_prefers_user_description_then_falls_back():
    cfg = CommentsConfig()
    assert pick_description({"userDescription": "curada", "description": "scan"}, cfg) == ("curada", "userDescription")
    assert pick_description({"userDescription": "  ", "description": "scan"}, cfg) == ("scan", "description")
    assert pick_description({}, cfg) == ("", None)


def test_tag_key_keeps_purview_name_and_changes_only_what_uc_rejects():
    # Validado no UC: só '.' e '=' são rejeitados em chaves, além de espaços nas pontas.
    assert tag_key("Receita Líquida") == "Receita Líquida"
    assert tag_key("Cliente/Prospect - Ativo") == "Cliente/Prospect - Ativo"
    assert tag_key("  camada_gold ") == "camada_gold"
    assert tag_key("LGPD.DADOS_PESSOAIS") == "LGPD_DADOS_PESSOAIS"
    assert tag_key("a=b") == "a_b"
    key = tag_key("x" * 400)
    assert len(key) == 255 and key.startswith("xxx")


def test_derive_tags_use_exact_purview_names_without_prefix():
    obj = {
        "classifications": [{"type_name": "LGPD.DADOS_PESSOAIS"}],
        "terms": [{"name": "Receita Líquida", "qualified_name": "Receita Líquida@Glossary"}],
        "labels": ["camada_gold"],
    }
    tags, collisions = derive_tags(obj, TagsConfig())
    assert {(t.key, t.value) for t in tags} == {("LGPD_DADOS_PESSOAIS", ""), ("Receita Líquida", ""), ("camada_gold", "")}
    # A origem exata no Purview fica registrada (vai para sync_actions.source).
    assert {t.source for t in tags} == {"classification:LGPD.DADOS_PESSOAIS", "term:Receita Líquida@Glossary", "label:camada_gold"}
    assert collisions == []


def test_term_and_label_with_same_name_become_one_tag():
    tags, collisions = derive_tags({"terms": [{"name": "Cliente"}], "labels": ["Cliente"]}, TagsConfig())
    assert [(t.key, t.value) for t in tags] == [("Cliente", "")] and collisions == []


def test_preset_and_multi_tag_alias():
    cfg = TagsConfig(
        aliases_preset="databricks_class",
        aliases={"CONFIDENCIAL.SALARIO": [{"key": "class.compensation"}, {"key": "sensibilidade", "value": "restrita"}]},
    )
    tags, _ = derive_tags({"classifications": [{"type_name": "MICROSOFT.PERSONAL.EMAIL"}, {"type_name": "CONFIDENCIAL.SALARIO"}]}, cfg)
    assert [(t.key, t.value) for t in tags] == [("class.email_address", ""), ("class.compensation", ""), ("sensibilidade", "restrita")]


def test_include_exclude_and_collisions():
    cfg = TagsConfig(
        exclude=["MICROSOFT.PERSONAL.AGE"],
        aliases={"A": {"key": "pii", "value": "x"}, "B": {"key": "pii", "value": "y"}},
    )
    tags, collisions = derive_tags({"classifications": [{"type_name": "MICROSOFT.PERSONAL.AGE"}, {"type_name": "A"}, {"type_name": "B"}]}, cfg)
    assert [(t.key, t.value) for t in tags] == [("pii", "x")]
    assert len(collisions) == 1 and collisions[0][0].value == "y"
