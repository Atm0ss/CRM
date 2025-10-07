"""Utilities for managing remote support tooling such as AnyDesk."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class RemoteAccessStatus:
    """Information about the state of the remote support tooling."""

    tool: str
    executable: Optional[str]
    installed: bool
    desk_id: Optional[str]
    message: str


ANYDESK_CANDIDATES = [
    "anydesk",
    "/usr/bin/anydesk",
    "/opt/anydesk/anydesk",
    "C:/Program Files (x86)/AnyDesk/AnyDesk.exe",
    "C:/Program Files/AnyDesk/AnyDesk.exe",
    "/Applications/AnyDesk.app/Contents/MacOS/AnyDesk",
]


def find_anydesk_executable() -> Optional[str]:
    """Return the path to the AnyDesk executable if it exists."""

    executable = shutil.which("anydesk")
    if executable:
        return executable

    for candidate in ANYDESK_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def install_anydesk() -> tuple[bool, str]:
    """Try to install AnyDesk using the platform package manager."""

    system = platform.system().lower()
    try:
        if system == "linux":
            if shutil.which("apt-get"):
                subprocess.run(
                    ["sudo", "apt-get", "update"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                subprocess.run(
                    ["sudo", "apt-get", "install", "-y", "anydesk"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                return True, "AnyDesk установлен через apt-get"
            if shutil.which("dnf"):
                subprocess.run(
                    ["sudo", "dnf", "install", "-y", "anydesk"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                return True, "AnyDesk установлен через dnf"
            return False, (
                "Не удалось определить пакетный менеджер для Linux. Установите AnyDesk вручную."
            )
        if system == "windows":
            return False, (
                "Автоматическая установка AnyDesk на Windows не поддерживается. Загрузите инсталлятор с https://anydesk.com/"
            )
        if system == "darwin":
            if shutil.which("brew"):
                subprocess.run(
                    ["brew", "install", "--cask", "anydesk"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                return True, "AnyDesk установлен через Homebrew"
            return False, (
                "Для macOS требуется предварительно установить Homebrew или выполнить установку AnyDesk вручную."
            )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - best effort logic
        return False, f"Не удалось установить AnyDesk автоматически: {exc}"

    return False, "Неизвестная платформа. Установите AnyDesk вручную."


def fetch_anydesk_id(executable: str) -> Optional[str]:
    """Run AnyDesk CLI to obtain the desktop ID if supported."""

    try:
        result = subprocess.run(
            [executable, "--get-id"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError:
        return None
    except subprocess.CalledProcessError:
        return None

    output = result.stdout.strip()
    if not output:
        return None
    return output.splitlines()[-1].strip()


def ensure_anydesk() -> RemoteAccessStatus:
    """Ensure AnyDesk is installed and retrieve the current desktop ID."""

    executable = find_anydesk_executable()
    if not executable:
        installed, message = install_anydesk()
        if not installed:
            return RemoteAccessStatus(
                tool="AnyDesk",
                executable=None,
                installed=False,
                desk_id=None,
                message=message,
            )
        executable = find_anydesk_executable()
        if not executable:
            return RemoteAccessStatus(
                tool="AnyDesk",
                executable=None,
                installed=False,
                desk_id=None,
                message="AnyDesk установка завершилась, но исполняемый файл не найден. Проверьте вручную.",
            )
        message = "AnyDesk успешно установлен."
    else:
        message = "AnyDesk уже установлен."

    desk_id = fetch_anydesk_id(executable)
    if desk_id:
        message = f"{message} Получен ID рабочего стола: {desk_id}."
    else:
        message = (
            f"{message} Не удалось автоматически получить ID рабочего стола. "
            "Откройте AnyDesk и скопируйте ID вручную."
        )

    return RemoteAccessStatus(
        tool="AnyDesk",
        executable=executable,
        installed=True,
        desk_id=desk_id,
        message=message,
    )
