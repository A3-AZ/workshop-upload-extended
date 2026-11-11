import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import upload  # noqa: E402

FIXTURES = ROOT / "test" / "fixtures"


class VdfTests(unittest.TestCase):
    def test_escape_quotes_and_backslashes(self):
        self.assertEqual(upload.vdf_escape('say "hi" C:\\x\\'), 'say \\"hi\\" C:\\\\x\\\\')

    def test_escape_normalizes_newlines_and_drops_nul(self):
        self.assertEqual(upload.vdf_escape("a\r\nb\rc\x00"), "a\nb\nc")

    def test_build_vdf(self):
        vdf = upload.build_vdf([("appid", "1"), ("changenote", 'Fix "x"\nand \\y')])
        self.assertEqual(vdf, '"workshopitem"\n{\n\t"appid"\t"1"\n\t"changenote"\t"Fix \\"x\\"\nand \\\\y"\n}\n')


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workspace)
        (self.workspace / "out").mkdir()
        (self.workspace / "out" / "mod.cpp").write_text("x")

    def env(self, **inputs):
        env = {
            "GITHUB_WORKSPACE": str(self.workspace),
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_REF": "refs/tags/v1.0.0",
            "GITHUB_REF_NAME": "v1.0.0",
            "GITHUB_SHA": "abc123",
            "INPUT_ITEM-ID": "123",
            "INPUT_CONTENT-PATH": "out",
        }
        for key, value in inputs.items():
            env["INPUT_" + key.upper().replace("_", "-")] = value
        return env

    def write(self, relative, content):
        path = self.workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def load(self, **inputs):
        with contextlib.redirect_stdout(io.StringIO()):
            return upload.load_config(self.env(**inputs))

    def fields(self, **inputs):
        return dict(upload.vdf_fields(self.load(**inputs)))

    def assert_fails(self, text, **inputs):
        with self.assertRaises(upload.ActionError) as ctx:
            self.load(**inputs)
        self.assertIn(text, str(ctx.exception))

    # --- minimal

    def test_minimal_leaves_everything_unchanged(self):
        fields = self.fields()
        self.assertEqual(set(fields), {"appid", "publishedfileid", "contentfolder"})
        self.assertEqual(fields["appid"], "107410")
        self.assertEqual(fields["publishedfileid"], "123")
        self.assertEqual(Path(fields["contentfolder"]), (self.workspace / "out").resolve())

    def test_underscore_input_names_work_too(self):
        env = self.env()
        env["INPUT_ITEM_ID"] = env.pop("INPUT_ITEM-ID")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(upload.load_config(env).item_id, "123")

    def test_required_and_numeric_inputs(self):
        self.assert_fails("'item-id' is required", item_id="")
        self.assert_fails("'item-id' must be a number", item_id="12a")
        self.assert_fails("'app-id' must be a number", app_id="x")
        self.assert_fails("'content-path' is required", content_path="")
        self.assert_fails("folder not found", content_path="missing")

    # --- simple fields

    def test_title_changelog_visibility(self):
        fields = self.fields(title="My Mod", changelog="v1\nfixes", visibility="Unlisted")
        self.assertEqual(fields["title"], "My Mod")
        self.assertEqual(fields["changenote"], "v1\nfixes")
        self.assertEqual(fields["visibility"], "3")

    def test_invalid_visibility(self):
        self.assert_fails("'visibility' must be one of", visibility="hidden")

    def test_invalid_dry_run(self):
        self.assert_fails("'dry-run' must be true or false", dry_run="maybe")

    def test_dry_run_values(self):
        self.assertTrue(self.load(dry_run="TRUE").dry_run)
        self.assertFalse(self.load(dry_run="false").dry_run)

    # --- description

    def test_default_description_file_is_converted(self):
        self.write(".steam/description.md", "# Hi\n![s](shot.png)")
        fields = self.fields()
        self.assertEqual(
            fields["description"],
            "[h1]Hi[/h1]\n[img]https://raw.githubusercontent.com/owner/repo/v1.0.0/.steam/shot.png[/img]",
        )

    def test_missing_default_description_file_is_unchanged(self):
        self.assertNotIn("description", self.fields())

    def test_missing_explicit_description_file_fails(self):
        self.assert_fails("file not found", description_file=".steam/description.md")

    def test_none_disables_default_description_file(self):
        self.write(".steam/description.md", "# Hi")
        self.assertNotIn("description", self.fields(description_file="none"))

    def test_inline_description_wins_over_default_file(self):
        self.write(".steam/description.md", "# File")
        self.assertEqual(self.fields(description="**Inline**")["description"], "[b]Inline[/b]")

    def test_inline_and_explicit_file_fails(self):
        self.write("d.md", "x")
        self.assert_fails("not both", description="x", description_file="d.md")

    def test_bbcode_format_is_passed_through(self):
        self.write("d.txt", "[b]x[/b] **y**\r\n")
        fields = self.fields(description_file="d.txt", description_format="bbcode")
        self.assertEqual(fields["description"], "[b]x[/b] **y**")

    def test_invalid_description_format(self):
        self.assert_fails("'description-format'", description="x", description_format="html")

    def test_markdown_repo_and_ref_override(self):
        fields = self.fields(description="[l](a.md)", markdown_repo="o/r", markdown_ref="dev")
        self.assertEqual(fields["description"], "[url=https://github.com/o/r/blob/dev/a.md]l[/url]")

    def test_link_ref_falls_back_to_sha(self):
        env = self.env(description="[l](a.md)")
        env["GITHUB_REF"] = "refs/pull/1/merge"
        with contextlib.redirect_stdout(io.StringIO()):
            config = upload.load_config(env)
        self.assertEqual(config.description, "[url=https://github.com/owner/repo/blob/abc123/a.md]l[/url]")

    def test_long_description_warns(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            upload.load_config(self.env(description="x" * 8001))
        self.assertIn("::warning::Description has 8001 characters", out.getvalue())

    # --- preview

    def test_default_preview_file(self):
        path = self.write(".steam/preview.png", b"png")
        self.assertEqual(self.fields()["previewfile"], str(path.resolve()))

    def test_missing_explicit_preview_fails(self):
        self.assert_fails("file not found", preview_file="p.png")

    def test_none_disables_default_preview(self):
        self.write(".steam/preview.png", b"png")
        self.assertNotIn("previewfile", self.fields(preview_file="none"))

    def test_preview_over_1mb_fails(self):
        self.write("big.png", b"\0" * (1024 * 1024 + 1))
        self.assert_fails("Steam allows 1 MB", preview_file="big.png")

    def test_preview_of_exactly_1mb_is_fine(self):
        self.write("ok.png", b"\0" * (1024 * 1024))
        self.assertIn("previewfile", self.fields(preview_file="ok.png"))


class MainTests(unittest.TestCase):
    def run_main(self, env):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = upload.main(env)
        return code, out.getvalue()

    def base_env(self, **extra):
        env = {
            "GITHUB_WORKSPACE": str(ROOT),
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_NAME": "main",
            "INPUT_ITEM-ID": "123",
            "INPUT_CONTENT-PATH": "test/fixtures/content",
            "INPUT_DESCRIPTION-FILE": "test/fixtures/description.md",
            "INPUT_PREVIEW-FILE": "test/fixtures/preview.png",
            "INPUT_TITLE": 'Title with "quotes"',
            "INPUT_CHANGELOG": "Fixed C:\\path and \"quotes\"",
        }
        env.update(extra)
        return env

    def test_dry_run_prints_vdf_without_credentials(self):
        code, out = self.run_main(self.base_env(**{"INPUT_DRY-RUN": "true"}))
        self.assertEqual(code, 0)
        self.assertIn('"title"\t"Title with \\"quotes\\""', out)
        self.assertIn('"changenote"\t"Fixed C:\\\\path and \\"quotes\\""', out)
        self.assertIn('"description"\t"[h1]My \\"Quoted\\" Mod[/h1]', out)
        self.assertIn("Dry run", out)

    def test_vdf_output_is_written(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            code, _ = self.run_main(self.base_env(**{"INPUT_DRY-RUN": "true", "GITHUB_OUTPUT": str(output)}))
            self.assertEqual(code, 0)
            text = output.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("vdf<<EOF_"))
        self.assertIn('"publishedfileid"\t"123"', text)

    def test_missing_credentials_fail_early(self):
        code, out = self.run_main(self.base_env())
        self.assertEqual(code, 1)
        self.assertIn("::error::Missing STEAM_USERNAME and STEAM_PASSWORD", out)

    def test_steamcmd_failure_prints_logs(self):
        temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp)
        steamcmd = temp / "steamcmd"
        steamcmd.mkdir()
        logs = temp / "home" / "Steam" / "logs"
        logs.mkdir(parents=True)
        (logs / "workshop_log.txt").write_text("workshop log content")
        # Fake steamcmd: a Python script that reports an error but exits with 0
        script = steamcmd / "steamcmd.sh"
        script.write_text("ERROR! Failed to update workshop item (Failure).\n")
        env = self.base_env(STEAM_USERNAME="u", STEAM_PASSWORD="p", STEAMCMDDIR=str(steamcmd),
                            STEAM_HOME=str(temp / "home"))

        original = upload.subprocess.Popen

        def fake_popen(command, **kwargs):
            self.assertEqual(command[1:4], ["+login", "u", "p"])
            vdf = Path(command[5]).read_text(encoding="utf-8")
            self.assertIn('"publishedfileid"\t"123"', vdf)
            return original([sys.executable, "-c", f"print(open({str(script)!r}).read(), end='')"], **kwargs)

        upload.subprocess.Popen = fake_popen
        try:
            code, out = self.run_main(env)
        finally:
            upload.subprocess.Popen = original
        self.assertEqual(code, 1)
        self.assertIn("::error::steamcmd failed", out)
        self.assertIn("workshop log content", out)
        self.assertIn("stderr.txt", out)


if __name__ == "__main__":
    unittest.main()
