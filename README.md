# Описание

## Интерфейс бота
Основной фреймворк - aiogram. Переключение экранов должно происходить через изменение сообщения, не добавление нового, там где это возможно. Есть библиотека для питона чтобы создавать такие интерфейсы, но как она называется я забыл.
Написание даты и времени от пользователя должно быть гибким. Бот должен уметь читать форматы ММ ДД, ММ:ДД, ММ/ДД, ЧЧ ММ, ЧЧ:ММ и подобное. При написании даты по умолчанию не требуется год, если получающаяся дата оказывается в прошлом - то бот добавляет год и предупреждает об этом пользователя.

## Архитектура

### Authentication implementation

Accounts share the `user` table. Telegram-only accounts have nullable email/password
and a unique 64-bit `telegram_user_id`; `telegram_private_chat_id` is delivery routing,
not identity. Password registration still requires email and password. Telegram-only
accounts cannot use password login; adding credentials or merging accounts is not supported.

The Telegram-identity migration refuses downgrade while any account lacks credentials
or retains a Telegram binding. Export/migrate affected accounts and explicitly clear
bindings before retrying. Downgrade never deletes accounts or invents credentials.

New teacher preferences default to 09:00–18:00, ISO weekdays 1–5, 60-minute
lessons, 5-minute breaks, 4 consecutive lessons, and 6 lessons/day. Existing
work-hour/break settings are not overwritten by migrations. Invitation codes
are unique and deep-link-safe; duplicate/invalid existing codes stop migration
for explicit remediation.

Notification units: daily digest = local minutes since midnight (0–1439),
lesson reminder = minutes before lesson (1–600), homework = positive days
before lesson; null disables any notification. Telegram onboarding defaults
are 540 / 15 / 2. The owner-approved onboarding migration resets existing
notification preferences to these values; downgrade removes new schema fields
but does not restore the former notification values. Preferences are saved only:
notification delivery and scheduling enforcement are not implemented here.

Profile PUT endpoints use partial-update semantics: omitted fields preserve saved
values and invitation codes; explicit null disables notification preferences only.
Teacher preferences are validated against the merged saved/requested state. API
work times use `HH:MM`; lesson-reminder UI must explicitly ask for hours or minutes
and convert the answer to backend minutes.

All teacher/student profile create/read/update routes require the profile owner
or an existing superuser. Teacher role alone does not grant access to other users'
profiles. `/api/v1/auth/me` includes role, teacher linkage, timezone and digest
preference; PATCH accepts only self-service fields. Public password registration
accepts TEACHER/STUDENT, not ADMIN or privilege flags.

The trusted adapter uses `POST /api/v1/telegram/session` with JSON
`{"telegram_user_id": 5000000000, "telegram_private_chat_id": 5000000000}`.
Configure backend `TELEGRAM_SERVICE_CREDENTIAL` to an independently generated
secret of at least 32 characters, and send it in `X-Bot-Service-Key`.
Never reuse the Bot API token, JWT signing key, or a user JWT as this credential.
Missing configuration disables adapter access (503); absent/wrong keys return 401.
Unknown senders return 404 with `detail: telegram_identity_not_found`; inactive
accounts return 400. Success returns `access_token` and `token_type: bearer`,
usable with existing user endpoints.

Use HTTPS between servers. The backend verifies the bot credential, not the
Telegram update itself: the bot must derive identity from the real update sender,
never callback data or user input. Never log credentials, tokens, or full payloads.

`POST /api/v1/telegram/register` requires the same service header. Teacher example:
```json
{
  "telegram_user_id": 5000000000,
  "telegram_private_chat_id": 5000000000,
  "role": "TEACHER",
  "timezone": "Asia/Bangkok",
  "full_name": "Example Teacher",
  "notification_lessons_today": 540,
  "profile": {
    "work_start": "09:00",
    "work_end": "18:00",
    "working_days": [1, 2, 3, 4, 5],
    "lesson_length": 60,
    "between_lessons_break": 5,
    "max_consecutive_lessons": 4,
    "max_lessons_per_day": 6
  }
}
```
Student input uses `role: STUDENT`, required `teacher_code`, and an optional
`profile` containing `notification_lesson` (default 15) and
`notification_homework` (default 2). Both roles require an explicit valid IANA
timezone. Omitted profiles use defaults; explicit null notification values disable
them. Do not send email/password, backend IDs, active/superuser flags, or teacher codes
inside a teacher profile.

