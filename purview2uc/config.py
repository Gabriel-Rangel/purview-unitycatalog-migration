"""Load and validate the YAML configuration used by the notebooks."""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field

import yaml

from .presets import PRESETS

DEFAULT_ENTITY_TYPES = [
    "azure_synapse_dedicated_sql_table",
    "azure_synapse_serverless_sql_table",
    "azure_sql_dw_table",
]


class ConfigError(ValueError):
    pass


@dataclass
class PurviewConfig:
    account: str
    entity_types: list[str] = field(default_factory=lambda: list(DEFAULT_ENTITY_TYPES))
    collection_ids: list[str] = field(default_factory=list)
    include_propagated_classifications: bool = False
    bulk_size: int = 50
    column_bulk_size: int = 100
    max_workers: int = 4

    @property
    def endpoint(self) -> str:
        if self.account.startswith("https://"):
            return self.account.rstrip("/")
        return f"https://{self.account}.purview.azure.com"


@dataclass
class AuthConfig:
    # service_credential (recomendado) | secret_scope | azure_cli (só para uso local/dev)
    method: str = "service_credential"
    service_credential: str = "purview-reader"
    secret_scope: str | None = None
    tenant_id_key: str = "tenant-id"
    client_id_key: str = "client-id"
    client_secret_key: str = "client-secret"


@dataclass
class MatchingRule:
    purview_qn_regex: str
    uc_target: str
    maps: dict[str, dict[str, str]] = field(default_factory=dict)
    normalize: str = "lower"
    entity_types: list[str] = field(default_factory=list)

    def __post_init__(self):
        try:
            self.pattern = re.compile(self.purview_qn_regex, re.IGNORECASE)
        except re.error as e:
            raise ConfigError(f"Regex inválida em matching.rules: {self.purview_qn_regex!r}: {e}") from e
        groups = set(self.pattern.groupindex)
        needed = set(re.findall(r"{(\w+)}", self.uc_target))
        if needed - groups:
            raise ConfigError(
                f"uc_target {self.uc_target!r} usa {sorted(needed - groups)}, "
                f"que não existem como grupos nomeados na regex"
            )
        if self.normalize not in ("lower", "none"):
            raise ConfigError("matching.rules[].normalize deve ser 'lower' ou 'none'")
        # Lookups case-insensitive: o qualifiedName do Synapse preserva maiúsculas.
        self.maps = {g: {str(k).lower(): str(v) for k, v in m.items()} for g, m in self.maps.items()}


@dataclass
class MatchingConfig:
    rules: list[MatchingRule] = field(default_factory=list)
    overrides: dict[str, str] = field(default_factory=dict)  # qualifiedName -> uc full name ("" = ignorar)


@dataclass
class UnityCatalogConfig:
    catalogs: list[str] = field(default_factory=list)
    exclude_schemas: list[str] = field(default_factory=lambda: ["information_schema"])
    skip_table_types: list[str] = field(default_factory=lambda: ["MATERIALIZED_VIEW", "STREAMING_TABLE"])


@dataclass
class CommentsConfig:
    table_comments: bool = True
    column_comments: bool = True
    source_priority: list[str] = field(default_factory=lambda: ["userDescription", "description"])
    on_conflict: str = "fill_empty"  # fill_empty | overwrite


@dataclass
class TagsConfig:
    prefix: str = "purview_"
    classifications: bool = True
    terms: bool = True
    labels: bool = True
    managed_attributes: bool = False
    include: list[str] = field(default_factory=lambda: ["*"])
    exclude: list[str] = field(default_factory=list)
    aliases_preset: str | None = None  # ex.: databricks_class (ver presets.py)
    # classificação -> {key, value} ou lista de {key, value}; sobrepõe o preset
    aliases: dict = field(default_factory=dict)
    on_conflict: str = "skip"  # skip | overwrite
    max_tags_per_object: int = 50
    max_column_tags_per_table: int = 1000

    def __post_init__(self):
        merged = {}
        if self.aliases_preset:
            if self.aliases_preset not in PRESETS:
                raise ConfigError(f"tags.aliases_preset desconhecido: {self.aliases_preset!r} (opções: {sorted(PRESETS)})")
            merged.update(PRESETS[self.aliases_preset])
        for name, target in (self.aliases or {}).items():
            targets = target if isinstance(target, list) else [target]
            for t in targets:
                if not isinstance(t, dict) or "key" not in t:
                    raise ConfigError(f"tags.aliases[{name!r}] deve ser {{key, value}} ou lista deles")
            merged[name] = [{"key": t["key"], "value": str(t.get("value", ""))} for t in targets]
        self.aliases = merged


