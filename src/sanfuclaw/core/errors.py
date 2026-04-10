"""Exception hierarchy for Sanfuclaw."""


class SanfuclawError(Exception):
    """Base exception."""


class ConfigError(SanfuclawError):
    """Configuration error."""


class ChannelError(SanfuclawError):
    """Channel connection or communication error."""


class AgentError(SanfuclawError):
    """Agent processing error."""


class ToolError(SanfuclawError):
    """Tool execution error."""


class AuthError(SanfuclawError):
    """Authentication or authorization error."""
