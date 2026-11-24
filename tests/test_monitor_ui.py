"""The input monitor in the recording settings: when its stream is open, and when it lets go."""

import asyncio
from types import SimpleNamespace

from nicegui import ui
from nicegui.testing import user_simulation

from vinst import defaults, recorder, store
from vinst.spec import InstrumentType
from vinst.ui import recording

SR = 48000


class Rig:
    """A fake interface, and what the app did with it."""

    def __init__(self, monkeypatch) -> None:
        self.devices = [recorder.Device(0, "Fake", 2, SR)]
        self.refuse = False
        self.refuse_output = False
        self.output_channels = 2
        self.scans: list[int] = []  # streams open at each re-scan of the devices
        self.opened: list[dict] = []  # what each stream was opened with
        monkeypatch.setattr(recorder, "input_devices", self.input_devices)
        monkeypatch.setattr(recorder, "_sd", lambda: SimpleNamespace(InputStream=self.stream, Stream=self.stream))
        monkeypatch.setattr(recorder, "output_channels", lambda: self.output_channels)
        monkeypatch.setattr(store, "load_settings", lambda: SimpleNamespace(input_device="Fake"))
        monkeypatch.setattr(recording, "_monitors", [])
        self.inst = defaults.new_instrument("Mini", InstrumentType.PLUCKED)
        self.inst.recording.sample_rate = SR
        self.ed = SimpleNamespace(inst=self.inst, on_change_hooks=[], set=self.set)

    def input_devices(self, rescan: bool = False) -> list[recorder.Device]:
        if rescan:
            self.scans.append(recorder.Recorder.open_count)
        return list(self.devices)

    def stream(self, **kw):
        if self.refuse or (self.refuse_output and isinstance(kw["device"], tuple)):
            raise OSError("device busy")
        self.opened.append(kw)
        return SimpleNamespace(start=lambda: None, stop=lambda: None, close=lambda: None)

    def set(self, obj, attr, value) -> None:
        setattr(obj, attr, value)
        for hook in list(self.ed.on_change_hooks):
            hook()


def lit(user) -> bool:
    return "unelevated" in user.find(kind=ui.button, content="graphic_eq").elements.pop().props


async def click(user, icon: str) -> None:
    user.find(kind=ui.button, content=icon).click()
    await asyncio.sleep(0.05)  # a refresh redraws in the background


def run(monkeypatch, exercise) -> None:
    rig = Rig(monkeypatch)
    open_before = recorder.Recorder.open_count

    async def main():
        async with user_simulation(lambda: recording.input_settings(rig.ed)) as user:
            await user.open("/")
            try:
                await exercise(user, rig, lambda: recorder.Recorder.open_count - open_before)
            finally:
                user.client.delete()  # as when the page is closed for good

    asyncio.run(main())
    assert recorder.Recorder.open_count == open_before  # released with the page


def test_monitor_opens_the_input_and_lets_go_for_a_take(monkeypatch):
    async def exercise(user, rig, streams):
        await click(user, "graphic_eq")
        assert streams() == 1 and lit(user)
        recording.stop_monitors()  # as a take or Detect does before opening the interface
        await asyncio.sleep(0.05)
        assert streams() == 0 and not lit(user)

    run(monkeypatch, exercise)


def test_monitor_plays_the_input_on_the_sound_output(monkeypatch):
    async def exercise(user, rig, streams):
        await click(user, "graphic_eq")
        assert rig.opened[-1]["device"] == (0, None)  # the interface in, the Mac's default output out
        assert rig.opened[-1]["channels"] == (1, 2) and rig.opened[-1]["latency"] == "low"

    run(monkeypatch, exercise)


def test_monitor_without_a_sound_output_shows_the_level(monkeypatch):
    async def exercise(user, rig, streams):
        rig.output_channels = 0
        await click(user, "graphic_eq")
        assert streams() == 1 and lit(user)
        assert len(rig.opened) == 1 and rig.opened[0]["device"] == 0  # the interface only

    run(monkeypatch, exercise)


def test_monitor_whose_output_refuses_still_shows_the_level(monkeypatch):
    async def exercise(user, rig, streams):
        rig.refuse_output = True
        await click(user, "graphic_eq")
        assert streams() == 1 and lit(user)
        assert rig.opened[-1]["device"] == 0
        await user.should_see("level only")

    run(monkeypatch, exercise)


def test_rescan_reads_the_devices_with_the_monitor_closed_and_reopens_it(monkeypatch):
    async def exercise(user, rig, streams):
        await click(user, "graphic_eq")
        await click(user, "refresh")
        assert rig.scans[-1] == 0  # PortAudio skips the re-scan while a stream is open
        assert streams() == 1 and lit(user)

    run(monkeypatch, exercise)


def test_unplugged_interface_closes_the_monitor_and_it_resumes_when_back(monkeypatch):
    async def exercise(user, rig, streams):
        await click(user, "graphic_eq")
        devices, rig.devices = rig.devices, []
        await click(user, "refresh")
        assert streams() == 0
        rig.devices = devices
        await click(user, "refresh")
        assert rig.scans[-1] == 0 and streams() == 1 and lit(user)

    run(monkeypatch, exercise)


def test_monitor_that_cant_open_shows_as_off(monkeypatch):
    async def exercise(user, rig, streams):
        rig.refuse = True
        await click(user, "graphic_eq")
        assert streams() == 0 and not lit(user)
        rig.refuse = False
        await click(user, "graphic_eq")  # turns it on, not off
        assert streams() == 1 and lit(user)

    run(monkeypatch, exercise)


def test_leaving_the_worksheet_stops_only_that_pages_monitor(monkeypatch):
    async def exercise(user, rig, streams):
        await click(user, "graphic_eq")
        recording.stop_monitors(object())  # another page
        assert streams() == 1
        recording.stop_monitors(recording._monitors[0].client)
        await asyncio.sleep(0.05)
        assert streams() == 0 and not lit(user)

    run(monkeypatch, exercise)


def test_a_take_that_cant_open_leaves_the_monitor_on(monkeypatch):
    async def exercise(user, rig, streams):
        await click(user, "graphic_eq")
        rig.inst.recording.sample_rate = 44100  # the interface runs at 48 kHz: no take
        with user.client:
            recording.open_dialog(rig.ed, "C3", rig.inst.signals, {}, lambda n: None, None, None, "")
        await asyncio.sleep(0.05)
        assert rig.scans[-1] == 0  # the devices were read again with the monitor closed
        assert streams() == 1 and lit(user)

    run(monkeypatch, exercise)


def test_detect_pauses_the_monitor_and_resumes_it(monkeypatch):
    monkeypatch.setattr(recording, "DETECT_S", 1)

    async def exercise(user, rig, streams):
        await click(user, "graphic_eq")
        await click(user, "hearing")
        assert streams() == 1 and not recording._monitors  # only Detect's own stream
        await asyncio.sleep(1.3)
        assert streams() == 1 and lit(user)

    run(monkeypatch, exercise)


def test_an_input_the_interface_lacks_cant_be_monitored(monkeypatch):
    async def exercise(user, rig, streams):
        rig.inst.recording.input_channels = {"pickup": 3}  # the interface has 2
        rig.ed.on_change_hooks[0]()
        await asyncio.sleep(0.05)
        assert not user.find(kind=ui.button, content="graphic_eq").elements.pop().enabled

    run(monkeypatch, exercise)
