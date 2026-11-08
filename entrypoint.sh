#!/bin/sh
set -e
# Crée les tables si elles n'existent pas (sans effet si elles existent déjà)
flask init-db
# Lance la commande du conteneur (gunicorn)
exec "$@"
