#!/bin/sh
# Runs before every container command. Applies migrations unless
# DJANGO_MIGRATE=0 (set that for one-off commands like tests or shells).
set -e

if [ "${DJANGO_MIGRATE:-1}" != "0" ]; then
  python manage.py migrate --noinput
fi

exec "$@"
