"""Executa um arquivo .sql statement a statement num SQL warehouse via Databricks CLI.

Uso: python3 demo/run_sql_file.py demo/seed_uc.sql --profile <profile> --warehouse-id <id>
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys


def split_statements(text: str) -> list[str]:
    text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in re.split(r";\s*(?:\n|$)", text) if s.strip()]


def run(statement: str, profile: str, warehouse_id: str) -> dict:
    body = json.dumps({"warehouse_id": warehouse_id, "statement": statement, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    out = subprocess.run(["databricks", "api", "post", "/api/2.0/sql/statements", "--profile", profile, "--json", body],
                         check=True, capture_output=True, text=True).stdout
    resp = json.loads(out)
    while resp["status"]["state"] in ("PENDING", "RUNNING"):
        out = subprocess.run(["databricks", "api", "get", f"/api/2.0/sql/statements/{resp['statement_id']}", "--profile", profile],
                             check=True, capture_output=True, text=True).stdout
        resp = json.loads(out)
    return resp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sql_file")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--warehouse-id", required=True)
    args = ap.parse_args()
    failed = 0
    for stmt in split_statements(open(args.sql_file, encoding="utf-8").read()):
        resp = run(stmt, args.profile, args.warehouse_id)
        state = resp["status"]["state"]
        first = stmt.splitlines()[0][:90]
        if state == "SUCCEEDED":
            print(f"OK    {first}")
        else:
            failed += 1
            print(f"ERRO  {first}\n      {resp['status'].get('error', {}).get('message', state)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
