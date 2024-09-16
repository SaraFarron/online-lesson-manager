![Update Online Lesson Manager](https://cronitor.io/badges/AJxpBZ/production/iKtBzEGJE15yW8sdZbfxxqvaD00.svg)

Запуск
docker compose up -d --build

Без докера
poetry run python src/main.py

.env

PYTHONPATH=/YOUR_PATH/online-lesson-manager/src/
BOT_TOKEN=

Создать миграцию
alembic revision --autogenerate -m '...'

Применить миграцию
alembic upgrade head

Миграции на проде

docker compose exec bot sh
PYTHONPATH=./src poetry run alembic upgrade head
