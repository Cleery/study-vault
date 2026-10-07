"""Exceptions raised by the question-analysis domain."""


class AIError(Exception):
    """Base class for expected AI analysis failures."""


class AIResultValidationError(AIError, ValueError):
    """The provider response does not satisfy the application schema."""


class AIProviderError(AIError):
    """A provider could not produce an analysis result."""


class AIProviderResponseError(AIProviderError):
    """A provider response failed parsing while retaining bounded audit data."""

    def __init__(self, message, *, raw_response=None):
        super().__init__(message)
        self.raw_response = raw_response


class AIProviderTimeoutError(AIProviderError, TimeoutError):
    """The provider exhausted its bounded timeout retries."""


class AIConcurrencyError(AIError):
    """An analysis result was superseded by a newer input version."""
