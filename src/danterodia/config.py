"""Configuración.

Orden de prioridad (de más a menos): argumentos de la CLI > variables de entorno ``DANTERODIA_*``
> fichero ``.env`` del directorio actual > valores por defecto.

Las claves de API **nunca** van en el código: se leen de ``.env`` o del entorno y se guardan
como ``SecretStr`` para que no aparezcan en logs ni en ``danterodia config``.
"""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal

from platformdirs import user_data_dir
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from danterodia import __version__

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def _default_data_dir() -> Path:
    return Path(user_data_dir("danterodia", appauthor=False))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DANTERODIA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- rutas
    data_dir: Path = Field(default_factory=_default_data_dir)
    """Carpeta de trabajo: base de datos, caché y casos."""

    # --- red
    user_agent: str = f"danterodia/{__version__} (+https://github.com/Quantika14/osint-suite-tools)"
    timeout: float = Field(default=20.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    backoff_base: float = Field(default=1.0, ge=0)
    per_domain_concurrency: int = Field(default=2, ge=1)
    per_domain_interval: float = Field(default=1.0, ge=0)
    """Segundos mínimos entre dos peticiones al mismo dominio."""
    max_concurrency: int = Field(default=10, ge=1)
    max_response_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    proxy: str | None = None
    """Proxy HTTP/SOCKS para todo el tráfico, p. ej. ``socks5://127.0.0.1:1080``."""
    tor_proxy: str = "socks5://127.0.0.1:9050"
    """Proxy de Tor. Solo se usa para direcciones ``.onion``."""
    allow_private_networks: bool = False
    """Permitir peticiones a IPs privadas o locales (protección anti-SSRF)."""

    # --- caché
    cache_enabled: bool = True
    cache_ttl: int = Field(default=24 * 3600, ge=0)

    # --- investigación
    source_timeout: float = Field(default=120.0, gt=0)
    max_pivot_entities: int = Field(default=50, ge=1)
    enabled_sources: Annotated[list[str] | None, NoDecode] = None
    """Si se indica, solo se usan estas fuentes."""
    disabled_sources: Annotated[list[str], NoDecode] = Field(default_factory=list)
    include_sensitive: bool = False
    """Activa las fuentes con datos penales o de categoría especial (art. 9 y 10 RGPD)."""
    case_retention_days: int = Field(default=90, ge=1)

    # --- logging
    log_level: LogLevel = "WARNING"
    """Nivel de la consola. El log de cada caso (JSONL) siempre guarda todo el detalle."""

    # --- claves de API (todas opcionales)
    hibp_api_key: SecretStr | None = None
    emailrep_api_key: SecretStr | None = None
    brave_api_key: SecretStr | None = None
    google_cse_key: SecretStr | None = None
    google_cse_cx: str | None = None
    mojeek_api_key: SecretStr | None = None
    youtube_api_key: SecretStr | None = None
    factcheck_api_key: SecretStr | None = None
    shodan_api_key: SecretStr | None = None
    censys_api_id: SecretStr | None = None
    censys_api_secret: SecretStr | None = None
    intelx_api_key: SecretStr | None = None
    librebor_api_key: SecretStr | None = None
    ipinfo_token: SecretStr | None = None

    # --- almacenamiento
    database_url: str | None = None
    """URL de SQLAlchemy. Por defecto, SQLite en ``data_dir/danterodia.db``."""

    @field_validator("enabled_sources", "disabled_sources", mode="before")
    @classmethod
    def _lista_csv(cls, v: object) -> object:
        """Acepta ``DANTERODIA_DISABLED_SOURCES=buscadores,prensa`` y también una lista JSON."""
        if isinstance(v, str):
            if v.strip().startswith("["):
                return json.loads(v)
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @cached_property
    def cases_dir(self) -> Path:
        return self.data_dir / "casos"

    @cached_property
    def cache_path(self) -> Path:
        return self.data_dir / "cache.sqlite"

    @cached_property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'danterodia.db').as_posix()}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cases_dir.mkdir(parents=True, exist_ok=True)

    def api_key(self, name: str) -> str | None:
        """Valor de una clave por nombre de campo (``None`` si no está configurada o está vacía)."""
        value = getattr(self, name, None)
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        return value or None

    def public_dump(self) -> dict[str, object]:
        """Configuración sin secretos, para mostrarla al usuario."""
        result: dict[str, object] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, SecretStr):
                result[name] = "configurada" if value.get_secret_value() else "vacía"
            elif value is None and name.endswith(("_key", "_token", "_secret", "_id")):
                result[name] = "no configurada"
            else:
                result[name] = value
        return result
