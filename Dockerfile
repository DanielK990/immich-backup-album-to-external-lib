FROM python:3.12-alpine

WORKDIR /app
COPY immich-backup-albums-to-external-lib.py requirements.txt ./
COPY templates ./templates
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
RUN chmod -R a+rX /app && chmod -R a+rX /app/templates
CMD ["python", "immich-backup-albums-to-external-lib.py"]