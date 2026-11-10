ARG PYTHON_UV_IMAGE
FROM ${PYTHON_UV_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1 \
    PATH=/opt/venv/bin:$PATH

# Application imports include the OCR and ML modules even for tag API tests.
# Keep the full locked dependency set, rather than stubbing those imports.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config libpq-dev libmagic1 libxml2 libxslt1.1 \
    zlib1g libgomp1 ghostscript tesseract-ocr unpaper poppler-utils qpdf \
    gnupg file fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY sources/paperless-ngx/pyproject.toml sources/paperless-ngx/uv.lock sources/paperless-ngx/README.md ./
RUN uv sync --frozen --no-default-groups --group testing --extra postgres \
    --python /usr/local/bin/python
COPY sources/paperless-ngx/src ./src
COPY prototype/paperless /harness
RUN mkdir -p /opt/reversedb-env /state/data /state/media /state/consume /state/scratch \
    && python --version > /opt/reversedb-env/python.txt \
    && uv --version > /opt/reversedb-env/uv.txt \
    && uv pip freeze --python /opt/venv/bin/python > /opt/reversedb-env/python-packages.txt \
    && dpkg-query -W > /opt/reversedb-env/system-packages.txt
CMD ["sh", "/harness/run_suite.sh"]
