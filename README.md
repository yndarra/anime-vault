# anime-vault

A small Windows tool that feeds [anime-sort](https://github.com/yndarra/anime-sort): it downloads **only new** pins
from private Pinterest boards and distributes fresh images into fixed-size `dataN` batches that anime-sort then
sorts by title and character. It has a CustomTkinter window and plain `.bat` launchers that run the same commands.

![anime-vault window: what is waiting for distribution, dataN folders, board sizes, duplicates](docs/images/overview.png)

## Features

- **Incremental Pinterest download.** `download` runs [gallery-dl](https://github.com/mikf/gallery-dl) with the
  browser's cookies (private boards) and skips every pin that already lives anywhere in the collection — in any
  `dataN` batch or in a distribution source — by pre-filling the gallery-dl archive. Pin title and description
  are embedded into the JPEG/PNG metadata, where anime-sort reads them as hints.
- **Keep-only boards.** A board marked `"keep": true` is kept in sync with Pinterest but is never distributed, even if
  it is listed among the sources by mistake.
- **Distribution with de-duplication.** `distribute` collects new files from the sources, drops duplicates (the same
  pin by its id, any other image by SHA-1 with a persistent hash cache), shows a plan
  (`data82: 500, data83: 500, data84: 213`) and moves the files only after confirmation. Duplicates go to a review
  folder; every run writes a JSON journal.
- **Friendly errors.** Every known failure (not logged in, browser still open so cookies are locked, HTTP 429,
  wrong paths) is printed in red with a "what to do" hint.
- **GUI.** Overview, Download (with a live "browser is open / closed" indicator), Distribute and Settings pages,
  plus a colour log. The commands run as a subprocess; their console questions arrive over a tiny line protocol
  (`\x1eASK\t<question>\t<options>`) and become buttons. No console windows flash: child processes are started
  with `CREATE_NO_WINDOW`, and the browser check uses a Toolhelp32 process snapshot instead of `tasklist`.

## Layout

The collection folder holds the images and a manifest, `anime-paths.json`, with paths relative to the collection
root (so the root can be moved) and the locations of both projects. Personal settings — batch size, Pinterest user,
boards and the browser profile — live in the project's `config.json`, which is not committed.

```
Pictures/Anime/
  anime-paths.json          collection manifest, read by anime-vault and anime-sort
  data/Anime JSON Pinterest/data/<board>/   downloaded boards, images straight in the board folder
  data/Anime ADD/processed_data/            manually curated images, also a distribution source
  dataN/data1, data2, …     batches for anime-sort
  test-dataN/               datasets processed by anime-sort
```

## Setup

Windows, Python 3.12.

```bat
py -3.12 -m venv venv
venv\Scripts\pip install -r requirements.txt
copy config.example.json config.json
```

1. Edit `config.json`: the collection folder, your Pinterest user name, the boards (`name` is the folder, `slug` is the
   board's URL part) and the browser profile for cookies.
2. Copy `anime-paths.example.json` into the collection folder as `anime-paths.json` and adjust the paths.
3. Open the window with `anime-vault.vbs`, or put copies of `launchers/anime-vault.bat` into the collection named
   `download.bat` and `distribute.bat` — the command is taken from the file name.

## Development

```bat
venv\Scripts\pip install -r requirements-dev.txt
venv\Scripts\ruff check .
venv\Scripts\pytest -q
```

The tests build a throwaway collection in a temp folder and cover pin keys, batch numbering, keep-only boards,
a full distribution run (moves, duplicates, journal) and the GUI's output parsing. CI runs them on `windows-latest`.

| Module | Purpose |
| --- | --- |
| `anime_vault/manifest.py` | reads `anime-paths.json` and `config.json` |
| `anime_vault/known.py` | what is already in the collection: pins by id, other files by cached SHA-1 |
| `anime_vault/download.py` | gallery-dl run, metadata embedding, error explanations |
| `anime_vault/distribute.py` | distribution into `dataN` |
| `anime_vault/console.py` | colour output, user-facing errors, the GUI question protocol |
| `anime_vault/gui/` | the window: `app.py` pages, `runner.py` subprocess and questions, `collection.py` stats |

Code comments and the user-facing text are in Russian; [README.txt](README.txt) is the full Russian manual.

## License

MIT
