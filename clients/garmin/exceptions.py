class GarminClientError(Exception):
    """Базовое исключение для ошибок Garmin."""
    pass


class GarminAuthError(GarminClientError):
    """Ошибка авторизации или истечения срока сессии."""
    pass


class GarminRateLimitError(GarminClientError):
    """Превышение лимитов запросов к Garmin SSO (HTTP 429)."""
    pass