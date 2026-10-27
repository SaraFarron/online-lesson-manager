# Описание

## Интерфейс бота
Основной фреймворк - aiogram. Переключение экранов должно происходить через изменение сообщения, не добавление нового, там где это возможно. Есть библиотека для питона чтобы создавать такие интерфейсы, но как она называется я забыл.
Даты вводятся в формате ДД ММ, ДД:ММ или ДД/ММ; четырехзначный год необязателен и использует тот же разделитель. Форматы с месяцем перед днем не поддерживаются. Для времени используются ЧЧ ММ или ЧЧ:ММ. Если дата без года уже прошла в текущем часовом поясе, бот предлагает соответствующую дату следующего года и просит подтвердить ее.

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
Before deploying the automatic-break backend, apply migration
`2026-10-25_lesson_post_break_snapshot.py`. It adds a nonnegative
`post_break_minutes` snapshot to one-off and recurring events; existing rows
receive zero and are not backfilled. New lesson writes snapshot the teacher's
current preference, so changing that preference affects future writes only.
The migration refuses downgrade while either table contains a nonzero snapshot
and makes no changes on refusal. A zero-only downgrade removes the snapshot
columns; do not force rollback or discard nonzero values without an explicitly
approved data-remediation plan.
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
the backend session before resubmitting. Teacher workdays/hours, lesson
duration defaults, and daily/consecutive lesson limits are enforced by the
backend. Automatic post-lesson breaks are derived from each lesson's saved
duration snapshot and shown in Telegram booking and management flows.
Validation and backend failures appear at the start of the active Telegram
screen as an escaped **❌ Error** block. Use **⚠️ Attention** for uncertain
write outcomes, accepted input that Telegram could not remove, and committed
writes whose refresh failed. Drafts and recovery controls remain available;
ordinary errors do not create extra messages or callback popups.
Long homework text is paginated in the existing screen without truncation.
Notification delivery, free-slot selection, offline schedule cache, and
account linking/transfers remain unimplemented. Teachers can create text,
file, or mixed assignments for their own students through Telegram and
optionally link an exact upcoming lesson occurrence. Students can browse
authorized assignments, view their text, download materials as separate
Telegram documents, and submit or resubmit text/files; a successful submission
completes the assignment. Teachers can review current and prior submissions,
mark assignments complete, and confirm assignment deletion through Telegram;
uncertain writes retain their retry or status-reconciliation safeguards.
Teachers and students can edit their saved account and role preferences from
Settings in the Telegram role home. Account and role preferences are saved
separately; timezone changes do not move stored event instants, and notification
preferences do not imply delivery. Authenticated day/week schedule reads are
available through the backend and Telegram bot as described below. Shared
temporal conflict checks and vacation/break policies are implemented as
described in the scheduling handoff below.
Telegram date-entry prompts omit timezone labels, but entered dates still use
the viewer's saved IANA timezone; reviews and schedule views retain timezone
context. Schedule weekday abbreviations are capitalized, upcoming rows use
`⏳`, and automatic-break row labels omit `(read-only)` while the derived
reservations remain non-editable. These display changes do not enable
notifications or change schedule status values.

Verification: `uv run pytest -q tests` covers simulated Telegram updates with
recorded Bot API calls, the real ASGI backend, and an isolated SQLite database,
including password/scheduling smoke behavior. Real teacher signup, invitation
student signup (including an hours-based reminder), and returning `/start`
were also manually confirmed. PostgreSQL schedule-mutation tests run when
`SCHEDULE_TEST_DATABASE_URL` points to a disposable PostgreSQL database; each
test creates and removes its own temporary schema. Disposable
SQLite/PostgreSQL migration checks and image/Compose configuration checks
passed; a full Compose stack startup and separate-server HTTPS deployment have
not been exercised.

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

For end-to-end debugging in VS Code, install the Python and Python Debugger
extensions, select the workspace `.venv` interpreter (created by `uv sync`),
and complete the `.env` setup and migration step above. In **Run and Debug**,
select **Backend + Telegram bot** and press **F5**. This launches two debug
sessions with breakpoints supported in both backend and bot code. The backend
runs with reload on port **8000**; the bot debug configuration overrides
`BOT_BACKEND_URL` with `http://127.0.0.1:8000`, regardless of its `.env` value.
Both sessions load credentials from `.env`.

