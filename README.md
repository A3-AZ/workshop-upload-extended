# Workshop Upload Extended

A GitHub Action that uploads content to an existing Steam Workshop item with `steamcmd`, and can also update
the item's **title**, **description**, **preview image** and **visibility**.

- Works for any Steam game (`app-id`); the default is Arma 3 (`107410`).
- Write the description in **Markdown** and let the action convert it to Steam BBCode.
- Every field whose input is empty **stays unchanged** on Steam, so a minimal call only uploads the content.
- All values are escaped before they are written into the VDF, so quotes and backslashes in changelogs are safe.
- `dry-run` builds and prints the VDF without logging in, useful for testing workflows.
- When `steamcmd` fails, the Steam log files are printed to the job log.

## Usage

### Minimal

Uploads the content and a change note. Title, description, preview and visibility stay as they are on Steam
(unless the repository has a `.steam/` folder, see below).

```yaml
- uses: A3-AZ/workshop-upload-extended@v1
  with:
    item-id: '1234567890'
    content-path: .hemttout/release
    changelog: ${{ github.event.release.body }}
  env:
    STEAM_USERNAME: ${{ secrets.STEAM_USERNAME }}
    STEAM_PASSWORD: ${{ secrets.STEAM_PASSWORD }}
```

### With metadata from the repository

Put the Workshop page into the repository:

```text
.steam/
  description.md   # Markdown, converted to Steam BBCode
  preview.png      # max. 1 MB
```

Both files are picked up automatically when they exist, no extra inputs needed:

```yaml
name: Release

on:
  release:
    types: [published]

jobs:
  workshop:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      # Build the mod here, for example with HEMTT (output in .hemttout/release)

      - uses: A3-AZ/workshop-upload-extended@v1
        with:
          item-id: '1234567890'
          content-path: .hemttout/release
          title: My Mod
          visibility: public
          changelog: ${{ github.event.release.body }}
        env:
          STEAM_USERNAME: ${{ secrets.STEAM_USERNAME }}
          STEAM_PASSWORD: ${{ secrets.STEAM_PASSWORD }}
```

Files in other places work the same way:

```yaml
    description-file: docs/workshop.md
    preview-file: media/workshop.jpg
```

### Dry-run

```yaml
- uses: A3-AZ/workshop-upload-extended@v1
  id: workshop
  with:
    item-id: '1234567890'
    content-path: .hemttout/release
    dry-run: true
- run: echo "$VDF"
  env:
    VDF: ${{ steps.workshop.outputs.vdf }}
```

No credentials are needed; the content folder and the files must still exist.

## Inputs

