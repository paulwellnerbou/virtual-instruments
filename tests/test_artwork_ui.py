import asyncio
from base64 import b64decode
from pathlib import Path

import pytest
from fastapi import HTTPException
from nicegui import app, ui
from nicegui.testing import user_simulation

from vinst import defaults, store
from vinst.spec import InstrumentType
from vinst.ui.app import Editor
from vinst.ui.build_steps import artwork_image, step_sound

# Looked up at import: user_simulation resets the app's routes, and then serves the page for every path.
ARTWORK_ROUTE = next(route.path for route in app.routes if getattr(route, "name", "") == "artwork_image")


def test_library_cover_upload_rejects_invalid_replacements_and_removes_only_the_cover(tmp_path, monkeypatch):
    monkeypatch.setenv("VINST_HOME", str(tmp_path))
    inst = store.create(defaults.new_instrument("Test Uke", InstrumentType.PLUCKED))
    editor = Editor(inst)
    png = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9WQAAAAASUVORK5CYII=")

    async def exercise():
        async with user_simulation(lambda: step_sound(editor)) as user:
            await user.open("/")
            await user.should_see("Library cover")
            images = [u for u in user.find(ui.upload).elements if ".png" in u.props["accept"]]
            background, cover = sorted(images, key=lambda field: field.id)

            for uploader in (background, cover):
                response = await user.http_client.post(uploader.props["url"], files={"file": ("art.png", png)})
                assert response.status_code == 200
            await user.should_see("background.png")
            await user.should_see("cover.png")
            art = cover.style["--vi-art"]
            assert art.startswith("url('") and art.endswith("')")
            assert art[5:-2].split("?")[0] == ARTWORK_ROUTE.format(slug=inst.meta.slug, attr="cover_art")
            assert Path(artwork_image(inst.meta.slug, "cover_art").path).read_bytes() == png
            editor.autosave()
            saved = store.load(inst.meta.slug).preset
            cover_path = Path(saved.cover_art)
            assert cover_path.parent == store.instrument_dir(inst.meta.slug)
            assert cover_path.read_bytes() == png

            response = await user.http_client.post(cover.props["url"], files={"file": ("fake.png", b"not a PNG")})
            assert response.status_code == 200
            await user.should_see("Choose a PNG or JPG image for library cover.")
            assert cover_path.read_bytes() == png
            assert inst.preset.cover_art == saved.cover_art

            user.find("Remove cover").click()
            await user.should_see("No library cover selected.")
            assert cover.style["--vi-art"] == "none"
            assert background.style["--vi-art"].startswith("url('")
            with pytest.raises(HTTPException):
                artwork_image(inst.meta.slug, "cover_art")
            editor.autosave()
            assert store.load(inst.meta.slug).preset.cover_art == ""
            assert not cover_path.exists()
            assert Path(inst.preset.background).read_bytes() == png

    asyncio.run(exercise())


def test_artwork_route_answers_404_for_an_unknown_instrument(tmp_path, monkeypatch):
    monkeypatch.setenv("VINST_HOME", str(tmp_path))
    with pytest.raises(HTTPException) as err:
        artwork_image("no-such-instrument", "cover_art")
    assert err.value.status_code == 404


def test_the_editor_moves_extras_from_their_own_keys_as_the_range_changes():
    inst = defaults.new_instrument("Uke", InstrumentType.PLUCKED)  # G2 (55) up; chuck C2, knock D2
    inst.range.mode, inst.range.low, inst.range.high = "notes", 55, 81
    chuck, knock = inst.extras[1], inst.extras[2]
    editor = Editor(inst)
    assert editor.follow_range() == []
    inst.range.low = 4  # typing "40": the "4" comes first
    editor.changed()
    assert (chuck.key, knock.key) == (2, 3)
    inst.range.low = 40
    editor.changed()
    assert (chuck.key, knock.key) == (38, 39)  # from C2 and D2, not from where "4" put them
    inst.range.low = 55
    editor.changed()
    assert (chuck.key, knock.key) == (48, 50)  # back where they were set
    knock.key = 52  # set by hand: from now on its own key
    inst.range.low = 50
    editor.changed()
    assert knock.key == 49
    inst.range.low = 55
    editor.changed()
    assert knock.key == 52


def test_opening_an_instrument_moves_extras_off_the_notes():
    inst = defaults.new_instrument("Bari", InstrumentType.PLUCKED)
    inst.range.strings = defaults.tuning_strings("Baritone ukulele (D G B E)")  # from D2, the knock's key
    editor = Editor(inst)
    assert [x.name for x in editor.follow_range()] == ["Body knock"]
    assert inst.extras[2].key == 49 and not inst.problems()
