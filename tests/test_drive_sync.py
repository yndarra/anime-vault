"""План синхронизации с Драйвом (make_plan) — без сети: что копировать, удалять, переносить."""
from __future__ import annotations

from anime_vault.drive_sync import Item, conflict_name, file_key, folder_key, make_plan, scan_local


def local(path, size=10, mtime=1):
    return Item(file_key(path.split("/")), path, size, mtime)


def remote(path, size=10, md5="m", id_="r"):
    return Item(file_key(path.split("/")), path, size, md5=md5, id=id_ or path)


def index(*items):
    return {item.key: item for item in items}


def base_of(item, md5="m", mtime=1, id_=None):
    return {item.key: {"path": item.path, "size": item.size, "mtime": mtime, "md5": md5, "id": id_ or item.path}}


def test_keys_ignore_folder_numbers():
    assert file_key(["102. 86 — Eighty Six", "74. Lena", "A.JPG"]) == "86 — eighty six/lena/a.jpg"
    assert folder_key(["1. Minah"]) == folder_key(["35. Minah"])
    assert conflict_name("t/a.jpg") == "t/a (Drive).jpg"


def test_first_sync_links_same_files_and_copies_the_rest():
    plan = make_plan(index(local("1. T/a.jpg"), local("1. T/b.jpg", size=5)),
                     index(remote("7. T/a.jpg"), remote("7. T/c.jpg", id_="c")), {})
    assert [m.path for m, _t in plan.link] == ["1. T/a.jpg"]                 # тот же файл, номер папки другой
    assert [m.path for m, _id in plan.upload] == ["1. T/b.jpg"]
    assert [t.path for t, _p in plan.download] == ["7. T/c.jpg"]
    assert plan.deletions == 0


def test_deletions_go_to_the_other_side():
    gone_local, gone_remote = local("T/a.jpg"), remote("T/b.jpg")
    base = {**base_of(gone_local), **base_of(local("T/b.jpg"))}
    plan = make_plan(index(gone_local), index(gone_remote), base)
    assert [t.path for t in plan.trash_remote] == ["T/b.jpg"]                # удалён на ПК → в корзину Драйва
    assert [m.path for m in plan.trash_local] == ["T/a.jpg"]                 # удалён на Драйве → в Корзину на ПК


def test_changes_and_conflicts():
    a, b, c = local("T/a.jpg", mtime=2), local("T/b.jpg"), local("T/c.jpg", mtime=2)
    base = {**base_of(a), **base_of(b), **base_of(c)}
    plan = make_plan(index(a, b, c), index(remote("T/a.jpg"), remote("T/b.jpg", md5="new"), remote("T/c.jpg", md5="new")), base)
    assert [m.path for m, _id in plan.upload] == ["T/a.jpg"]                 # изменён на ПК
    assert [t.path for t, _p in plan.download] == ["T/b.jpg"]                # изменён на Драйве
    assert [m.path for m, _t in plan.conflict] == ["T/c.jpg"]                # изменён с обеих сторон


def test_edited_file_is_not_deleted():
    edited = local("T/a.jpg", mtime=5)
    plan = make_plan(index(edited), {}, base_of(local("T/a.jpg")))
    assert plan.deletions == 0 and [m.path for m, _id in plan.upload] == ["T/a.jpg"]


def test_moves_are_detected_by_md5():
    # На ПК персонаж переехал в другой тайтл: старый путь остался только на Драйве, новый — только на ПК.
    old_remote = remote("Small/Lena/a.jpg", md5="x")
    moved_local = local("Big/Lena/a.jpg")
    plan = make_plan(index(moved_local), index(old_remote), base_of(local("Small/Lena/a.jpg"), md5="x"),
                     local_md5=lambda item: "x")
    assert [(t.path, m.path) for t, m in plan.move_remote] == [("Small/Lena/a.jpg", "Big/Lena/a.jpg")]
    assert not plan.upload and not plan.trash_remote
    # И наоборот: перенесли на телефоне.
    old_local = local("Small/a.jpg")
    plan = make_plan(index(old_local), index(remote("Big/a.jpg", md5="y")), base_of(old_local, md5="y"))
    assert [(m.path, t.path) for m, t in plan.move_local] == [("Small/a.jpg", "Big/a.jpg")]
    assert not plan.download and not plan.trash_local


def test_renumbered_folder_is_only_remembered():
    renamed = local("12. T/a.jpg")
    was = {renamed.key: {"path": "11. T/a.jpg", "size": 10, "mtime": 1, "md5": "m", "id": "r1"}}
    plan = make_plan(index(renamed), index(remote("11. T/a.jpg", id_="r1")), was)
    assert plan.empty and [m.path for m, _t in plan.link] == ["12. T/a.jpg"]


def test_scan_local_skips_service_files(tmp_path):
    (tmp_path / "3. T" / "1. Lena").mkdir(parents=True)
    (tmp_path / "3. T" / "1. Lena" / "a.jpg").write_bytes(b"1")
    (tmp_path / "3. T" / "desktop.ini").write_text("x")
    (tmp_path / "3. T" / "b.jpg.part").write_bytes(b"1")
    files, folders = scan_local(tmp_path)
    assert list(files) == ["t/lena/a.jpg"]
    assert folders == {"t": "3. T", "t/lena": "3. T/1. Lena"}
