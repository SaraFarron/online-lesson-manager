import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

BOT_ROOT = Path(__file__).resolve().parents[1] / "telegram"
SHARED_HELPER_FILES = {"date_cleanup.py", "feedback.py", "inputs.py"}


@pytest.mark.parametrize(
    "import_order",
    [
        ("telegram.runtime", "backend.main"),
        ("backend.main", "telegram.runtime"),
    ],
)
def test_bot_and_backend_imports_are_independent(
    import_order: tuple[str, str], tmp_path: Path
) -> None:
    project_root = BOT_ROOT.parent
    (tmp_path / "static").mkdir()
    (tmp_path / "templates").mkdir()
    environment = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(project_root),
        "AUTH_JWT_SECRET": "test-auth-secret-with-no-real-credentials",
        "BOT_TOKEN": "test-bot-token",
        "BOT_SERVICE_CREDENTIAL": "test-bot-service-credential-at-least-32",
        "TELEGRAM_SERVICE_CREDENTIAL": "test-backend-service-credential-at-least-32",
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'imports.db'}",
        "HOMEWORK_STORAGE_PATH": str(tmp_path / "homework"),
    }
    script = (
        "import importlib\n"
        "import sys\n"
        f"order = {import_order!r}\n"
        "for module in order:\n"
        "    importlib.import_module(module)\n"
        '    if order[0] == "telegram.runtime" and module == "telegram.runtime":\n'
        "        assert not any(\n"
        '            name == "backend" or name.startswith("backend.")\n'
        "            for name in sys.modules\n"
        "        )\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=environment,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _module_name(path: Path) -> str:
    relative = path.relative_to(BOT_ROOT.parent).with_suffix("")
    parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
    return ".".join(parts)


def _import_targets(node: ast.Import | ast.ImportFrom, package: str) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}

    module = node.module or ""
    if node.level:
        target = importlib.util.resolve_name("." * node.level + module, package)
    else:
        target = module
    targets = {target}
    targets.update(
        f"{target}.{alias.name}" if target else alias.name
        for alias in node.names
        if alias.name != "*"
    )
    return targets


def _is_shared_helper(path: Path) -> bool:
    relative = path.relative_to(BOT_ROOT)
    return relative.parts[0] == "shared" or (
        len(relative.parts) == 1 and relative.name in SHARED_HELPER_FILES
    )


def _is_feature_dialog(target: str) -> bool:
    if target == "telegram.dialogs" or target.startswith("telegram.dialogs."):
        return True
    return any(
        part == "onboarding" or part.endswith("_dialogs")
        for part in target.split(".")[1:]
    )


def test_bot_import_architecture_has_no_backend_or_dialog_leaks() -> None:
    backend_imports: list[str] = []
    shared_dialog_imports: list[str] = []

    for path in sorted(BOT_ROOT.rglob("*.py")):
        module = _module_name(path)
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            targets = _import_targets(node, package)
            backend_imports.extend(
                f"{path.relative_to(BOT_ROOT)}:{node.lineno}: {target}"
                for target in targets
                if target == "backend" or target.startswith("backend.")
            )
            if _is_shared_helper(path):
                shared_dialog_imports.extend(
                    f"{path.relative_to(BOT_ROOT)}:{node.lineno}: {target}"
                    for target in targets
                    if _is_feature_dialog(target)
                )

    assert not backend_imports, "\n".join(backend_imports)
    assert not shared_dialog_imports, "\n".join(shared_dialog_imports)


def test_public_api_exports_canonical_classes() -> None:
    from telegram.api import BackendClient, BackendError, Registration
    from telegram.api.client import BackendClient as ClientImplementation
    from telegram.api.errors import BackendError as ErrorImplementation
    from telegram.api.models.accounts import Registration as RegistrationContract

    assert BackendClient is ClientImplementation
    assert BackendError is ErrorImplementation
    assert Registration is RegistrationContract


def test_event_service_reexports_canonical_mutation_entry_points() -> None:
    from backend.events import service
    from backend.events.mutations import (
        occurrence_actions,
        one_off,
        recurring,
        serialization,
    )

    assert service.create_event is one_off.create_event
    assert service.update_event is one_off.update_event
    assert service.delete_event is one_off.delete_event
    assert service.create_recurrent_event is recurring.create_recurrent_event
    assert service.update_recurrent_event is recurring.update_recurrent_event
    assert (
        service.cancel_recurrent_lesson_occurrence
        is occurrence_actions.cancel_recurrent_lesson_occurrence
    )
    assert (
        service.move_recurrent_lesson_occurrence
        is occurrence_actions.move_recurrent_lesson_occurrence
    )
    assert (
        service.run_serialized_schedule_mutation
        is serialization.run_serialized_schedule_mutation
    )


def test_homework_cleanup_module_import_does_not_execute_cleanup() -> None:
    from backend.homework import cleanup

    assert callable(cleanup.main)
