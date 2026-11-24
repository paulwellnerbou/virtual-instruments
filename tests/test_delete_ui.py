"""Deleting an instrument with its recordings, when their disk has no Trash."""

import asyncio

from nicegui import ui
from nicegui.testing import user_simulation

from vinst import defaults, store, trash
from vinst.spec import InstrumentType
from vinst.ui.app import delete_dialog


def test_recordings_on_a_disk_without_a_trash_go_only_when_confirmed(tmp_path, monkeypatch, trash_bin):
    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VINST_BUILD", str(tmp_path / "build"))
    takes = tmp_path / "nas"
    takes.mkdir()
    for name in ("060_C-0_p_pickup.wav", "060_C-0_f_pickup.wav"):
        (takes / name).write_bytes(b"audio")
    inst = defaults.new_instrument("Uke", InstrumentType.PLUCKED)
    inst.recording.takes_dir = str(takes)
    inst = store.create(inst)
    to_bin = trash._system_trash  # the test's own Trash, for the instrument's folder

    def system_trash(path):
        if path.parent == takes:
            raise trash.NoTrash(f"{path.name}: its disk has no Trash")
        return to_bin(path)

    monkeypatch.setattr(trash, "_system_trash", system_trash)

    async def exercise():
        async with user_simulation(lambda: delete_dialog(inst.meta.slug, inst.meta.name, inst)) as user:
            await user.open("/")
            await user.should_see("Also its recordings (2 files")
            user.find(ui.checkbox).click()
            user.find(kind=ui.button, content="Delete").click()
            await user.should_see("Delete its recordings for good?")
            await user.should_not_see("in the Trash already")  # none went: they're all on that disk
            assert len(list(takes.iterdir())) == 2 and store.exists(inst.meta.slug)
            user.find("Delete for good").click()
            for _ in range(50):
                if store.exists(inst.meta.slug):
                    await asyncio.sleep(0.05)
            assert not any(takes.iterdir()) and not store.exists(inst.meta.slug)
            assert (trash_bin / inst.meta.slug).is_dir()  # its settings went to the Trash

    asyncio.run(exercise())
