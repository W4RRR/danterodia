# syntax=docker/dockerfile:1
#
# Imagen de osintsuite en dos etapas:
#   1. «build»: instala las dependencias EXACTAS de uv.lock en un entorno virtual.
#   2. final: solo copia ese entorno a una imagen limpia y ejecuta como usuario sin privilegios.
#
# Uso:  docker build -t osintsuite .
#       docker run --rm -v "$PWD/datos:/datos" osintsuite dominio example.com -f "Mi finalidad"

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_FROZEN=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Primero solo las dependencias: esta capa se reutiliza mientras no cambie uv.lock.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv sync --no-dev --extra mongo --no-install-project
COPY src ./src
RUN uv sync --no-dev --extra mongo --no-editable

FROM python:${PYTHON_VERSION}-slim
LABEL org.opencontainers.image.title="osintsuite" \
      org.opencontainers.image.description="OSINT con fuentes españolas (basado en Dante's Gates de Quantika14)" \
      org.opencontainers.image.licenses="GPL-3.0-only" \
      org.opencontainers.image.source="https://github.com/Quantika14/osint-suite-tools"
RUN useradd --create-home --uid 10001 osint \
    && mkdir -p /datos /trabajo \
    && chown osint:osint /datos /trabajo
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OSINT_DATA_DIR=/datos
USER osint
WORKDIR /trabajo
VOLUME ["/datos"]
ENTRYPOINT ["osint"]
CMD ["--help"]
