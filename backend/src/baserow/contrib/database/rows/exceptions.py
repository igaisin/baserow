class RowDoesNotExist(Exception):
    """Raised when trying to get rows that don't exist."""

    def __init__(self, ids, *args, **kwargs):
        if not isinstance(ids, list):
            ids = [ids]
        self.ids = ids
        super().__init__(*args, **kwargs)


class RowIdsNotUnique(Exception):
    """Raised when trying to update the same rows multiple times"""

    def __init__(self, ids, *args, **kwargs):
        self.ids = ids
        super().__init__(*args, **kwargs)


class ReportMaxErrorCountExceeded(Exception):
    """
    Raised when a the report raises too many error.
    """

    def __init__(self, report, *args, **kwargs):
        self.report = report
        super().__init__("Too many errors", *args, **kwargs)


class CannotCalculateIntermediateOrder(Exception):
    """
    Raised when an intermediate order can't be calculated. This could be because the
    fractions are equal.
    """
