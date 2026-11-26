"""Moving a recording to another note, e.g. a take played on the wrong one."""

from pathlib import Path

from vinst import cleanup_eq, takes_meta
from vinst.plan import Take, more_base, more_file, plan_takes, take_key
from vinst.slicing import more_numbers, recordings
from vinst.spec import Instrument


def move_recording(inst: Instrument, takes_dir: Path, source_key: str, note_base: str, lane: str) -> str:
    """Renames the recording's files to those of `lane` of the note `note_base`, in the same pass, and
    returns the recording's new key. A lane that's recorded already gets it as more variations, so
    nothing is overwritten. Its hits are sliced afresh there: decisions about them don't come along, the
    clean-up EQs it uses do (`inst` changes then)."""
    source = next((t for t in recordings(inst, takes_dir) if t.key == source_key), None)
    if source is None or source.kind != "note":
        raise ValueError("That recording isn't there any more.")
    target = next((t for t in plan_takes(inst) if t.kind == "note" and t.note_base == note_base
                   and t.lane == lane and t.pass_id == source.pass_id), None)
    if target is None:
        raise ValueError(f"No {lane} velocity to move it to.")
    if target.note_base == source.note_base and target.lane == source.lane and source.origin == "plan":
        raise ValueError("It's there already.")
    files, key = destination(target, takes_dir)
    moves = [(takes_dir / old, takes_dir / new) for old, new in zip(source.files, files)]
    if any(not old.exists() for old, _ in moves):
        raise ValueError("Some of its files are missing.")
    if any(new.exists() for _, new in moves):
        raise ValueError(f"{target.title} has a recording of that name already.")
    for old, new in moves:
        old.rename(new)  # keeps size and modification time, so its hand cuts still apply
        takes_meta.rename(inst.meta.slug, old.name, new.name)
    cleanup_eq.move_take(inst, source_key, key)
    return key


def destination(target: Take, takes_dir: Path) -> tuple[list[str], str]:
    """The lane's own files while it isn't recorded, otherwise those of its next "more" recording."""
    if not any((takes_dir / f).exists() for f in target.files):
        return target.files, target.key
    number = max(more_numbers(target, takes_dir), default=0) + 1
    return [more_file(target, number, s) for s in target.signals], take_key(more_base(target, number), target.pass_id)
