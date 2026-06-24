"""Двухфакторная аутентификация на основе TOTP (RFC 6238).

Совместимо с Google Authenticator, Authy, 1Password и т.п.
"""

import base64
import io

import pyotp
import qrcode

# Имя сервиса, отображаемое в приложении-аутентификаторе
ISSUER = "Aeterna Analytics"


def generate_secret() -> str:
    """Случайный base32-секрет для TOTP."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    """otpauth://-ссылка для добавления в приложение-аутентификатор."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def qr_png_base64(secret: str, email: str) -> str:
    """QR-код provisioning-ссылки как data-URI (PNG в base64)."""
    img = qrcode.make(provisioning_uri(secret, email))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def verify_code(secret: str, code: str) -> bool:
    """Проверяет 6-значный код (с окном ±1 интервал для рассинхрона часов)."""
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
