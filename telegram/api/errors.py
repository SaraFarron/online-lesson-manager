from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models.events import BreakDisplacementPreview


class BackendUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Backend unavailable. Please retry shortly; no offline login is possible."
        )


class BackendOutcomeUnknown(BackendUnavailable):
    def __init__(self) -> None:
        RuntimeError.__init__(
            self,
            "The save outcome is unknown. Reload the latest data before trying again; "
            "do not repeat the action.",
        )


class HomeworkOutcomeUnknown(BackendOutcomeUnknown):
    def __init__(self, *, requires_operation_key: bool = True) -> None:
        if requires_operation_key:
            message = (
                "The homework save outcome is unknown. Retry only with the same "
                "operation key to avoid creating a duplicate."
            )
        else:
            message = (
                "The homework action outcome is unknown. Reload the latest homework "
                "state before deciding whether to retry."
            )
        RuntimeError.__init__(
            self,
            message,
        )


class HomeworkAttachmentTooLarge(ValueError):
    def __init__(self) -> None:
        super().__init__("Homework attachments must be 20 MiB or smaller.")


class BackendError(RuntimeError):
    def __init__(
        self,
        status: int,
        detail: str,
        *,
        code: str | None = None,
        displacement_preview: BreakDisplacementPreview | None = None,
    ) -> None:
        self.status = status
        self.detail = detail
        self.code = code
        self.displacement_preview = displacement_preview
        super().__init__(detail)
