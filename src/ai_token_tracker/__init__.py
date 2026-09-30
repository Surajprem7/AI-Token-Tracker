"""AI Token Tracker - see how many tokens each AI coding session spent."""

__version__ = "2.0.0"


def log_usage(*args, **kwargs):
    """Record usage from any AI in the custom log. See sources.log_usage."""
    from .sources import log_usage as _log

    return _log(*args, **kwargs)
