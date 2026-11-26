"""Deleting one recording from the worksheet: it leaves in place, and nothing above it moves."""

import asyncio
import threading

from nicegui import ui
from nicegui.testing import user_simulation
from nicegui.testing.user_interaction import UserInteraction

from vinst import defaults, discard, review, store
from vinst.slicing import process
from vinst.spec import InstrumentType
from vinst.synth import Mistakes, write_takes
from vinst.ui.app import Editor
from vinst.ui.worksheet import step_worksheet


def test_a_deleted_recording_fades_out_in_place(tmp_path, monkeypatch, trash_bin):
    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VINST_BUILD", str(tmp_path / "build"))
    inst = defaults.new_instrument("Uke", InstrumentType.PLUCKED)
    inst.range.low, inst.range.high, inst.range.density = 60, 61, 1
    inst.dynamics.layers, inst.extras = ["p", "f"], []
    inst.recording.takes_dir = str(tmp_path / "takes")
    store.create(inst)
    write_takes(inst, tmp_path / "takes", mistakes=Mistakes())
    review.save(process(inst), store.manifest_path(inst.meta.slug))

    # Held until the test has looked at the worksheet while the file goes.
    going, release = threading.Event(), threading.Event()
    delete = discard.delete

    def held(*args, **kwargs):
        going.set()
        assert release.wait(10)
        return delete(*args, **kwargs)

    monkeypatch.setattr(discard, "delete", held)

    def of(user, cls):
        return [e for e in user.find(ui.element).elements if cls in e.classes]

    def button(user, text):
        return UserInteraction(user, {b for b in user.find(ui.button).elements if b.text == text}, None)

    async def exercise():
        async with user_simulation(lambda: step_worksheet(Editor(inst))) as user:
            await user.open("/")
            tiles = of(user, "vi-hit")
            UserInteraction(user, {min(tiles, key=lambda e: e.id)}, None).click()
            button(user, "Delete…").click()
            button(user, "Delete").click()
            for _ in range(100):
                if going.is_set():
                    break
                await asyncio.sleep(0.02)
            assert going.is_set()
            faded = [t for t in tiles if "vi-going" in t.classes]
            assert faded and len(faded) < len(tiles)  # that recording's hits only
            assert not of(user, "vi-open") or all("vi-gapless" not in e.classes for e in of(user, "vi-open"))
            release.set()
            for _ in range(100):
                if of(user, "vi-arriving"):
                    break
                await asyncio.sleep(0.02)
            assert [e.text for e in of(user, "vi-arriving")] == ["or drop an audio file here"]
            assert len(of(user, "vi-hit")) == len(tiles) - len(faded)
            assert len(list(trash_bin.iterdir())) == 1

    asyncio.run(exercise())
