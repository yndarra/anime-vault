"""Все строки в _("…") / _('…') из кода пакета — для проверки, что у каждой есть английский перевод (test_i18n.py)."""
from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "anime_vault"


def translatable() -> dict[str, str]:
    """строка → файл:строка, где она встречается."""
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.setdefault(node.args[0].value, f"{path.relative_to(PACKAGE.parent)}:{node.lineno}")
    return found


if __name__ == "__main__":
    import json

    print(json.dumps(sorted(translatable()), ensure_ascii=False, indent=0))
