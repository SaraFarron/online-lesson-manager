from __future__ import annotations

# Placeholders the backend substitutes for missing student identity; a test pins
# them to backend.auth.constants because the bot must not import the backend.
STUDENT_NAME_UNAVAILABLE_LABEL = "Name unavailable"
STUDENT_USERNAME_UNAVAILABLE_LABEL = "No username"


def student_list_label(display_name: str, username_display: str) -> str:
    """Prefer the full name, then the username, then the name placeholder."""
    if display_name != STUDENT_NAME_UNAVAILABLE_LABEL:
        return display_name
    if username_display != STUDENT_USERNAME_UNAVAILABLE_LABEL:
        return username_display
    return display_name