| Input | Default | Description |
|---|---|---|
| `item-id` | **required** | ID of the Workshop item to update. The item must already exist. |
| `content-path` | **required** | Folder to upload, relative to the workspace. |
| `app-id` | `107410` | Steam app ID of the game (Arma 3 by default). |
| `changelog` | | Change note for this update. |
| `title` | | New title. Empty = unchanged. |
| `visibility` | | `public`, `friends`, `private` or `unlisted`. Empty = unchanged. |
| `description` | | New description as inline text. Takes precedence over the default description file. |
| `description-file` | `.steam/description.md` | File with the new description. See [Optional files](#optional-files). |
| `description-format` | `markdown` | `markdown` (converted to Steam BBCode) or `bbcode` (used as is). |
| `markdown-repo` | current repository | `owner/name` that relative Markdown links and images point to. |
| `markdown-ref` | branch / tag of the run | Branch, tag or commit for relative links and images. Falls back to the commit SHA (for example on pull requests). |
| `preview-file` | `.steam/preview.png` | Preview image, max. 1 MB. See [Optional files](#optional-files). |
| `dry-run` | `false` | Only build and print the VDF: no login, no upload, no credentials needed. |

### Optional files

`description-file` and `preview-file` follow the same rules:

- **Default path** (`.steam/description.md` / `.steam/preview.png`): used if the file exists.
  If it doesn't, the field stays unchanged on Steam.
- **Another path**: the file must exist, otherwise the action fails.
- **Empty (`''`)**: the feature is off, no file is used even if the default file exists.

> [!NOTE]
> If you wrap this action in a reusable workflow and forward an input like
> `description-file: ${{ inputs.description-file }}`, an unset wrapper input arrives as `''` and turns the
> feature off. Give the wrapper input the same default (`.steam/description.md`) to keep the default behavior.

`description` takes precedence over the default description file. Setting both `description` and a
non-default `description-file` is an error.

## Outputs

| Output | Description |
|---|---|
| `vdf` | The `workshop_build_item` VDF that was built (also in dry-run mode). |

## Credentials

The Steam account is passed as environment variables, the same way as in
[`arma-actions/workshop-upload`](https://github.com/arma-actions/workshop-upload), so switching over only
needs the new inputs:

| Variable | Description |
|---|---|
| `STEAM_USERNAME` | Login name of the Steam account. |
| `STEAM_PASSWORD` | Password of the Steam account. |

Store both as [repository or organization secrets](https://docs.github.com/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions).
The action fails early with a clear error when one of them is missing (except in dry-run mode).

> [!IMPORTANT]
> **Steam Guard has to be disabled on the account.** steamcmd can't answer a Steam Guard / 2FA prompt in CI,
> so the login fails. Use a dedicated account that owns the game and has Steam Guard turned off, not your
> personal one. That account must be allowed to update the item (owner or contributor).

## Markdown to BBCode

With `description-format: markdown` (the default) the description is converted to the BBCode Steam uses:

- Headings (`#` to `###`, deeper levels become `[h3]`; an empty `##` becomes a horizontal rule),
  underlined headings, horizontal rules
- **Bold**, *italic*, ~~strikethrough~~, `inline code`
- Links and images, also images inside links (badges), `<img>` and `<br>` tags
- Nested and numbered lists
- Fenced code blocks, quotes and GitHub alerts (`> [!NOTE]`, `> [!WARNING]`, ...)
- Tables

Relative links point to `https://github.com/<repo>/blob/<ref>/...` and relative images to
`https://raw.githubusercontent.com/<repo>/<ref>/...`, resolved from the folder of the description file.
Links to anchors (`#section`) become plain text. Images Steam can't show (SVG, shields.io and badgen badges)
are replaced by their alt text, so a badge link stays a link.

Markers let one Markdown file serve both GitHub and Steam:

```markdown
<!-- workshop-skip -->
## Building from source
Only shown on GitHub.
<!-- /workshop-skip -->

<!-- workshop-only Only shown on Steam, like a link back to the [GitHub repository](https://github.com/owner/repo). -->
```

Other HTML comments are removed. Markers inside code blocks are left alone.

Steam descriptions are limited to 8000 characters; the action warns when the converted text is longer.

To preview the result locally (Python 3.9+, no dependencies):

```bash
python src/md2bbcode.py .steam/description.md --repo owner/repo --base-dir .steam
```

## Things to know

- **The repository wins.** Title, description, preview and visibility set by this action overwrite whatever
  is on Steam, including edits made by hand on the Workshop page. Edit the files in the repository instead,
  or leave the input empty (`description-file: ''`, `preview-file: ''`) to keep managing a field on Steam.
- The Workshop item must already exist; this action only updates items. Create it once in-game or with the
  game's publishing tool.
- It is a Docker action, so it runs on Linux runners only (`ubuntu-latest`).
- `steamcmd` doesn't always set an exit code when an upload fails, so a line starting with `ERROR!` in its
  output also fails the action.

## Why a Docker action

`steamcmd` is a 32-bit program that needs extra libraries and updates itself on its first start. The
[`cm2network/steamcmd`](https://hub.docker.com/r/cm2network/steamcmd) image ships it installed and updated,
so the action doesn't depend on what is installed on the runner. The image only adds Python 3 for the
action script and the Markdown converter.

## Development

```bash
python -m unittest discover -s test -v
```

CI runs the unit tests and several dry-runs of the action itself.

## License

[MIT](LICENSE)
