# Online Lesson Manager
A telegram bot for managing schedule for online lessons.
It is a personal project that I do for my wife, who is an online teacher.

## Run
### Docker
docker compose up -d --build
### Poetry
poetry run python src/main.py

## .env
```shell
PYTHONPATH=python executable, might be deprecated
BOT_TOKEN=telegram bot token from botfather
BOT_TOKEN_PROD=might be deprecated

IRINA_TG_ID=telegram id of 1st admin, might be deprecated
SARA_TG_ID=telegram id of 2nd admin, might be deprecated

```
## Migrations
Everything is done through default alembic
```shell
alembic revision --autogenerate -m '...'

alembic upgrade head
```
Migrations in production
```shell
docker compose exec bot sh
PYTHONPATH=./src poetry run alembic upgrade head
```
