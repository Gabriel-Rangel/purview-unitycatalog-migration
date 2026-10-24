"""Bearer tokens for the Purview data plane.

The recommended method is a Unity Catalog *service credential* backed by an Access Connector
managed identity: no secret to store or rotate, and access is governed by UC grants.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time

from .config import AuthConfig

PURVIEW_SCOPE = "https://purview.azure.net/.default"
_REFRESH_MARGIN_S = 300


class TokenProvider:
    """Callable that returns a valid token, refreshing it ~5 min before expiry. Thread-safe."""

    def __init__(self, fetch):
        self._fetch = fetch  # () -> (token, expires_on_epoch)
        self._token = None
        self._expires_on = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            if self._token is None or time.time() > self._expires_on - _REFRESH_MARGIN_S:
                self._token, self._expires_on = self._fetch()
            return self._token


def _from_azure_credential(credential) -> TokenProvider:
    def fetch():
        access = credential.get_token(PURVIEW_SCOPE)
        return access.token, float(access.expires_on)

    return TokenProvider(fetch)


def _from_azure_cli() -> TokenProvider:
    def fetch():
        out = subprocess.run(
            ["az", "account", "get-access-token", "--resource", "https://purview.azure.net", "-o", "json"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        data = json.loads(out)
        return data["accessToken"], float(data.get("expires_on") or time.time() + 3000)

    return TokenProvider(fetch)


def get_token_provider(cfg: AuthConfig, dbutils=None) -> TokenProvider:
    if cfg.method == "service_credential":
        if dbutils is None:
            raise RuntimeError("auth.method=service_credential exige rodar dentro do Databricks (dbutils)")
        # Em serverless é obrigatório informar o nome da credencial explicitamente.
        return _from_azure_credential(dbutils.credentials.getServiceCredentialsProvider(cfg.service_credential))

    if cfg.method == "secret_scope":
        if dbutils is None or not cfg.secret_scope:
            raise RuntimeError("auth.method=secret_scope exige dbutils e auth.secret_scope")
        from azure.identity import ClientSecretCredential  # instalado só neste modo

        get = lambda key: dbutils.secrets.get(cfg.secret_scope, key)  # noqa: E731
        return _from_azure_credential(
            ClientSecretCredential(get(cfg.tenant_id_key), get(cfg.client_id_key), get(cfg.client_secret_key))
        )

    if cfg.method == "azure_cli":
        return _from_azure_cli()

    raise ValueError(f"auth.method desconhecido: {cfg.method}")
