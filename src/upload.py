"""Entry point of the workshop-upload-extended action.

Reads the action inputs from the INPUT_* environment variables, builds the
workshop_build_item VDF and runs steamcmd with it. Every field whose input is
empty is left out of the VDF, so Steam keeps its current value.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import md2bbcode  # noqa: E402

DEFAULT_DESCRIPTION_FILE = ".steam/description.md"
DEFAULT_PREVIEW_FILE = ".steam/preview.png"
PREVIEW_LIMIT = 1024 * 1024  # Steam rejects preview images over 1 MB
TITLE_LIMIT = 128

VISIBILITY = {"public": "0", "friends": "1", "private": "2", "unlisted": "3"}
TRUE = {"true", "1", "yes", "on"}
FALSE = {"false", "0", "no", "off", ""}


class ActionError(Exception):
    """An error with a message meant for the user."""


# ------------------------------------------------------------------ helpers


def _escape_command(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def error(message: str) -> None:
    print(f"::error::{_escape_command(message)}", flush=True)


def warning(message: str) -> None:
    print(f"::warning::{_escape_command(message)}", flush=True)


def get_input(name: str, env: dict[str, str], default: str = "") -> str:
    """Value of an action input, default when it isn't set at all (outside of GitHub).

    GitHub keeps the dashes in the variable name.
    """
    for key in (f"INPUT_{name.upper()}", f"INPUT_{name.upper().replace('-', '_')}"):
        if key in env:
            return env[key].strip()
    return default


def write_output(name: str, value: str, env: dict[str, str]) -> None:
    path = env.get("GITHUB_OUTPUT")
    if not path:
        return
    delimiter = f"EOF_{uuid.uuid4().hex}"
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{name}<<{delimiter}\n{value.rstrip(chr(10))}\n{delimiter}\n")


def vdf_escape(value: str) -> str:
    """Make a value safe for a quoted VDF (KeyValues) string.

    steamcmd reads build configs without escape sequences: a backslash is a plain character
    (so it is kept as is) and a double quote always ends the string. Double quotes can't be
    written at all, so they are replaced by typographic quotes.
    """
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    value = re.sub(r'(^|(?<=[\s(\[{<\-/]))"', "“", value)
    return value.replace('"', "”")


def build_vdf(fields: list[tuple[str, str]]) -> str:
    lines = ['"workshopitem"', "{"]
    for key, value in fields:
        lines.append(f'\t"{key}"\t"{vdf_escape(value)}"')
    lines.append("}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------- config


@dataclass
class Config:
    app_id: str
    item_id: str
    content_path: Path
    changelog: str = ""
    title: str = ""
    visibility: str = ""
    description: str | None = None  # converted, None = unchanged
    description_source: str = ""
    preview: Path | None = None  # None = unchanged
    dry_run: bool = False


def _bool(name: str, value: str) -> bool:
    if value.lower() in TRUE:
        return True
    if value.lower() in FALSE:
        return False
    raise ActionError(f"Input '{name}' must be true or false, got '{value}'.")


def _same_path(a: str, b: str) -> bool:
    return Path(a).as_posix() == Path(b).as_posix()


def _optional_file(name: str, value: str, default: str, workspace: Path) -> Path | None:
    """Path of an optional file input.

    Empty: None, the feature is turned off.
    The default path: used when it exists, otherwise None (field unchanged).
    Any other path: must exist.
    """
    if not value:
        return None
    path = workspace / value
    if path.is_file():
        return path
    if _same_path(value, default):
        return None
    raise ActionError(f"Input '{name}': file not found: {value}")


def _link_ref(env: dict[str, str]) -> str:
    ref = env.get("GITHUB_REF", "")
    if ref.startswith(("refs/heads/", "refs/tags/")) and env.get("GITHUB_REF_NAME"):
        return env["GITHUB_REF_NAME"]
    return env.get("GITHUB_SHA") or "main"


def _relative_dir(path: Path, workspace: Path) -> str:
    try:
        relative = path.resolve().parent.relative_to(workspace.resolve()).as_posix()
        return "" if relative == "." else relative
    except ValueError:
        return ""


def load_config(env: dict[str, str]) -> Config:
    workspace = Path(env.get("GITHUB_WORKSPACE") or os.getcwd())

    app_id = get_input("app-id", env) or "107410"
    if not app_id.isdigit():
        raise ActionError(f"Input 'app-id' must be a number, got '{app_id}'.")

    item_id = get_input("item-id", env)
    if not item_id:
        raise ActionError("Input 'item-id' is required.")
    if not item_id.isdigit():
        raise ActionError(f"Input 'item-id' must be a number, got '{item_id}'.")

    content_input = get_input("content-path", env)
    if not content_input:
        raise ActionError("Input 'content-path' is required.")
    content_path = (workspace / content_input).resolve()
    if not content_path.is_dir():
        raise ActionError(f"Input 'content-path': folder not found: {content_input}")
    if not any(content_path.iterdir()):
        warning(f"Content folder '{content_input}' is empty.")

    title = get_input("title", env)
    if len(title) > TITLE_LIMIT:
        warning(f"Title has {len(title)} characters, Steam allows {TITLE_LIMIT}.")

    visibility_input = get_input("visibility", env).lower()
    if visibility_input and visibility_input not in VISIBILITY:
        raise ActionError(
            f"Input 'visibility' must be one of {', '.join(VISIBILITY)} or empty, got '{visibility_input}'."
        )

    # Description: inline text wins over the default file, both explicitly set is an error
    fmt = (get_input("description-format", env) or "markdown").lower()
    if fmt not in ("markdown", "bbcode"):
        raise ActionError(f"Input 'description-format' must be markdown or bbcode, got '{fmt}'.")
    description_text = get_input("description", env)
    description_file_input = get_input("description-file", env, DEFAULT_DESCRIPTION_FILE)
    if description_text and description_file_input and not _same_path(description_file_input, DEFAULT_DESCRIPTION_FILE):
        raise ActionError("Set either 'description' or 'description-file', not both.")

    description = None
    source = ""
    base_dir = ""
    if description_text:
        source = "input 'description'"
    else:
        path = _optional_file("description-file", description_file_input, DEFAULT_DESCRIPTION_FILE, workspace)
        if path:
            description_text = path.read_text(encoding="utf-8-sig")
            source = str(path.relative_to(workspace)) if path.is_relative_to(workspace) else str(path)
            base_dir = _relative_dir(path, workspace)
    if source:
        if fmt == "markdown":
            converter = md2bbcode.Converter(
                repo=get_input("markdown-repo", env) or env.get("GITHUB_REPOSITORY") or None,
                ref=get_input("markdown-ref", env) or _link_ref(env),
                base_dir=base_dir,
            )
            description = converter.convert(description_text)
            for message in converter.warnings:
                warning(f"Description: {message}")
        else:
            description = description_text.replace("\r\n", "\n").strip()
        if len(description) > md2bbcode.STEAM_DESCRIPTION_LIMIT:
            warning(
                f"Description has {len(description)} characters, "
                f"Steam allows {md2bbcode.STEAM_DESCRIPTION_LIMIT}."
            )

    preview = _optional_file(
        "preview-file", get_input("preview-file", env, DEFAULT_PREVIEW_FILE), DEFAULT_PREVIEW_FILE, workspace
    )
    if preview:
        size = preview.stat().st_size
        if size > PREVIEW_LIMIT:
            raise ActionError(
                f"Preview image {preview.name} is {size / 1024 / 1024:.2f} MB, Steam allows 1 MB."
            )
        preview = preview.resolve()

    return Config(
        app_id=app_id,
        item_id=item_id,
        content_path=content_path,
        changelog=get_input("changelog", env),
        title=title,
        visibility=visibility_input,
        description=description,
        description_source=source,
        preview=preview,
        dry_run=_bool("dry-run", get_input("dry-run", env)),
    )


def vdf_fields(config: Config) -> list[tuple[str, str]]:
    fields = [
        ("appid", config.app_id),
        ("publishedfileid", config.item_id),
        ("contentfolder", str(config.content_path)),
    ]
    if config.preview:
        fields.append(("previewfile", str(config.preview)))
    if config.visibility:
        fields.append(("visibility", VISIBILITY[config.visibility]))
    if config.title:
        fields.append(("title", config.title))
    if config.description is not None:
        fields.append(("description", config.description))
    if config.changelog:
        fields.append(("changenote", config.changelog))
    return fields


# ------------------------------------------------------------------ steamcmd


def print_steam_logs(app_id: str, steam_home: Path) -> None:
    # https://partner.steamgames.com/doc/features/workshop/implementation#SteamCmd
    for log in (
        steam_home / "Steam/logs/stderr.txt",
        steam_home / "Steam/logs/workshop_log.txt",
        steam_home / f"Steam/workshopbuilds/depot_build_{app_id}.log",
    ):
        print(f"::group::{log}")
        try:
            print(log.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            print(f"(not readable: {exc})")
        print("::endgroup::", flush=True)


def run_steamcmd(config: Config, vdf_path: Path, env: dict[str, str]) -> int:
    username = env.get("STEAM_USERNAME", "")
    password = env.get("STEAM_PASSWORD", "")
    steamcmd_dir = Path(env.get("STEAMCMDDIR") or "/home/steam/steamcmd")
    steam_home = Path(env.get("STEAM_HOME") or "/home/steam")

    process_env = dict(env)
    process_env["HOME"] = str(steam_home)
    command = [
        str(steamcmd_dir / "steamcmd.sh"),
        "+login", username, password,
        "+workshop_build_item", str(vdf_path),
        "+quit",
    ]
    print(f"Uploading item {config.item_id} for app {config.app_id} ...", flush=True)
    failed_output = False
    with subprocess.Popen(
        command, cwd=steamcmd_dir, env=process_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace",
    ) as process:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            # steamcmd does not always set an exit code when the upload fails
            if line.lstrip().startswith("ERROR!"):
                failed_output = True
    if process.returncode != 0 or failed_output:
        error(f"steamcmd failed (exit code {process.returncode}), see the Steam logs below.")
        print_steam_logs(config.app_id, steam_home)
        return 1
    return 0


# --------------------------------------------------------------------- main


def main(env: dict[str, str] | None = None) -> int:
    env = dict(os.environ if env is None else env)
    try:
        config = load_config(env)
        if not config.dry_run:
            missing = [name for name in ("STEAM_USERNAME", "STEAM_PASSWORD") if not env.get(name)]
            if missing:
                raise ActionError(
                    f"Missing {' and '.join(missing)}. Pass them as env from your secrets, "
                    "for example STEAM_USERNAME: ${{ secrets.STEAM_USERNAME }}."
                )
    except ActionError as exc:
        error(str(exc))
        return 1

    print(f"Item:        {config.item_id} (app {config.app_id})")
    print(f"Content:     {config.content_path}")
    print(f"Title:       {config.title or '(unchanged)'}")
    print(f"Visibility:  {config.visibility or '(unchanged)'}")
    print(f"Description: {config.description_source or '(unchanged)'}")
    print(f"Preview:     {config.preview or '(unchanged)'}")
    print(f"Changelog:   {'set' if config.changelog else '(empty)'}")

    vdf = build_vdf(vdf_fields(config))
    print("::group::workshop_build_item VDF")
    print(vdf, end="")
    print("::endgroup::", flush=True)
    write_output("vdf", vdf, env)

    if config.dry_run:
        print("Dry run - not logging in, nothing uploaded.")
        return 0

    with tempfile.TemporaryDirectory() as temp:
        vdf_path = Path(temp) / "workshop.vdf"
        vdf_path.write_text(vdf, encoding="utf-8", newline="\n")
        return run_steamcmd(config, vdf_path, env)


if __name__ == "__main__":
    sys.exit(main())
