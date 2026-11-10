"""SQL statement counter shared by the ``count_statements`` fixture and tests (S-1.4.7)."""


class StatementCounter:
    """Number of SQL statements executed while a ``count_statements`` block was open."""

    def __init__(self) -> None:
        self.count = 0
        self.statements: list[str] = []
