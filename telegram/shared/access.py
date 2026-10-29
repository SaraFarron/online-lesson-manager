from aiogram_dialog import DialogManager

from telegram.api import BackendClient


def backend(manager: DialogManager) -> BackendClient:
    client = manager.middleware_data["backend_client"]
    if not isinstance(client, BackendClient):
        raise TypeError("Backend client is not configured")
    return client
