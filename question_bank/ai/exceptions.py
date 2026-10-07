"""Exceptions raised by the question-analysis domain."""


class AIError(Exception):
    """Base class for expected AI analysis failures."""


class AIResultValidationError(AIError, ValueError):
    """The provider response does not satisfy the application schema."""


class AIProviderError(AIError):
    """A provider could not produce an analysis result."""


class AIProviderTimeoutError(AIProviderError, TimeoutError):
    """The provider exhausted its bounded timeout retries."""


class AIConcurrencyError(AIError):
    """An analysis result was superseded by a newer input version."""