Wait for backend startup and bot polling before sending `/start` in Telegram.
The compound starts the processes concurrently, not in readiness order; if
the bot reports the backend unavailable during startup, retry `/start` after
the backend is ready (the circuit breaker may need its recovery interval).
Use the debug toolbar's **Stop** button (**Shift+F5**) to stop both sessions.
You can also launch **Dev server (reload)** and **Telegram bot (local backend)**
individually; VS Code supports simultaneous debug sessions. Stop any existing
bot poller before starting a debug session with the same token.

Local Compose (PostgreSQL + backend + opt-in bot):
```shell
docker compose --profile bot up --build
```
Production Compose:
```shell
docker compose -f docker-compose.prod.yml --profile bot up -d --build
```
Homework attachments are stored privately by the backend. Set
`HOMEWORK_STORAGE_PATH` for local deployments (default `./homework-storage`);
both Compose manifests mount the persistent `homework_data` volume at the
configured path. Schedule this command daily to remove expired staged uploads
and aged orphan files:
```shell
docker compose run --rm app python -m backend.homework.cleanup
```
Back up the homework volume together with the database. The bot transfers files
through authenticated backend endpoints and does not mount this storage.

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

## Документация проекта и планы

Новые планы релизов, реализации и отдельные task descriptions хранятся в
[docs/plans/](./docs/plans/), а не в корне репозитория. Для создания плана
используйте skill
[plan-feature](./.github/skills/plan-feature/SKILL.md); планы должны отражать
актуальный статус из [AGENTS.md](./AGENTS.md) и требования этого README.
Существующие планы сверены, перенесены в `docs/plans/`, а ссылки обновлены.

Планы передачи по основным направлениям:

1. [Telegram: управление расписанием и настройками](./docs/plans/telegram-scheduling-and-settings-ui.md) — реализовано; проверка и критерии завершения записаны в плане.
2. [Автоматические перерывы после уроков, включая Telegram UI](./docs/plans/automatic-between-lesson-breaks.md) —
   реализовано и проверено на SQLite/PostgreSQL; наследуемые уроки не изменяются автоматически.
3. [Домашние задания: backend и Telegram UI](./docs/plans/homework-backend-and-telegram-ui.md) — реализованы контракт, backend, создание и управление заданиями учителем, а также просмотр/сдача решений учеником в Telegram; осталось сквозное подтверждение хранения, поведения после перезапуска и передача требований для будущих уведомлений.
4. [Сокращение интерфейса Telegram](./docs/plans/telegram-interface-streamlining.md) — завершено и проверено. Этот план заменяет только старую грамматику дат и контракт отображения расписания (UTC-смещения, счетчики страниц, Refresh); он не меняет API, правила расписания или авторизацию.
5. [Ошибки в интерфейсе Telegram](./docs/plans/telegram-error-emphasis.md) — ошибки и неопределённые исходы выделяются в начале текущего экрана; длинный текст заданий разбит на страницы без усечения.

Следующий шаг плана домашних заданий — сквозная проверка и документирование требований для будущих уведомлений; доставка уведомлений остается нереализованной.

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
Перерыв между уроками - настройка учителя. Если = 0, то никаких перерывов
не добавляется. Эта настройка автоматически добавляет перерыв после каждого
поставленного урока у учителя и удаляет его при удалении. В текущей реализации
длительность фиксируется в снимке урока или серии; изменение настройки влияет
только на новые записи, существующие уроки автоматически не изменяются. По
умолчанию 5 минут.
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

### Текущая реализация просмотра

Backend предоставляет авторизованные read-only endpoints:

- `GET /api/v1/schedule/day`
- `GET /api/v1/schedule/week`

Необязательный query-параметр `date=YYYY-MM-DD` задаёт дату в сохранённом
часовом поясе текущего пользователя. Без `date` выбираются сегодняшний день
или текущая неделя в этом часовом поясе. Неделя всегда содержит понедельник —
воскресенье. Даты ответа локальные, а `generated_at`, `range_start`,
`range_end`, `start` и `end` — UTC timestamp-ы с явным offset. В ответе есть
`period`, `timezone` и массив `days`; каждый день содержит `date` и `items`.
Элемент события включает тип и источник, идентификатор источника, полные
`start`/`end` и статус (`past`, `ongoing` или `upcoming`), рассчитанный
относительно единого `generated_at`. Учитель получает подписи учеников у
уроков; ученик получает только собственные видимые события без этих подписей.
Пустые дни недели тоже включены. Событие, пересекающее местную полночь,
показывается только на дате своего местного начала, сохраняя полный интервал.

