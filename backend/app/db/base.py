from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def one_of(column: str, values: tuple[str, ...]) -> str:
    """The CHECK expression that holds `column` to `values`."""
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"
