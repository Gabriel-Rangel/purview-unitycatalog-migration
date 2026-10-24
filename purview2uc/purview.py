"""Read-only access to the Purview Data Map (plain REST) and normalization of the results.

Why REST instead of PyApacheAtlas / azure-purview-datamap: fewer dependencies, every call is
visible, and the endpoints used here are GA (api-version 2023-09-01).
"""

from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from .config import PurviewConfig

log = logging.getLogger(__name__)
API_VERSION = "2023-09-01"
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _is_active(related: dict) -> bool:
    return related.get("relationshipStatus", "ACTIVE") == "ACTIVE" and related.get("entityStatus", "ACTIVE") == "ACTIVE"


class PurviewClient:
    def __init__(self, endpoint: str, token_provider, max_retries: int = 6, timeout: int = 60):
        self.endpoint = endpoint.rstrip("/")
        self.token_provider = token_provider
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        self._terms: dict[str, dict | None] = {}

    def request(self, method: str, path: str, params=None, json=None, ok_status=(200,)):
        params = list(params or [])
        params.append(("api-version", API_VERSION))
        for attempt in range(self.max_retries + 1):
            resp = self.session.request(
                method,
                f"{self.endpoint}{path}",
                params=params,
                json=json,
                headers={"Authorization": f"Bearer {self.token_provider()}"},
                timeout=self.timeout,
            )
            if resp.status_code in ok_status:
                return resp.json() if resp.content else None
            if resp.status_code in _RETRY_STATUS and attempt < self.max_retries:
                wait = float(resp.headers.get("Retry-After") or min(2**attempt, 30)) + random.random()
                log.warning("Purview %s %s -> %s; nova tentativa em %.1fs", method, path, resp.status_code, wait)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Purview {method} {path} -> HTTP {resp.status_code}: {resp.text[:500]}")

    # ---------------------------------------------------------------- discovery
    def search(self, entity_types: list[str], collection_ids: list[str] | None = None, page_size: int = 1000):
        """All entities of the given types. The search index lags a few minutes and lacks
        userDescription, so we only take GUIDs from it and read the content via the Atlas API."""
        clauses = [{"or": [{"entityType": t, "includeSubTypes": False} for t in entity_types]}]
        if collection_ids:
            clauses.append({"or": [{"collectionId": c} for c in collection_ids]})
        body = {"keywords": None, "limit": page_size, "filter": {"and": clauses}}
        results = []
        while True:
            page = self.request("POST", "/datamap/api/search/query", json=body)
            results.extend(page.get("value", []))
            token = page.get("continuationToken")
            if not token or not page.get("value"):
                return results
            body["continuationToken"] = token

    def entity_type_counts(self) -> list[tuple[str, int]]:
        # O facet devolve só os tipos (sem contagem); contamos cada tipo com @search.count.
        body = {"keywords": None, "limit": 1, "facets": [{"facet": "entityType", "count": 500}]}
        page = self.request("POST", "/datamap/api/search/query", json=body)
        counts = []
        for facet in page.get("@search.facets", {}).get("entityType", []):
            body = {"keywords": None, "limit": 1, "filter": {"entityType": facet["value"], "includeSubTypes": False}}
            counts.append((facet["value"], self.request("POST", "/datamap/api/search/query", json=body).get("@search.count", 0)))
        return sorted(counts, key=lambda c: (-c[1], c[0]))

    # ---------------------------------------------------------------- entities
    def get_entities(self, guids: list[str], chunk_size: int, max_workers: int = 4) -> dict[str, dict]:
        def fetch(chunk):
            params = [("guid", g) for g in chunk] + [("minExtInfo", "true"), ("ignoreRelationships", "false")]
            return self.request("GET", "/datamap/api/atlas/v2/entity/bulk", params=params).get("entities", [])

        out = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for entities in pool.map(fetch, list(_chunks(guids, chunk_size))):
                for e in entities:
                    out[e["guid"]] = e
        return out

    def get_term(self, guid: str) -> dict | None:
        if guid not in self._terms:
            try:
                self._terms[guid] = self.request("GET", f"/datamap/api/atlas/v2/glossary/term/{guid}")
            except RuntimeError as e:
                log.warning("Termo de glossário %s não pôde ser lido: %s", guid, e)
                self._terms[guid] = None
        return self._terms[guid]


# -------------------------------------------------------------------- normalization
def _classifications(entity: dict, include_propagated: bool) -> list[dict]:
    out = []
    for c in entity.get("classifications") or []:
        if c.get("entityStatus", "ACTIVE") != "ACTIVE":
            continue
        propagated = c.get("entityGuid") not in (None, entity["guid"])
        if propagated and not include_propagated:
            continue
        out.append({"type_name": c["typeName"], "propagated": propagated})
    return out


def _terms(entity: dict, term_lookup) -> list[dict]:
    out = []
    for m in (entity.get("relationshipAttributes") or {}).get("meanings") or []:
        if not _is_active(m):
            continue
        term = term_lookup(m["guid"]) or {}
        name = term.get("name") or m.get("displayText")
        if name:
            out.append({"guid": m["guid"], "name": name, "qualified_name": term.get("qualifiedName") or name})
    return out


def normalize_entity(entity: dict, column_entities: dict[str, dict], term_lookup, include_propagated: bool = False) -> dict:
    """Atlas entity (+ its column entities) -> plain dict stored in the snapshot and used by the planner."""

    def base(e):
        a = e.get("attributes") or {}
        return {
            "guid": e["guid"],
            "type_name": e["typeName"],
            "name": a.get("name"),
            "qualified_name": a.get("qualifiedName"),
            "userDescription": a.get("userDescription"),
            "description": a.get("description"),
            "classifications": _classifications(e, include_propagated),
            "labels": sorted(e.get("labels") or []),
            "terms": _terms(e, term_lookup),
            "business_attributes": e.get("businessAttributes") or {},
        }

    asset = base(entity)
    asset["columns"] = []
    for ref in (entity.get("relationshipAttributes") or {}).get("columns") or []:
        col = column_entities.get(ref["guid"])
        if not _is_active(ref) or col is None or col.get("status", "ACTIVE") != "ACTIVE":
            continue
        c = base(col)
        c["data_type"] = (col.get("attributes") or {}).get("data_type")
        asset["columns"].append(c)
    asset["columns"].sort(key=lambda c: (c["name"] or "").lower())
    return asset


def extract_assets(client: PurviewClient, cfg: PurviewConfig) -> list[dict]:
    hits = client.search(cfg.entity_types, cfg.collection_ids)
    guids = sorted({h["id"] for h in hits})
    log.info("Purview: %d assets encontrados na busca", len(guids))
    tables = client.get_entities(guids, cfg.bulk_size, cfg.max_workers)

    column_guids = sorted(
        {
            ref["guid"]
            for t in tables.values()
            for ref in (t.get("relationshipAttributes") or {}).get("columns") or []
            if _is_active(ref)
        }
    )
    # Segunda leitura: as cópias em referredEntities não trazem meanings (termos) das colunas.
    columns = client.get_entities(column_guids, cfg.column_bulk_size, cfg.max_workers)

    assets = [
        normalize_entity(t, columns, client.get_term, cfg.include_propagated_classifications)
        for t in tables.values()
        if t.get("status", "ACTIVE") == "ACTIVE"
    ]
    return sorted(assets, key=lambda a: a["qualified_name"] or "")