Доступ определяется JWT и ролью текущего аккаунта, а не параметрами запроса:
учитель видит свой расписанный график, ученик — только собственные события у
связанного активного учителя. Неактивный аккаунт или отсутствие связи ученика
возвращают `400`, отсутствующий/невалидный JWT — `401`, недопустимая роль —
`403`, отсутствующий/неактивный связанный учитель — `409`, некорректная дата,
часовой пояс или данные расписания — `422`.

В Telegram доступны кнопки **Today** и **This week**. В недельном расписании
учитель и ученик могут переходить на предыдущую или следующую неделю целиком;
недели остаются выровнены по понедельникам в часовом поясе пользователя.
Переключение периода, недели и страниц изменяет то же сообщение. В расписании
нет отдельной кнопки Refresh: для повторной загрузки можно вернуться домой и
снова открыть нужный вид либо переключить период/неделю. Заголовки используют
компактный формат вроде `mon, Nov 5`, время снимка показывается кратко, а один
ярлык часового пояса ставится в конце каждой применимой страницы. Пустой Today
показывает дату и `No events` без NOW и часового пояса; в непустом Today NOW
разделяет прошедшие и будущие события. Повторяющийся час при переходе DST
помечается как `first occurrence` или `second occurrence`, а интервал через
местную полночь явно содержит обе даты. Если расписание не помещается, события
распределяются целыми строками по страницам до 3 500 UTF-16 code units; кнопки
Previous/Next появляются только при переполнении, числового счетчика страниц
нет. Подписи длиннее 80 Unicode code points сокращаются и HTML-экранируются.
Это live-запросы к backend: offline кэширования и показа расписания без backend
нет.

Будущие consumers (включая уведомления и кэш) должны сначала авторизовать
просмотр для конкретного пользователя и применять ту же ролевую фильтрацию.
Нельзя выдавать или кэшировать сырые данные всего расписания учителя как
результат для ученика. Правила записи/бронирования остаются отдельной задачей;
read-only просмотр не означает, что все ограничения расписания уже применяются.

## Изменение расписания
В текущем интерфейсе Telegram преподаватель начинает **Add lesson** с выбора
типа урока; по умолчанию урок назначен самому преподавателю и использует
сохраненную длительность. Ученика и другую длительность можно выбрать как
необязательные изменения. Ученик создает урок только для себя, без таких
переопределений. После принятия даты в черновик сообщение с датой удаляется;
ошибочный или отклоненный ввод остается в чате.

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

Проверки пересечений:
- Одноразовые уроки и перерывы не могут пересекаться с другими уроками и перерывами. При создании или переносе одноразового события также проверяются каникулы учителя и соответствующего ученика. Создание или изменение каникул само по себе не удаляет и не перемещает уже запланированные события.
- Для еженедельных событий проверяются все фактические вхождения до даты окончания серии, а не только дата первого события. Каникулы не участвуют в проверке пересечений повторяющихся событий. Новые и изменяемые серии ограничены горизонтом в 10 лет.
- Если перерыв учителя пересекается с уроками, API сначала возвращает `409` со списком затронутых вхождений и ничего не меняет. Клиент должен явно подтвердить действие и повторить запрос с `?confirm_displacement=true`; сервер повторно проверяет расписание и атомарно создает/изменяет перерыв, удаляя одноразовые уроки и добавляя отмены соответствующих вхождений повторяющихся уроков. Удаление перерыва не восстанавливает отмененные уроки.

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

### Передача для будущих уведомлений

Доставка уведомлений о домашних заданиях пока не реализована. При проектировании
отдельного сервиса уведомлений следует учитывать следующие переходы и границы:

- Уведомление ученику о задании может возникать только после успешной публикации
  записи `Homework`; получатель — именно назначенный ученик. Уведомление учителю
  может возникать только после финализации новой версии `HomeworkSubmission`;
  получатель — учитель этого задания.
