"""HTTP-заголовки безопасности.

Добавляются ко всем ответам через единый middleware. Закрывают типовые
векторы: clickjacking, MIME-sniffing, утечку referrer, downgrade на HTTP.

CSP подобран под текущий фронтенд (inline <style>/<script> + Google Fonts).
'unsafe-inline' для script-src — временно: при переезде фронта на фреймворк
(этап 7) заменим на nonce/hash и уберём inline-скрипты.
"""
from starlette.middleware.base import BaseHTTPMiddleware

# Content-Security-Policy одной строкой
_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
    ]
)

_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    # Запрещаем встраивание сайта в <iframe> (дублирует frame-ancestors для старых браузеров)
    "X-Frame-Options": "DENY",
    # Браузер не угадывает MIME-тип, использует заявленный
    "X-Content-Type-Options": "nosniff",
    # Не утекаем полный URL в Referer на сторонние ресурсы
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Отключаем потенциально чувствительные браузерные API
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    # Принудительный HTTPS на год + поддомены (применяется браузером только по https)
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Проставляет заголовки безопасности на каждый ответ."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response
