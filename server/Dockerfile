FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc AS web
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git unzip xz-utils \
    && git clone --depth 1 --branch 3.47.0 https://github.com/flutter/flutter.git /opt/flutter
ENV PATH="/opt/flutter/bin:${PATH}"
RUN git config --global --add safe.directory /opt/flutter && flutter precache --web
WORKDIR /app
COPY app/pubspec.yaml app/pubspec.lock ./
RUN flutter pub get
COPY app/ ./
# The dashboard must keep working on a network with no internet, so nothing can come from a CDN.
RUN flutter build web --release --no-web-resources-cdn

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PYTHONUNBUFFERED=1 WHISKERLESS_NO_UPDATE_CHECK=1 PIP_ROOT_USER_ACTION=ignore PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
COPY server/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY server/pyproject.toml ./
COPY server/src ./src
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
COPY --from=web /app/build/web /srv/web
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"]
CMD ["python", "-m", "whisker_local"]