- Повтор запроса с тем же ключом операции не является новым событием. Будущий
  потребитель должен дедуплицировать публикацию по устойчивому ID задания или
  версии решения и учитывать только зафиксированные переходы состояния.
- Состояние и происхождение завершения хранятся отдельно: `status`,
  `completion_source` (`STUDENT_SUBMISSION` или `TEACHER`) и `completed_at`.
  Отправка решения завершает задание автоматически; ручное завершение учителем
  не означает, что ученик отправлял решение. Удаление задания отзывает доступ к
  нему и его файлам.
- Получателей нужно разрешать по сохранённым ID пользователя и текущей связи
  учитель/ученик; Telegram chat ID служит только адресом доставки. Перед чтением
  связанного урока применяются существующая авторизация расписания и правила
  видимости.
- Связь с уроком — точное вхождение `(kind, source_id, starts_at UTC)`.
  Отмена, перенос или удаление урока не удаляют и не переназначают задание.
  Для будущих напоминаний следует заново проверить активность этого вхождения,
  его отмену и видимость с учётом каникул; неактивное вхождение не должно
  запускать напоминание о предстоящем уроке.
- Существующая настройка `notification_homework` задаёт срок напоминания о
  невыполненном задании перед уроком, но не является переключателем уведомлений
  о создании задания или отправке решения. Эти два переключателя, надёжная
  доставка, повторные попытки и ключи идемпотентности нужно определить в
  отдельном milestone. Существующая таблица `Notification` не является
  реализованным outbox.

## Профиль ученика
Доступ только у учителя. Предоставляет информацию о:
юзернейме и нике ученика
дате добавления
статусе выполнения домашних работ
истории последних 10 действий
кнопку удаления ученика (с подтверждением)

### Scheduling foundation and schedule-viewing handoff

The shared scheduling foundation and authorized backend day/week endpoints
serve the live Telegram schedule views. The endpoint contract and current
Telegram behavior are described in «Текущая реализация просмотра» above.
There is no offline schedule cache.

#### Time and recurrence contract

- Event and cancellation database timestamps are naive UTC. API timestamps with
  an offset are normalized to UTC; an API timestamp without an offset means
  UTC, never server-local time. Public event timestamps carry an explicit UTC
  offset.
- New recurring series use the teacher's IANA timezone captured when the
  series is created. Changing the teacher's display timezone does not move
  events or alter existing series. Legacy series retain their `UTC_STEP`
  recurrence mode; do not reinterpret them using the current teacher timezone.
- Recurrence preserves elapsed duration. `interval_end` is an exclusive
  occurrence-start cutoff, while both event intervals and query ranges are
  half-open. A query includes full occurrences crossing either boundary.
- Teacher-local recurrence preserves wall-clock starts across DST. A
  nonexistent local time shifts forward by the DST gap; an ambiguous local
  time resolves to the earlier instant. User-entered local dates must use the
  viewer's explicit IANA timezone, not the server timezone.
- An occurrence's stable identity is
  `(source kind, source UUID, occurrence start in UTC)`. A cancellation
  identifies one exact generated occurrence by its UTC start and end; it is
  not a partial blackout interval.

#### Open-ended weekly lessons and deployment

- `recurrent_event.interval_end IS NULL` means there is no fixed recurrence
  end. It is allowed only for a weekly lesson (`event_type=LESSON`,
  `interval=7`); one-off events and recurring breaks/vacations remain distinct
  and finite. The value is not a hidden date or a limit on bounded reads.
- Finite `interval_end` remains an exclusive occurrence-start cutoff. In
  Telegram, the teacher's inclusive last lesson date is resolved as the
  following local midnight; clearing the choice stores null. Recurring breaks
  continue to require a finite cutoff.
- Open-ended lesson writes validate generated occurrences only up to the
  exclusive one-calendar-year anniversary of the series start in its
  recurrence timezone. A February 29 anniversary clamps to February 28. This
  validation bound is calculated for the write and is never stored as
  `interval_end`; it does not promise conflict-free scheduling forever. An
  edit to an old series start can therefore validate a wholly historical
  year, and conflicts beyond a new series' validation year are not guaranteed
  to have been discovered. Existing open-ended series are still expanded
  against the bounded ranges requested by later event writes and reads.
- Finite new or updated recurrences retain the existing ten-calendar-year
  maximum. Occurrence queries remain explicitly bounded and subject to their
  occurrence budget; open-ended recurrence does not authorize an unbounded
  scan.
