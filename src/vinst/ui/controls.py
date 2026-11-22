"""Controls Quasar has no desktop version of: a segmented control whose highlight slides to the choice, a
number field with a stepper instead of the browser's spin buttons, and a bar of velocity layers whose
boundaries are dragged."""

from typing import Any

from nicegui import ui
from nicegui.elements.choice_element import ChoiceElement
from nicegui.elements.mixins.disableable_element import DisableableElement
from nicegui.elements.mixins.value_element import ValueElement
from nicegui.events import GenericEventArguments, Handler, ValueChangeEventArguments


class Segmented(ChoiceElement, DisableableElement, component="segmented.js"):
    """Like ``ui.toggle``: options as a list of values or a dict of value -> label."""

    def __init__(self, options: list | dict, *, value: Any = None,
                 on_change: Handler[ValueChangeEventArguments[Any]] | None = None,
                 clearable: bool = False) -> None:
        super().__init__(options=options, value=value, on_change=on_change)
        self._props.set_bool("clearable", clearable)

    # The client knows the options by index, as with ui.toggle: values needn't be JSON.
    def _event_args_to_value(self, e: GenericEventArguments) -> Any:
        return self._values[e.args] if e.args is not None else None

    def _value_to_model_value(self, value: Any) -> Any:
        return self._values.index(value) if value in self._values else None

    def _displayed_contents(self, *, only_visible: bool) -> list:
        """What NiceGUI's test helpers (``user.should_see``, ``find(content=...)``) can find."""
        return [*super()._displayed_contents(only_visible=only_visible), *self._labels]


class NumberField(ui.number, component="number_field.js"):
    """``ui.number`` with a stepper: the field's step, within its limits, repeating while held."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tag = self.component.tag  # ui.number names Quasar's q-input; this wraps it


class VelocitySplits(ValueElement[list[int]], component="velocity_splits.js"):
    """The velocity layers as one bar, with a knob on each boundary. The value is where each layer after the
    first starts (2..127, rising); it changes when a knob is let go."""

    def __init__(self, labels: list[str], colours: list[str], *, value: list[int],
                 on_change: Handler[ValueChangeEventArguments[list[int]]] | None = None) -> None:
        super().__init__(value=value, on_value_change=on_change)
        self._props["labels"] = labels
        self._props["colours"] = colours

    def _displayed_contents(self, *, only_visible: bool) -> list:
        return [*super()._displayed_contents(only_visible=only_visible), *self._props["labels"]]
