#!/bin/bash

set -e

BACKUP_DIR="backups"
DB_FILE="db/db.sqlite"

mkdir -p $BACKUP_DIR

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")

cp $DB_FILE $BACKUP_DIR/"${TIMESTAMP}"_db.sqlite

echo "Backup created: ${TIMESTAMP}_db.sqlite"

find backups -type f -mtime +30 -delete