- Deploy in order: apply `uv run alembic upgrade head` (including
  `2026-10-27_open_ended_weekly_lessons.py`), deploy the backend with nullable
  schema/service support, then deploy the compatible Telegram client and
  enable open-ended lesson flows. Older clients may reject null cutoff
  responses. The migration downgrade refuses before changing schema or data
  while any null cutoff exists; assign explicit finite cutoffs through an
  approved data change before retrying. It never invents cutoffs or deletes
  open-ended rows.

#### Internal service handoff

Use the shared `backend/events/` services rather than reimplementing
recurrence, cancellation, or vacation rules:

```python
occurrences = await load_raw_occurrences(
    session, teacher_id, range_start_utc, range_end_utc
)
visibility = filter_vacation_visibility(occurrences, teacher_id)
```

`load_raw_occurrences` is in `backend/events/range_service.py`. It returns
active raw `Occurrence` objects for one teacher, with cancellations applied,
without committing or applying display visibility. Each occurrence exposes
`source.kind`, `source.id`, `source.teacher_id`, `source.user_id`,
`source.event_type`, `start`, `end`, and the stable `identity` above.
`filter_vacation_visibility` in `backend/events/visibility.py` returns a
`VisibilityResult` with separate `visible` and `hidden` tuples; hidden items
retain the occurrence and a `teacher_vacation` or `student_vacation` reason.
Teacher vacations hide the teacher's whole schedule; student vacations hide
only that student's events.

Both helpers consume and return aware UTC timestamps. Compute day/week query
bounds in the viewer's timezone with `local_day_bounds(day, timezone_name)` or
`local_week_bounds(day, timezone_name)` from `backend/events/time_utils.py`;
these helpers return aware UTC bounds and account for DST-length days. Apply
vacation visibility and viewer authorization before shaping the response.
Teacher-wide raw data is needed to apply policy correctly, but a student must
not receive other students' event details. Format timestamps for the viewer
only after overlap, cancellation, and visibility decisions.

The shared temporal policy is wired through
`validate_schedule_change(session, data, teacher_id, ...)` in
`backend/events/conflicts.py`. It validates the complete candidate recurrence
horizon, rejects overlapping event writes, and implements vacation admission
and break displacement. A break overlapping lessons returns HTTP 409 with
`detail.affected_occurrences` unless the caller explicitly confirms; a
confirmed retry rechecks current conflicts and applies the break plus
displacement atomically. Only lessons are displaced. One-off lessons are
deleted, recurring lessons receive exact cancellation rows, and deleting the
break does not restore either.

The schedule-range service accepts finite half-open ranges of at most 31 days.
Occurrence expansion is capped at 10,000 per query. Finite new or updated
recurrences are limited to ten calendar years. Open-ended weekly lessons use
the one-calendar-year, start-based write-validation bound described above;
this does not establish conflict-free scheduling beyond that bound.
Unsupported ranges, invalid recurrence/cancellation data, and budget exhaustion
fail explicitly; callers must not convert these failures into an empty or
partial schedule.

Lesson admission checks every occurrence against the teacher's configured
local working days and hours, rejecting local-midnight crossings. Lesson
creates may omit `end` to derive it from the configured duration; student
bookings must use that duration, while teacher-authorized writes may choose
another explicit duration. Daily and consecutive limits count teacher-personal
lessons and all assigned students, excluding canceled occurrences and the
series being updated. The checks use the recurrence timezone snapshot for new
teacher-local series and the current teacher timezone for one-off and legacy
UTC-step events. Schedule mutations acquire a persistent per-teacher database
guard before validation and hold it through writes/displacement; SQLite and
PostgreSQL transaction behavior is covered by concurrent mutation tests.

Automatic post-lesson breaks are implemented as derived, read-only reservations
from each lesson's snapshotted duration. Legacy lessons and series keep a
zero-minute snapshot; preference changes apply to future writes only. A
reservation may extend beyond work hours or local midnight, follows its parent
lesson's cancellation and visibility, and does not count as a lesson.
Free-slot selection, an arbitrary-date picker, an HTMX schedule UI, offline
schedule caching, and notification delivery remain pending. Telegram week
navigation is available for earlier and later Monday–Sunday weeks.
