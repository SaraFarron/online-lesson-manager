#!/bin/bash
set -e

git pull

./scripts/backup.sh

docker compose down
docker compose up -d --build
