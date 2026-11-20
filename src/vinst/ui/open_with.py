"""Opening a file in another application, as Music Finder does: a split button whose main part
opens with the default application and whose arrow lists every application macOS offers for the
file, each with its icon.

macOS only asks NSWorkspace through JavaScript for Automation (osascript), so this needs nothing
beyond the system; elsewhere the button opens with the default application only.
"""

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import FileResponse
from nicegui import app, run, ui

from vinst.ui import finder

ICON_DIR = Path.home() / "Library" / "Caches" / "vinst" / "app-icons"

APPS_SCRIPT = """
ObjC.import('Foundation');
ObjC.import('AppKit');
function run(argv) {
  const url = $.NSURL.fileURLWithPath(argv[0]);
  const ws = $.NSWorkspace.sharedWorkspace;
  const fm = $.NSFileManager.defaultManager;
  const def = ws.URLForApplicationToOpenURL(url);
  const list = ws.URLsForApplicationsToOpenURL(url);
  const apps = [];
  for (let i = 0; i < list.count; i++) {
    const p = list.objectAtIndex(i).path.js;
    apps.push({path: p, name: fm.displayNameAtPath(p).js.replace(/\\.app$/, '')});
  }
  return JSON.stringify({def: def.isNil() ? null : def.path.js, apps: apps});
}
"""

# Drawn through NSWorkspace: also for applications whose icon lives only in an asset catalog.
ICON_SCRIPT = """
ObjC.import('Foundation');
ObjC.import('AppKit');
function run(argv) {
  const img = $.NSWorkspace.sharedWorkspace.iconForFile(argv[0]);
  const rep = $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(null, 64, 64, 8, 4, true, false, $.NSCalibratedRGBColorSpace, 0, 0);
  $.NSGraphicsContext.saveGraphicsState;
  $.NSGraphicsContext.setCurrentContext($.NSGraphicsContext.graphicsContextWithBitmapImageRep(rep));
  img.drawInRectFromRectOperationFraction($.NSMakeRect(0, 0, 64, 64), $.NSZeroRect, $.NSCompositingOperationSourceOver, 1);
  $.NSGraphicsContext.restoreGraphicsState;
  rep.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $()).writeToFileAtomically(argv[1], true);
  return 'ok';
}
"""


def apps_for_file(path: Path) -> list[dict]:
    """The applications macOS offers for `path`: [{path, name, default}], the default first, then by name."""
    if sys.platform != "darwin":
        return []
    result = subprocess.run(["osascript", "-l", "JavaScript", "-e", APPS_SCRIPT, str(path)],
                            capture_output=True, text=True, check=False, timeout=15)
    if result.returncode != 0:
        return []
    found = json.loads(result.stdout)
    seen, apps = set(), []
    for a in found["apps"]:
        if a["path"] not in seen:
            seen.add(a["path"])
            apps.append({**a, "default": a["path"] == found["def"]})
    return sorted(apps, key=lambda a: (not a["default"], a["name"].lower()))


def icon_file(app_path: str) -> Path | None:
    """A 64 px PNG of the application's icon, drawn once and kept."""
    if not app_path.endswith(".app") or not Path(app_path).is_dir():
        return None
    dest = ICON_DIR / f"{hashlib.sha1(app_path.encode()).hexdigest()[:16]}.png"
    if not dest.exists():
        ICON_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["osascript", "-l", "JavaScript", "-e", ICON_SCRIPT, app_path, str(dest)],
                       capture_output=True, check=False, timeout=30)
    return dest if dest.exists() else None


@app.get("/app-icon")
async def app_icon(path: str) -> FileResponse:
    found = await run.io_bound(icon_file, path)
    if found is None:
        raise HTTPException(404)
    return FileResponse(found, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


def open_button(files: Callable[[], list[Path]], tooltip: str, before: Callable[[list[Path]], None] | None = None,
                label: str = "Open") -> None:
    """The split button for `files` (asked for when used: they may change meanwhile). `before` runs
    with the files just before they're opened, e.g. to note that they're being edited."""

    def go(app_path: str | None) -> None:
        paths = files()
        if not paths:
            ui.notify("Its files aren't there any more.", type="warning")
            return
        if before:
            before(paths)
        if not finder.open_with(app_path, paths):
            ui.notify("It couldn't be opened there.", type="warning")

    if sys.platform != "darwin":
        ui.button(label, icon="open_in_new", on_click=lambda: go(None)).props("flat dense no-caps size=sm") \
            .tooltip(tooltip)
        return

    shown: dict[str, list[dict] | None] = {"apps": None}

    async def fill() -> None:
        if shown["apps"] is not None:
            return
        paths = files()
        shown["apps"] = await run.io_bound(apps_for_file, paths[0]) if paths else []
        listing.refresh()

    @ui.refreshable
    def listing() -> None:
        ui.label("Open with").classes("text-xs opacity-60 px-3 pt-2 pb-1")
        apps = shown["apps"]
        if apps is None:
            ui.label("Asking macOS…").classes("text-sm opacity-60 px-3 pb-2")
            return
        if not apps:
            ui.label("No application claims this file type").classes("text-sm opacity-60 px-3 pb-2")
            return
        for a in apps:
            with ui.item(on_click=lambda a=a: go(a["path"])).props("dense clickable v-close-popup") \
                    .classes("min-w-56"):
                with ui.item_section().props("avatar").classes("min-w-0 pr-2"):
                    ui.image(f"/app-icon?path={quote(a['path'])}").classes("w-5 h-5")
                with ui.item_section():
                    ui.item_label(a["name"])
                if a["default"]:
                    with ui.item_section().props("side"):
                        ui.label("default").classes("text-xs opacity-60")

    with ui.button_group().props("flat dense"):
        ui.button(label, icon="open_in_new", on_click=lambda: go(None)).props("flat dense no-caps size=sm") \
            .tooltip(tooltip)
        with ui.button(icon="expand_more").props("flat dense size=sm").tooltip("Open with…"):
            with ui.menu().on("before-show", fill).props("auto-close"):
                listing()
