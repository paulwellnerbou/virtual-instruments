import shutil
from pathlib import Path
from typing import Annotated

import typer

from vinst import defaults, launcher, sheet, store
from vinst.plan import summarize
from vinst.spec import InstrumentType

app = typer.Typer(
    help="DecentSampler Instrument Builder: build DecentSampler instruments from recordings.",
    no_args_is_help=True,
)


@app.command()
def ui(
    port: int = 8080,
    show: bool = True,
    idle_exit: Annotated[float, typer.Option(help="stop after this many seconds without an open page; 0: never")] = 0,
) -> None:
    """Run the instrument builder and open it in the browser."""
    from vinst.ui.app import run

    run(port=port, show=show, idle_exit=idle_exit)


@app.command("open")
def open_(
    port: int = launcher.APP_PORT,
    idle_exit: Annotated[float, typer.Option(help="seconds without an open page before the server stops")] = 120,
) -> None:
    """Open the builder, starting its server first if needed (what the launcher app runs)."""
    try:
        launcher.open_builder(port, idle_exit)
    except launcher.LaunchError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e


@app.command("install-app")
def install_app(
    folder: Annotated[str, typer.Option(help="where to put the app")] = "~/Applications",
) -> None:
    """Create a macOS launcher app that runs the builder from this checkout."""
    uv = shutil.which("uv")
    if uv is None:
        typer.echo("uv not found on PATH.", err=True)
        raise typer.Exit(1)
    try:
        repo = launcher.project_root()
    except launcher.LaunchError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    bundle = Path(folder).expanduser() / f"{launcher.APP_NAME}.app"
    launcher.write_launcher(bundle, repo, Path(uv))
    typer.echo(f"Installed {bundle}")


@app.command("list")
def list_() -> None:
    """List instruments."""
    instruments, broken = store.scan()
    for inst in instruments:
        s = summarize(inst)
        typer.echo(f"{inst.meta.slug:24} {inst.meta.name:28} recorded {s.recorded_files}/{s.files} files · "
                   f"plan: {s.sampled} notes, {s.hits} hits, ~{s.sample_mb:,.0f} MB")
    for slug, error in broken.items():
        typer.echo(f"{slug:24} BROKEN {store.SPEC_FILE}: {error.splitlines()[0]}", err=True)


@app.command()
def new(
    name: str,
    kind: Annotated[InstrumentType, typer.Option("--type")] = InstrumentType.PLUCKED,
) -> None:
    """Create an instrument with the defaults for its type."""
    inst = store.create(defaults.new_instrument(name, kind))
    typer.echo(store.instrument_dir(inst.meta.slug) / store.SPEC_FILE)


@app.command("sheet")
def sheet_(slug: str) -> None:
    """Write the recording sheet (HTML + CSV) next to the instrument spec."""
    html_path, csv_path = sheet.write(store.load(slug), store.instrument_dir(slug))
    typer.echo(html_path)
    typer.echo(csv_path)


@app.command("slice")
def slice_(slug: str) -> None:
    """Cut the recorded takes into hits, analyse them and write hits.json."""
    from vinst import review, slicing

    inst = store.load(slug)
    path = store.manifest_path(slug)

    def progress(i: int, n: int, title: str) -> None:
        typer.echo(f"\r[{i}/{n}] {title[:60]:60}", nl=False)

    manifest = slicing.process(inst, review.load(path), progress)
    typer.echo()
    review.save(manifest, path)
    c = manifest.counts()
    typer.echo(f"{c['takes_ok']}/{c['takes']} takes ok, {c['takes_missing']} not recorded, "
               f"{c['takes_problem']} with problems · {c['hits']} hits, {c['errors']} errors, "
               f"{c['warnings']} warnings, {c['rejected']} rejected")
    for t in manifest.takes:
        if t.status in ("count_mismatch", "error") or (t.message and t.status == "ok"):
            typer.echo(f"  take {t.number} {t.title}: {t.message}")
    typer.echo(path)


@app.command("synth-takes")
def synth_takes(
    slug: str,
    folder: Annotated[str, typer.Argument(help="where to write the fake take files")],
    ring: Annotated[float, typer.Option(help="seconds per synthetic hit")] = 1.5,
) -> None:
    """Write synthetic take files (with a few deliberate mistakes) to try slicing and review."""
    from pathlib import Path

    from vinst.synth import write_takes

    inst = store.load(slug)
    n = write_takes(inst, Path(folder).expanduser(), ring=ring)
    typer.echo(f"wrote {n} files to {folder}")


@app.command("build")
def build_(
    slug: str,
    install: Annotated[bool, typer.Option(help="copy into the DecentSampler library folder")] = False,
    package: Annotated[bool, typer.Option(help="also write a .dslibrary")] = False,
) -> None:
    """Generate and validate the DecentSampler bundle from the reviewed hits."""
    from vinst import bundle

    inst = store.load(slug)
    try:
        result = bundle.build(inst)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(f"{result.bundle}  ({result.samples} samples, {result.size_bytes / 1e6:,.0f} MB)")
    for w in result.warnings + result.report.warnings:
        typer.echo(f"  warning: {w}")
    for line in result.info:
        typer.echo(f"  {line}")
    for e in result.report.errors:
        typer.echo(f"  ERROR: {e}", err=True)
    if not result.report.ok:
        raise typer.Exit(1)
    if install:
        typer.echo(f"installed: {bundle.install(result.bundle)}")
    if package:
        typer.echo(f"packaged: {bundle.package(inst, result.bundle)}")


if __name__ == "__main__":
    app()
