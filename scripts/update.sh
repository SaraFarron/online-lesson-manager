#!/bin/bash

git pull
docker compose stop
cp db/db.sqlite backups/"$(date +"%Y-%m-%d %T" )"_db.sqlite
docker compose up -d --build
