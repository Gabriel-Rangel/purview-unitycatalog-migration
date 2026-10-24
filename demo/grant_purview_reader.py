"""Dá o papel Data Reader (purview-reader) a uma identidade numa collection do Purview.

Equivale a Purview portal > Data Map > Collections > <collection> > Role assignments > Data readers.
Use o *principal/object id* da managed identity do Access Connector (não o client id):
    az resource show --ids <access-connector-id> --query identity.principalId -o tsv

Uso (da raiz do repo, com `az login` e papel Collection Admin na collection):
    python3 demo/grant_purview_reader.py --account <conta> --principal-id <object-id> [--collection <nome>]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from purview2uc.auth import _from_azure_cli  # noqa: E402

import requests  # noqa: E402

API = "api-version=2021-07-01-preview"
ROLE = "purviewmetadatarole_builtin_purview-reader"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--principal-id", required=True)
    ap.add_argument("--collection", help="nome técnico da collection (padrão: raiz = nome da conta)")
    args = ap.parse_args()
    collection = args.collection or args.account
    base = f"https://{args.account}.purview.azure.com/policystore/metadataPolicies"
    headers = {"Authorization": f"Bearer {_from_azure_cli()()}"}

    policies = requests.get(f"{base}?{API}", headers=headers, timeout=60).json()["values"]
    policy = next(p for p in policies if p["properties"]["collection"]["referenceName"] == collection)
    # Busca o documento completo logo antes do PUT: o PUT substitui a policy inteira.
    policy = requests.get(f"{base}/{policy['id']}?{API}", headers=headers, timeout=60).json()

    rule_id = f"{ROLE}:{collection}"
    rules = policy["properties"]["attributeRules"]
    rule = next((r for r in rules if r["id"] == rule_id), None)
    if rule is None:
        rule = {"kind": "attributerule", "id": rule_id, "name": rule_id, "dnfCondition": [[
            {"attributeName": "principal.microsoft.id", "attributeValueIncludedIn": []},
            {"attributeName": "derived.purview.role", "attributeValueIncludes": ROLE},
        ]]}
        rules.append(rule)
        permission = next(r for r in rules if r["id"] == f"permission:{collection}")
        permission["dnfCondition"].append([{"attributeName": "derived.purview.permission", "attributeValueIncludes": rule_id}])

    principals = next(c for c in rule["dnfCondition"][0] if c["attributeName"] == "principal.microsoft.id")["attributeValueIncludedIn"]
    if args.principal_id in principals:
        print(f"{args.principal_id} já é Data Reader em '{collection}'")
        return
    principals.append(args.principal_id)
    resp = requests.put(f"{base}/{policy['id']}?{API}", headers=headers, json=policy, timeout=60)
    resp.raise_for_status()
    print(f"{args.principal_id} agora é Data Reader em '{collection}' (policy versão {resp.json().get('version')})")


if __name__ == "__main__":
    main()
