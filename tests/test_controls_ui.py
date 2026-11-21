import asyncio

from nicegui.testing import user_simulation

from vinst.ui import theme
from vinst.ui.controls import NumberField, Segmented, VelocitySplits


def test_segmented_is_found_by_its_labels_and_keeps_values_off_the_client():
    changes = []

    async def exercise():
        async with user_simulation(lambda: Segmented({"off": "Off", 2: "2 semitones"}, value="off",
                                                     on_change=lambda e: changes.append(e.value))) as user:
            await user.open("/")
            await user.should_see("2 semitones")
            segmented = user.find(Segmented).elements.pop()
            assert segmented.props["model-value"] == 0  # the client gets indexes, not the values
            segmented.value = 2
            assert segmented.props["model-value"] == 1
            assert changes == [2]

    asyncio.run(exercise())


def test_number_field_wraps_the_field_and_takes_the_theme_defaults():
    async def exercise():
        theme.apply()
        async with user_simulation(lambda: NumberField("Hits", value=3, min=1, max=9)) as user:
            await user.open("/")
            await user.should_see("Hits")
            field = user.find(NumberField).elements.pop()
            assert field.tag == NumberField.component.tag
            assert "outlined" in field.props and "dense" in field.props
            assert field.props["min"] == 1 and field.props["max"] == 9

    asyncio.run(exercise())


def test_velocity_splits_shows_its_layers_and_reports_moved_starts():
    changes = []

    async def exercise():
        async with user_simulation(lambda: VelocitySplits(["p", "mf", "f"], ["#aaa", "#888", "#666"], value=[43, 86],
                                                          on_change=lambda e: changes.append(e.value))) as user:
            await user.open("/")
            await user.should_see("mf")
            splits = user.find(VelocitySplits).elements.pop()
            assert splits.props["model-value"] == [43, 86]
            splits.value = [50, 86]
            assert splits.props["model-value"] == [50, 86]
            assert changes == [[50, 86]]

    asyncio.run(exercise())
