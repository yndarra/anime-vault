r"""python -m anime_vault [<команда>] [--root <папка коллекции>]

Команды:
    download     скачать новое с досок (config.json → pinterest.boards)
    distribute   разложить новое из источников (anime-paths.json → sources) по dataN пачками по batch_size
    (без команды) открыть окно anime-vault
Без --root корень ищется вверх от текущей папки (там, где лежит anime-paths.json), потом config.json → "collection".

Собранный anime-vault.exe — та же точка входа: двойной щелчок открывает окно, окно запускает его же с командой,
а «anime-vault.exe --gallery-dl <аргументы>» — встроенный gallery-dl (у .exe нет отдельного python).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def run_gallery_dl(arguments: list[str]) -> int:
    import gallery_dl

    sys.argv = ["gallery-dl", *arguments]
    return gallery_dl.main() or 0


def main() -> int:
    from anime_vault import console

    console.attach_std()
    if len(sys.argv) > 1 and sys.argv[1] == "--gallery-dl":
        return run_gallery_dl(sys.argv[2:])
    if len(sys.argv) == 1 or sys.argv[1] == "--page":
        from anime_vault.gui.app import main as gui

        return gui()

    from anime_vault import distribute, download, manifest
    from anime_vault.gui import collection

    parser = argparse.ArgumentParser(prog="anime_vault")
    parser.add_argument("command", choices=["download", "distribute"])
    parser.add_argument("--root", type=Path, default=None, help="папка коллекции (где лежит anime-paths.json)")
    args = parser.parse_args()

    def command() -> int:
        if args.root:
            root = args.root.resolve()
        else:
            try:
                root = manifest.find_root(Path.cwd())
            except console.UserError:
                root = collection.find_root()
                if root is None:
                    raise
        data = manifest.load(root)
        return {"download": download.run, "distribute": distribute.run}[args.command](data)

    return console.guarded(command)


if __name__ == "__main__":
    raise SystemExit(main())