Registration returns 201 and the normal token shape only after commit. Repeat or
concurrent registration returns 409 with `detail: telegram_already_registered`;
recover uncertain/lost responses through `/session`, not blind registration retries.
Read `/auth/me` with the JWT, then the owner's teacher/student profile. Teacher
profiles expose the server-generated invitation code, formatted as `t_` plus
six uppercase ASCII letters or digits. Student code entry and backend code
resolution use the complete displayed value, including `t_`.

`POST /api/v1/telegram/teacher-code/resolve` accepts `{"teacher_code": "CODE"}`
and returns only `teacher_id` / `full_name` for confirmation. Unknown, inactive
or non-teacher invitations return 404 with `detail: teacher_code_not_found`.
Registration checks the invitation again; bad settings return 422 and cannot
leave a partial account. Existing accounts are never reassigned by registration.

Apply schema changes using `uv run alembic upgrade head` before using these routes.
For local manual checks, run `uv run fastapi dev backend/main.py` and use `/docs`;
authorize Telegram routes with the service key and user routes with a user JWT.
The bot is a separate process: `uv run python -m telegram`. It uses aiogram 3 /
aiogram-dialog 2, private chats, in-memory drafts, per-user update isolation,
and inline editing. Initial screens send a message; normal callback/text input
transitions edit it. A deleted/uneditable screen permits one replacement.
Other Telegram errors are not converted into new sends.

Bot settings use `BOT_TOKEN`, `BOT_BACKEND_URL` and `BOT_SERVICE_CREDENTIAL`;
the last must match backend `TELEGRAM_SERVICE_CREDENTIAL`. Incomplete drafts
restart after process restart; completed accounts remain in the backend.
Run a single polling process per Bot API token.

### Implemented Telegram flows and limits

In a private chat, `/start` opens the saved role-specific home for existing
accounts; unknown senders choose teacher or student registration. Both wizards
require explicit timezone confirmation, show defaults, and support Back,
Cancel, and review edits before submitting. Review setting choices are shown
one per row. Invalid input edits the active prompt and preserves the draft.

Teacher signup returns a server-generated code matching `^t_[A-Z0-9]{6}$` and
an invitation such as `https://t.me/<bot_username>?start=t_ABC123`. Telegram
delivers this as `/start t_ABC123`. Returning teachers see the current profile
code and invitation on each `/start`.
Unknown invited students confirm the teacher; students without a link enter
the code manually. Lesson reminders explicitly offer hours or minutes and
store canonical minutes. Existing accounts opening an invitation retain their
role and teacher.

Existing teacher codes can be explicitly replaced after deployment with
`uv run python -m backend.auth.teacher_code_rotation --dry-run`, then
`uv run python -m backend.auth.teacher_code_rotation --apply`. Apply previews
the affected count and requires typing the matching `ROTATE <count>` phrase.
It changes only nonconforming codes in one transaction; existing shared links
for replaced codes stop working and no aliases are retained. Do not run apply
without owner approval and a backup.

Backend outages show an unavailable/retry screen, never offline login or
successful local signup. If a registration response is lost, the bot checks
the backend session before resubmitting. Notification preferences and teacher
limits are persisted only: notification delivery, scheduling enforcement,
schedule viewing/offline cache, homework actions, account linking/transfers,
and post-onboarding settings UI remain unimplemented.

Verification: `uv run pytest -q tests` covers simulated Telegram updates with
recorded Bot API calls, the real ASGI backend, and an isolated SQLite database,
including password/scheduling smoke behavior. Real teacher signup, invitation
student signup (including an hours-based reminder), and returning `/start`
were also manually confirmed. Disposable SQLite/PostgreSQL migration checks
and image/Compose configuration checks passed; a full Compose stack startup
and separate-server HTTPS deployment have not been exercised.

### Running backend and bot

Copy `.env.example` to the ignored `.env` and replace every placeholder.
Generate separate random signing/service secrets (for example `openssl rand -hex 32`).
Get the bot token from BotFather. For local processes, `BOT_SERVICE_CREDENTIAL`
must equal `TELEGRAM_SERVICE_CREDENTIAL`.

