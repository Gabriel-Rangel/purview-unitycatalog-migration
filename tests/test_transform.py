from purview2uc.config import CommentsConfig, TagsConfig
from purview2uc.transform import derive_tags, html_to_text, make_tag_key, normalize_comment, pick_description, slugify


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


def test_slug_and_tag_key_respect_uc_rules():
    assert slugify("Receita Líquida") == "receita_liquida"
    assert slugify("MICROSOFT.PERSONAL.EMAIL") == "microsoft_personal_email"
    key = make_tag_key("purview_", "term", "x" * 400)
    assert len(key) == 255 and key.startswith("purview_term_x")


def test_derive_tags_default_keys_and_values():
    obj = {
        "classifications": [{"type_name": "LGPD.DADOS_PESSOAIS"}],
        "terms": [{"name": "Cliente", "qualified_name": "Cliente@Glossary"}],
        "labels": ["camada_gold"],
    }
    tags, collisions = derive_tags(obj, TagsConfig())
    assert {(t.key, t.value) for t in tags} == {
        ("purview_class_lgpd_dados_pessoais", "LGPD.DADOS_PESSOAIS"),
        ("purview_term_cliente", "Cliente@Glossary"),
        ("purview_label_camada_gold", ""),
    }
    assert collisions == []


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
