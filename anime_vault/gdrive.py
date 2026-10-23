r"""Google Drive: вход в аккаунт и клиент API для синхронизации папки (anime_vault\drive_sync.py).

Нужен OAuth-клиент Google «Приложение для ПК» (Desktop app) — файл google-credentials.json рядом с программой
(config.json и прочее — anime_vault\paths.py). Вход — один раз через браузер (login): Google спрашивает, какой
аккаунт и разрешить ли доступ к Драйву; ключ доступа сохраняется в google-token.json рядом (не в git) и дальше
обновляется сам. Права — весь Драйв (drive): иначе программа не увидит фото, добавленные с телефона.
"""
from __future__ import annotations

import json
import threading

from anime_vault.console import UserError
from anime_vault.i18n import _
from anime_vault.paths import PROJECT

SCOPES = ["https://www.googleapis.com/auth/drive"]
CLIENT = PROJECT / "google-credentials.json"
TOKEN = PROJECT / "google-token.json"
FOLDER = "application/vnd.google-apps.folder"
_local = threading.local()          # у каждого потока свой клиент: httplib2 не потокобезопасен


def credentials():
    """Сохранённый вход (обновлённый при надобности) или None."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    except (OSError, ValueError):
        return None
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            return None
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
        return creds
    return None


def login() -> str:
    """Вход через браузер (окно Google). → адрес аккаунта."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CLIENT.exists():
        raise UserError(_("нет файла {path}").format(path=CLIENT),
                        _("создайте OAuth-клиент «Приложение для ПК» в Google Cloud Console и сохраните его JSON под этим именем"))
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, prompt="select_account consent",
                                  success_message=_("Вход выполнен — окно браузера можно закрыть и вернуться в anime-vault."))
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return account()["email"]


def logout() -> None:
    TOKEN.unlink(missing_ok=True)


def service():
    """Клиент Drive API этого потока."""
    from googleapiclient.discovery import build

    if getattr(_local, "service", None) is None:
        creds = credentials()
        if creds is None:
            raise UserError(_("не выполнен вход в Google"), _("в окне anime-vault: страница «Драйв» → «Войти в Google»"))
        _local.service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _local.service


def account() -> dict:
    """{"email", "used", "limit"} — limit 0, если места без ограничения."""
    about = service().about().get(fields="user(emailAddress),storageQuota(limit,usage)").execute(num_retries=3)
    quota = about.get("storageQuota", {})
    return {"email": about["user"]["emailAddress"], "used": int(quota.get("usage", 0)), "limit": int(quota.get("limit", 0) or 0)}


def status() -> dict:
    """Для окна: {"client": есть ли google-credentials.json, "email"/"used"/"limit" или "error"}."""
    result = {"client": CLIENT.exists()}
    if credentials() is None:
        return result
    try:
        result.update(account())
    except Exception as exc:          # сеть, отозванный доступ — показать текстом
        result["error"] = str(exc)
    return result


def client_id() -> str:
    """Чей OAuth-клиент (для подсказки в окне), без секретов."""
    try:
        data = json.loads(CLIENT.read_text(encoding="utf-8"))
        return (data.get("installed") or data.get("web") or {}).get("project_id", "")
    except (OSError, ValueError):
        return ""
