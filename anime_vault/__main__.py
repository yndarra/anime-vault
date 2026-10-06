r"""python -m anime_vault <команда> [--root <папка коллекции>]

Команды:
    download     скачать новые пины с досок Pinterest (anime-paths.json → pinterest.boards)
    distribute   разложить новое из источников (anime-paths.json → sources) по dataN пачками по batch_size
Без --root корень ищется вверх от текущей папки (там, где лежит anime-paths.json).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from anime_vault import console, distribute, download, manifest


def main() -> int:
    parser = argparse.ArgumentParser(prog="anime_vault")
    parser.add_argument("command", choices=["download", "distribute"])
    parser.add_argument("--root", type=Path, default=None, help="папка коллекции (где лежит anime-paths.json)")
    args = parser.parse_args()

    def command() -> int:
        root = args.root.resolve() if args.root else manifest.find_root(Path.cwd())
        data = manifest.load(root)
        return {"download": download.run, "distribute": distribute.run}[args.command](data)

    return console.guarded(command)


if __name__ == "__main__":
    raise SystemExit(main())