@dataclass
class ControlConfig:
    catalog: str
    schema: str = "purview_migration"

    @property
    def actions_table(self) -> str:
        return f"`{self.catalog}`.`{self.schema}`.`sync_actions`"

    @property
    def snapshot_table(self) -> str:
        return f"`{self.catalog}`.`{self.schema}`.`purview_snapshot`"


@dataclass
class ExecutionConfig:
    max_workers: int = 8


@dataclass
class Config:
    purview: PurviewConfig
    auth: AuthConfig
    matching: MatchingConfig
    unity_catalog: UnityCatalogConfig
    comments: CommentsConfig
    tags: TagsConfig
    control: ControlConfig
    execution: ExecutionConfig


def _build(cls, data, section):
    data = data or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Seção '{section}' deve ser um objeto")
    known = set(cls.__dataclass_fields__)
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"Chaves desconhecidas em '{section}': {sorted(unknown)}")
    try:
        return cls(**data)
    except TypeError as e:
        raise ConfigError(f"Seção '{section}': {e}") from e


def read_overrides_csv(path: str) -> dict[str, str]:
    """CSV com colunas purview_qualified_name,uc_full_name. uc_full_name vazio = ignorar o asset."""
    overrides = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(line for line in f if not line.lstrip().startswith("#")):
            qn = (row.get("purview_qualified_name") or "").strip()
            if qn:
                overrides[qn] = (row.get("uc_full_name") or "").strip().lower()
    return overrides


def load_config(path: str) -> Config:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return parse_config(raw, base_dir=os.path.dirname(os.path.abspath(path)))


def parse_config(raw: dict, base_dir: str = ".") -> Config:
    for required in ("purview", "control"):
        if required not in raw:
            raise ConfigError(f"Seção obrigatória ausente: '{required}'")

    tags = _build(TagsConfig, raw.get("tags"), "tags")
    if tags.on_conflict not in ("skip", "overwrite"):
        raise ConfigError("tags.on_conflict deve ser 'skip' ou 'overwrite'")
    comments = _build(CommentsConfig, raw.get("comments"), "comments")
    if comments.on_conflict not in ("fill_empty", "overwrite"):
        raise ConfigError("comments.on_conflict deve ser 'fill_empty' ou 'overwrite'")
    auth = _build(AuthConfig, raw.get("auth"), "auth")
    if auth.method not in ("service_credential", "secret_scope", "azure_cli"):
        raise ConfigError("auth.method deve ser 'service_credential', 'secret_scope' ou 'azure_cli'")

    matching_raw = dict(raw.get("matching") or {})
    rules = [_build(MatchingRule, r, "matching.rules[]") for r in matching_raw.pop("rules", [])]
    overrides_csv = matching_raw.pop("overrides_csv", None)
    if matching_raw:
        raise ConfigError(f"Chaves desconhecidas em 'matching': {sorted(matching_raw)}")
    overrides = {}
    if overrides_csv:
        csv_path = overrides_csv if os.path.isabs(overrides_csv) else os.path.join(base_dir, overrides_csv)
        overrides = read_overrides_csv(csv_path)
    if not rules and not overrides:
        raise ConfigError("Defina ao menos uma regra em matching.rules ou um matching.overrides_csv")

    return Config(
        purview=_build(PurviewConfig, raw.get("purview"), "purview"),
        auth=auth,
        matching=MatchingConfig(rules=rules, overrides=overrides),
        unity_catalog=_build(UnityCatalogConfig, raw.get("unity_catalog"), "unity_catalog"),
        comments=comments,
        tags=tags,
        control=_build(ControlConfig, raw.get("control"), "control"),
        execution=_build(ExecutionConfig, raw.get("execution"), "execution"),
    )
