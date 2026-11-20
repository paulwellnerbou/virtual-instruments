import asyncio
from base64 import b64decode
from pathlib import Path

from nicegui import ui
from nicegui.testing import user_simulation

from vinst import defaults, store
from vinst.spec import InstrumentType
from vinst.ui.app import Editor
from vinst.ui.build_steps import step_sound


def test_library_cover_upload_rejects_invalid_replacements_and_removes_only_the_cover(tmp_path, monkeypatch):
    monkeypatch.setenv("VINST_HOME", str(tmp_path))
    inst = store.create(defaults.new_instrument("Test Uke", InstrumentType.PLUCKED))
    editor = Editor(inst)
    png = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9WQAAAAASUVORK5CYII=")

    async def exercise():
        async with user_simulation(lambda: step_sound(editor)) as user:
            await user.open("/")
            await user.should_see("Library cover")
            background, cover = sorted(user.find(ui.upload).elements, key=lambda field: field.id)

            for uploader in (background, cover):
                response = await user.http_client.post(uploader.props["url"], files={"file": ("art.png", png)})
                assert response.status_code == 200
            await user.should_see("background.png")
            await user.should_see("cover.png")
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
            editor.autosave()
            assert store.load(inst.meta.slug).preset.cover_art == ""
            assert not cover_path.exists()
            assert Path(inst.preset.background).read_bytes() == png

    asyncio.run(exercise())
