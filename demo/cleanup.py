"""Remove os dados de demonstração criados por demo/seed_purview.py (não apaga a conta Purview).

Uso (da raiz do repo, com `az login`):
    python3 demo/cleanup.py --account <conta-purview>

O lado UC é removido com:  DROP CATALOG p2uc_demo CASCADE;
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from purview2uc.auth import _from_azure_cli  # noqa: E402
from purview2uc.purview import PurviewClient  # noqa: E402
from seed_purview import CUSTOM_CLASSIFICATIONS, TERMS, build_entities  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    args = ap.parse_args()
    pv = PurviewClient(f"https://{args.account}.purview.azure.com", _from_azure_cli())

    entities, _ = build_entities()
    guids = []
    for e in entities:
        try:
            found = pv.request("GET", f"/datamap/api/atlas/v2/entity/uniqueAttribute/type/{e['typeName']}",
                               params=[("attr:qualifiedName", e["attributes"]["qualifiedName"])])
            guids.append(found["entity"]["guid"])
        except RuntimeError:
            pass  # já removida
    for i in range(0, len(guids), 50):
        pv.request("DELETE", "/datamap/api/atlas/v2/entity/bulk", params=[("guid", g) for g in guids[i : i + 50]])
    print(f"entidades removidas: {len(guids)}")

    for glossary in pv.request("GET", "/datamap/api/atlas/v2/glossary") or []:
        for term in pv.request("GET", f"/datamap/api/atlas/v2/glossary/{glossary['guid']}/terms", params=[("limit", "1000")]) or []:
            if term["name"] in TERMS:
                pv.request("DELETE", f"/datamap/api/atlas/v2/glossary/term/{term['guid']}", ok_status=(200, 204))
                print(f"termo removido: {term['name']}")

    for name in CUSTOM_CLASSIFICATIONS:
        try:
            pv.request("DELETE", f"/datamap/api/atlas/v2/types/typedef/name/{name}", ok_status=(200, 204))
            print(f"classificação removida: {name}")
        except RuntimeError as e:
            print(f"classificação {name} não removida (ainda referenciada por entidades excluídas?): {e}")


if __name__ == "__main__":
    main()
