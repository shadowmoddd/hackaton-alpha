from .config import settings


def is_url(value) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def link(url: str) -> str:
    """Добавляет необязательную UTM-метку к ссылке (если задана UTM_SUFFIX)."""
    if not url or not settings.utm_suffix:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{settings.utm_suffix.lstrip('?&')}"