```shell
uv sync
uv run alembic upgrade head
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8093
# In a separate terminal:
uv run python -m telegram
```

Local Compose (PostgreSQL + backend + opt-in bot):
```shell
docker compose --profile bot up --build
```
Production Compose:
```shell
docker compose -f docker-compose.prod.yml --profile bot up -d --build
```
To use another interpolation file, add `--env-file .env.docker`. Compose passes
only explicitly listed settings to each service: only backend gets the database
URL/JWT signing secret, only bot gets `BOT_TOKEN`, and both get the service
credential. `.env` is excluded from image builds and is never copied into images.
The bot has no database volume/access and runs no migrations; it can start while
the backend is unavailable. Send `/start` to retry after recovery.

For separate servers, set `BOT_BACKEND_URL=https://your-backend.example` for a
local bot process, or `BOT_COMPOSE_BACKEND_URL=https://your-backend.example` for
the bot container. Start only the bot remotely with `docker compose --profile bot
up -d --no-deps bot`; it does not require the backend/database containers locally.
Plain HTTP is allowed only for localhost or the private Compose `app` hostname.

Keep exactly one poller per bot token across machines/containers. Stop with Ctrl+C
or `docker compose stop bot` before launching a replacement; do not scale the bot.
Drafts and JWT cache are memory-only. After restart, `/start` verifies completed
accounts against the backend; incomplete wizards restart. To rotate the service
credential, generate a new value, update both services and restart both; an
intermediate mismatch returns authentication errors, not offline success.

Интерфейс бота является отдельным сервисом от бекенда. Он должен иметь свой кеш для read only операций. В случае, если бекенд недоступен пользователь все еще должен иметь возможность посмотреть расписание уроков. Восстановление соединения должно идти по паттерну circuit breaker. Пердполагается, что телеграм бот и бекенд запускаются на разных серверах, но для разработки и дебага должна быть возможность запустить их на одной машине. Предполагается, что в будущем будут добавляться новые интерфейсы - фронтенд, другие мессенджеры, интеграции с календарями. Бекенд должен быть универсальным и единственной точкой истины.

## Фронтенд
В случае если до фронтенда дойдет, первые версии нужно делать без использования каких то больших фреймворков типо react. Из шаблона проектов есть начальные файлы в templates - используется htmx. Этого должно быть достаточно на первое время.

## Старт

Тут описан сценарий регистрации когда пользователь впервые заходит в бота.

Команда /start - бот предлагает регистрацию, 2 кнопки - как учитель, как ученик.
В Telegram ссылка-приглашение открывает `/start t_ABC123` и регистрацию ученика под конкретного учителя.

Регистрация учителя:
Бот предлагает настроить:
Часовой пояс
Начало и конец рабочего дня
Рабочие дни
Длительноть урока по умолчанию
Перерыв между уроками
Максимальное количество уроков подряд
Максимальное количество уроков в день
Уведомления о количестве уроков сегодня

Регистрация ученика:
Без кода - просит ввести код учителя
Бот предлагает настроить:
Часовой пояс
Уведомления о количестве уроков сегодня
Уведомления перед уроком
Уведомления о домашних заданиях

