import asyncio

from conftest import piezo_and_mic
from nicegui.testing import user_simulation

from vinst import defaults, store
from vinst.spec import InstrumentType
from vinst.ui.app import Editor
from vinst.ui.build_steps import step_sound
from vinst.ui.controls import NumberField


def test_the_blend_knob_start_is_set_under_signal_mix(tmp_path, monkeypatch):
    monkeypatch.setenv("VINST_HOME", str(tmp_path))
    inst = store.create(piezo_and_mic(defaults.new_instrument("Test Uke", InstrumentType.PLUCKED)))
    a, b = inst.signals[:2]
    editor = Editor(inst)

    async def exercise():
        async with user_simulation(lambda: step_sound(editor)) as user:
            await user.open("/")
            await user.should_see(f"Where the {b.name} knob starts: 0 % plays only {a.name}")
            field = next(f for f in user.find(NumberField).elements if f.props.get("label") == "Starts at"
                         and f.props.get("suffix") == "%")
            assert field.value == 50
            field.value = 30
            assert inst.preset.mix == 30

    asyncio.run(exercise())
