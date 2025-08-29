FROM python:3.12-alpine

ARG UID=1000
ARG GID=1000

# Use addgroup/adduser for Alpine
RUN addgroup -g $GID immich && \
    adduser -D -u $UID -G immich immich

WORKDIR /app
COPY immich-backup-albums-to-external-lib.py requirements.txt ./
COPY templates ./templates
RUN chown -R immich:immich /app
USER immich
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
CMD ["python", "immich-backup-albums-to-external-lib.py"]