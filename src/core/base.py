import os


class ImproperlyConfiguredError(Exception):
    """Raises when a environment variable is missing."""

    def __init__(self, variable_name: str, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        """Constructor."""
        self.variable_name = variable_name
        self.message = f"Set the {variable_name} environment variable."
        super().__init__(self.message, *args, **kwargs)


def getenv(var_name: str, cast_to=str) -> str:  # noqa: ANN001
    """
    Gets an environment variable or raises an exception.

    Args:
    ----
        var_name: An environment variable name.
        cast_to: A type to cast.

    Returns:
    -------
        A value of the environment variable.

    Raises:
    ------
        ImproperlyConfigured: If the environment variable is missing.

    """
    try:
        value = os.environ[var_name]
        return cast_to(value)
    except KeyError:
        raise ImproperlyConfiguredError(var_name)  # noqa: B904
    except ValueError:
        error = f"The value {value} can't be cast to {cast_to}."
        raise ValueError(error)  # noqa: B904
