from __future__ import annotations

from aiogram import Router

from middlewares import DatabaseMiddleware

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())


FRL_START_CALLBACK = "frl_choose_date_sl:"
ORL_START_CALLBACK = "orl_choose_date_sl:"
ORL_RS_CALLBACK = "orl_rs:"
