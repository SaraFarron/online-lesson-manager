from aiogram import Router

from middlewares import DatabaseMiddleware

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())
