FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Optional extras can be enabled at build time, e.g.:
# docker build --build-arg INSTALL_EXTRAS="[providers,telegram]" -t closeclaw .
ARG INSTALL_EXTRAS=""

COPY pyproject.toml README.md README_zh.md ./
COPY closeclaw ./closeclaw

RUN pip install --upgrade pip && \
    if [ -n "$INSTALL_EXTRAS" ]; then pip install ".${INSTALL_EXTRAS}"; else pip install .; fi

# Runtime directories typically mounted by docker-compose.
RUN mkdir -p /workspace /runtime-data && \
    addgroup --system closeclaw && \
    adduser --system --ingroup closeclaw closeclaw && \
    chown -R closeclaw:closeclaw /app /workspace /runtime-data

USER closeclaw

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD closeclaw --help > /dev/null || exit 1

ENTRYPOINT ["closeclaw"]
CMD ["--help"]