Про эти настройки:
Часовой пояс - индивидуален для каждого пользователя, часовой пояс пользователя никак не должен влиять на расписание в БД, т.к. у БД свой часовой пояс. Пользователь же должен всегда видеть даты и время в том поясе, который он выбрал, во всех интерфейсах, всегда.
Начало и конец рабочего дня - настройка учителя. Ученики не могут добавить урок вне рабочего дня его учителя. По умолчанию - 9 утра и 6 вечера
Рабочие дни - настройка учителя. Учитель может выбрать дни недели, в которые он работает. Ученики не могут добавить урок в нерабочие дни. По умолчанию рабочие дни с понедельника по пятницу.
Длительноть урока по умолчанию - настройка учителя. При создании урока он создается с длительностью этой настройки. Поменять длительность урока может только учитель. По умолчанию - 1 час.
Перерыв между уроками - настройка учителя. Если = 0, то никаких перерывов не добавляется. Эта настройка автоматически добавляет перерыв после каждого поставленного урока у учителя и удаляет его при удалении. Технически, это может хранится в базе как отдельное поле, означающее длительность перерыва после урока, или как отдельная строка в таблице ивентов. По умолчанию 5 минут.
Максимальное количество уроков подряд - настройка учителя. Считается, что уроки стоят подряд, если один идет сразу за другим, или между ними стоит только перерыв по настройке "Перерыв между уроками". Ученик не может поставить урок так, чтобы цепочка уроков подряд превышала эту настройку. По умолчанию 4.
Максимальное количество уроков в день - настройка учителя. Запрещает ученику ставить урок в день, когда уже запланировано количество уроков, указанное в этой настройке. По умолчанию - 6.
Уведомления о количестве уроков сегодня - сообщает пользователю о расписании на день. Если пользователь учитель - добавляет имя ученика напротив каждого урока. Можно настроить время, когда это уведомление приходит. По умолчанию - 9 утра.
Уведомления перед уроком - настройка ученика. Присылает уведомление о том, что скоро начнется урок. Можно настроить за сколько времени до начала урока придет уведомление. Максимум - 10 часов. По умолчанию - 15 минут.
Уведомления о домашних заданиях - настройка ученика. Присылает уведомление о том, что через несколько дней будет урок, к которому не сделана домашняя работа. Домашняя работа считается сделанной если было прислано выполнение. По умолчанию - 2.

## Просмотр расписания
Учитель:
Может посмотреть расписание на сегодня - по порядку отображаются все события у всех учеников учителя. Если у ученика есть каникулы - его уроки не отображаются. Если каникулы или выходной у учителя - ничего не отображается. Отображается "линия времени - разделитель, который указывает, какие события уже прошли, какие еще нет.
Может посмотреть расписание на неделю - аналогично, только в одном сообщении все дни на неделю. Неделя идет всегда с понедельника вне зависимости от текущей даты.

Ученик:
Логика аналогична учителю, за исключением, что отображаются только события ученика и нет подписи кому проводится урок.

## Изменение расписания
Еженедельные события:
Добавление:
Ученик может добавит себе урок на любое свободное место в расписании. Свободным считается любое время без событий у учителя в день, когда нету выходных или каникул у ученика и учителя. При этом если в будущем есть уже события на такой же период - они имеют более высокий приоритет, включая другие постоянные уроки. События учителя имеют более высокий приоритет над любыми событиями ученика, кроме каникул ученика. Каникулы имеют более высокий приоритет чем уроки. 
Учитель может добавить урок для любого из своих учеников или для себя. Учитель может добавлять еженедельные перерывы.
Изменение:
Ученик может изменять/удалять только свои события. Если урок переносится, свободные слоты считаются также, как для создания нового урока.
Учитель может менять события любых своих учеников.

Одноразовые события:
Добавление:
Ученик может создать каникулы - они идут с до определённой даты.
Учитель также может добавлять каникулы. Учитель может добавлять перерывы.
Изменение:
Аналогично еженедельным событиям.

## Уведомления
Утреннее уведомление "уроки на сегодня":
Приходит только если сегодня есть какие либо уроки. В уведомлении содержится информация из расписания на сегодня, у ученика дополнительно информация о невыполненном дз, если такое имеется. Можно включить и выключить такое уведомление, настроить во сколько оно приходит.

Уведомление "скоро урок":
Приходит только ученику. Приходит перед уроком. Можно включить и выключить, настроить за сколько времени до урока должно приходить.

Уведомление от учителя:
Учитель может разослать всем своим ученикам разовое уведомление.

Уведомление о домашнем задании: приходит ученику когда учитель создает дз. Учителю приходит уведомление когда ученик загружает решение. Оба можно выключить.

## Домашние задания
Учитель может дать ученику домашнее задание: для этого нужно загрузить файлы или просто написать сообщение.
Ученик может скачать материалы дз, загрузить решение. После загрузки ДЗ помечается выполненным. Учитель может пометить дз выполненным или удалить его вообще. Учитель может посмотреть выполнение дз в прикреплённых материалах.

## Профиль ученика
Доступ только у учителя. Предоставляет информацию о:
юзернейме и нике ученика
дате добавления
статусе выполнения домашних работ
истории последних 10 действий
кнопку удаления ученика (с подтверждением)
