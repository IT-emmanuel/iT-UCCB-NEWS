FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=app.py

WORKDIR /app

# Utilisateur sans privilèges + dossier des données (images) appartenant à cet utilisateur
RUN useradd --create-home --uid 1000 app \
    && mkdir /data && chown app:app /data

# Les dépendances d'abord : cette couche est mise en cache tant que requirements.txt ne change pas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .
RUN chmod +x entrypoint.sh

USER app
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
CMD ["gunicorn", "-w", "3", "-b", "0.0.0.0:8000", "--access-logfile", "-", "app:app"]
