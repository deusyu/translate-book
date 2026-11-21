import contextlib
import hashlib
import html
import io
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import figure_text  # noqa: E402
from manifest import create_manifest, file_hash, load_manifest  # noqa: E402


def png(width, height, salt=b""):
    """A valid PNG; salt after IEND changes the hash but not the image."""
    raw = b"".join(b"\x00" + b"\xff" * (width * 3) for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"") + salt)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


SVG = (
    '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg">'
    '<text x="0" y="10">Load</text>'
    '<text y="20"><tspan x="0" y="20">Web</tspan><tspan x="0" y="32">server</tspan></text>'
    '<text>42</text></svg>'
)


class ImageInfoTests(unittest.TestCase):
    def test_png(self):
        self.assertEqual(figure_text.image_info(png(400, 300)), ("png", 400, 300))

    def test_gif(self):
        data = b"GIF89a" + struct.pack("<HH", 120, 80) + b"\x00" * 8
        self.assertEqual(figure_text.image_info(data), ("gif", 120, 80))

    def test_jpeg_reads_size_from_the_frame_header(self):
        app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
        sof0 = b"\xff\xc0" + struct.pack(">HBHH", 17, 8, 90, 160) + b"\x00" * 10
        self.assertEqual(figure_text.image_info(b"\xff\xd8" + app0 + sof0), ("jpeg", 160, 90))

    def test_webp_extended(self):
        payload = b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4 + (639).to_bytes(3, "little") + (479).to_bytes(3, "little")
        data = b"RIFF" + struct.pack("<I", len(payload) + 4) + b"WEBP" + payload
        self.assertEqual(figure_text.image_info(data), ("webp", 640, 480))

    def test_svg_with_byte_order_mark(self):
        self.assertEqual(figure_text.image_info(b"\xef\xbb\xbf" + SVG.encode()), ("svg", None, None))

    def test_unknown_format(self):
        self.assertEqual(figure_text.image_info(b"II*\x00 tiff"), (None, None, None))


class SvgTextTests(unittest.TestCase):
    def test_one_block_per_text_element_with_lines_joined(self):
        self.assertEqual(figure_text.svg_text_blocks(SVG.encode()), ["Load", "Web server", "42"])

    def test_kerning_runs_on_one_line_form_one_word(self):
        data = b'<svg xmlns="http://www.w3.org/2000/svg"><text><tspan x="0" y="0">W</tspan><tspan x="9" y="0">orld</tspan></text></svg>'
        self.assertEqual(figure_text.svg_text_blocks(data), ["World"])

    def test_dy_and_inkscape_lines_start_new_words(self):
        data = (
            b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd">'
            b'<text><tspan>Data</tspan><tspan x="0" dy="1.2em">store</tspan></text>'
            b'<text y="5"><tspan sodipodi:role="line" y="5">Message</tspan><tspan sodipodi:role="line" y="5">queue</tspan></text>'
            b'</svg>'
        )
        self.assertEqual(figure_text.svg_text_blocks(data), ["Data store", "Message queue"])

    def test_entity_declarations_and_broken_xml_yield_nothing(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]><svg><text>&a;</text></svg>'
        self.assertEqual(figure_text.svg_text_blocks(bomb), [])
        self.assertEqual(figure_text.svg_text_blocks(b"<svg><text>open"), [])


class EscapeTests(unittest.TestCase):
    CASES = [
        "Client *req* [1] <b> $5 and $10 x_1 a~b~ c^2^ `code`",
        "@doe99 says hi to john@example.com",
        "# not a heading",
        "1. Step one",
        "2023) a year",
        "(a) item",
        "iv. roman",
        "- dash",
        "+ plus",
        ": colon",
        "| pipe",
        "<tag> and [^1] and ![img](x.png)",
        "ends with a backslash \\",
    ]

    def test_escapes_inline_specials_and_leading_markers(self):
        self.assertEqual(figure_text.escape_markdown("a*b_c [d] <e> $f"), r"a\*b\_c \[d\] \<e\> \$f")
        self.assertEqual(figure_text.escape_markdown("# h"), r"\# h")
        self.assertEqual(figure_text.escape_markdown("1. Step"), r"1\. Step")
        self.assertEqual(figure_text.escape_markdown("(a) x"), r"\(a\) x")
        self.assertEqual(figure_text.escape_markdown("@cite me@home"), r"\@cite me@home")
        self.assertEqual(figure_text.escape_markdown("  two\n lines  "), "two lines")
        self.assertEqual(figure_text.escape_markdown("e.g. this"), "e.g. this")

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc is not installed")
    def test_pandoc_renders_every_item_literally(self):
        md = "\n".join(f"- {figure_text.escape_markdown(text)}" for text in self.CASES) + "\n"
        rendered = subprocess.run(
            ["pandoc", "--from", "markdown+smart+east_asian_line_breaks", "--to", "html5", "--wrap=none"],
            input=md, capture_output=True, text=True, check=True,
        ).stdout
        items = [html.unescape(item) for item in re.findall(r"<li>(.*?)</li>", rendered, re.S)]
        self.assertEqual(items, self.CASES)


class SidecarTests(unittest.TestCase):
    SHA = "a" * 64

    def sidecar(self, **overrides):
        data = {"version": 1, "sha256": self.SHA, "kind": "diagram", "blocks": ["Client"]}
        data.update(overrides)
        return data

    def error(self, data, sha=SHA):
        return figure_text.check_sidecar(data, sha)[1]

    def test_valid(self):
        self.assertIsNone(self.error(self.sidecar()))
        self.assertIsNone(self.error(self.sidecar(kind="photo", blocks=[])))

    def test_rejects_schema_violations(self):
        self.assertIn("version", self.error(self.sidecar(version=2)))
        self.assertIn("sha256", self.error(self.sidecar(sha256="b" * 64)))
        self.assertIn("kind", self.error(self.sidecar(kind="poster")))
        self.assertIn("list of strings", self.error(self.sidecar(blocks="Client")))
        self.assertIn("list of strings", self.error(self.sidecar(blocks=["ok", 3])))
        self.assertIn("file name", self.error(self.sidecar(sha256="x"), sha="x"))
        self.assertIn("object", self.error(["Client"]))

    def test_rejects_oversized_text(self):
        self.assertIn("at most", self.error(self.sidecar(blocks=["x"] * (figure_text.MAX_BLOCKS + 1))))
        self.assertIn("block 1", self.error(self.sidecar(blocks=["x" * (figure_text.MAX_BLOCK_CHARS + 1)])))
        many = ["x" * figure_text.MAX_BLOCK_CHARS] * (figure_text.MAX_TOTAL_CHARS // figure_text.MAX_BLOCK_CHARS + 1)
        self.assertIn("in total", self.error(self.sidecar(blocks=many)))

    def test_unreadable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{self.SHA}.json"
            path.write_text("{not json", encoding="utf-8")
            sidecar, error = figure_text.load_sidecar(path)
        self.assertIsNone(sidecar)
        self.assertIn("cannot read JSON", error)


class WorkspaceMixin:
    FLOW = png(400, 300)
    PHOTO = png(300, 200, salt=b"photo")

    CHUNK1 = (
        "# Chapter 1\n\nIntro text.\n\n"
        "![Figure 1.1: Request flow](images/flow.png)\n\n"
        "Figure 1.1 How a request flows.\n\n"
        "More text.\n\n"
        "![](images/icon.png) inline icon\n\n"
        "```markdown\n![example](images/in-code.png)\n```\n"
    )
    CHUNK2 = (
        "![Photo](images/photo.png)\n\n"
        "![Architecture](images/arch.svg)\n\n"
        "<img src=\"images/copy%20of%20flow.png\" alt=\"Same figure\">\n\n"
        "![Remote](https://example.com/x.png) ![Gone](images/missing.png)\n\n"
        "End.\n"
    )

    def make_workspace(self, tmp):
        temp_dir = Path(tmp)
        images = temp_dir / "images"
        images.mkdir()
        (images / "flow.png").write_bytes(self.FLOW)
        (images / "copy of flow.png").write_bytes(self.FLOW)
        (images / "icon.png").write_bytes(png(16, 16))
        (images / "photo.png").write_bytes(self.PHOTO)
        (images / "arch.svg").write_text(SVG, encoding="utf-8")
        (temp_dir / "chunk0001.md").write_text(self.CHUNK1, encoding="utf-8")
        (temp_dir / "chunk0002.md").write_text(self.CHUNK2, encoding="utf-8")
        (temp_dir / "input.md").write_text(self.CHUNK1 + "\n" + self.CHUNK2, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            create_manifest(str(temp_dir), ["chunk0001.md", "chunk0002.md"], str(temp_dir / "input.md"))
        return temp_dir

    def write_sidecar(self, temp_dir, data, kind="diagram", blocks=("Client", "Load balancer")):
        digest = sha256(data)
        folder = temp_dir / figure_text.SIDECAR_DIR
        folder.mkdir(exist_ok=True)
        path = folder / f"{digest}.json"
        path.write_text(json.dumps({"version": 1, "sha256": digest, "kind": kind, "blocks": list(blocks)}),
                        encoding="utf-8")
        return path

    def snapshot(self, temp_dir):
        return {str(p.relative_to(temp_dir)): p.read_bytes() for p in sorted(temp_dir.rglob("*")) if p.is_file()}


class PlanTests(WorkspaceMixin, unittest.TestCase):
    def test_sorts_images_by_what_they_still_need(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            self.write_sidecar(temp_dir, self.PHOTO, kind="photo", blocks=())
            result = figure_text.plan(str(temp_dir))

        self.assertEqual(result["counts"], {"queue": 1, "done": 1, "svg": 1, "skipped": 3})
        [queued] = result["queue"]
        self.assertEqual(queued["sha256"], sha256(self.FLOW))
        self.assertEqual(queued["paths"], ["images/flow.png", "images/copy of flow.png"])
        self.assertEqual(queued["chunks"], ["chunk0001.md", "chunk0002.md"])
        self.assertEqual((queued["format"], queued["width"], queued["height"]), ("png", 400, 300))
        self.assertEqual(queued["sidecar"], f"figure_text/{sha256(self.FLOW)}.json")
        self.assertEqual(result["done"][0]["paths"], ["images/photo.png"])
        self.assertEqual(result["svg"], [{"path": "images/arch.svg", "chunks": ["chunk0002.md"], "blocks": 2}])
        reasons = {entry["path"]: entry["reason"] for entry in result["skipped"]}
        self.assertEqual(reasons, {
            "images/icon.png": "small",
            "images/missing.png": "missing",
            "https://example.com/x.png": "not_local",
        })

    def test_invalid_sidecar_keeps_the_image_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            self.write_sidecar(temp_dir, self.FLOW, kind="poster")
            queue = {entry["sha256"]: entry for entry in figure_text.plan(str(temp_dir))["queue"]}
        self.assertIn("kind must be one of", queue[sha256(self.FLOW)]["sidecar_error"])
        self.assertNotIn("sidecar_error", queue[sha256(self.PHOTO)])

    def test_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            before = self.snapshot(temp_dir)
            figure_text.plan(str(temp_dir))
            self.assertEqual(self.snapshot(temp_dir), before)

    def test_size_thresholds_are_adjustable(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            result = figure_text.plan(str(temp_dir), min_side=10, min_area=100)
        self.assertEqual(result["counts"]["queue"], 3)


class InjectTests(WorkspaceMixin, unittest.TestCase):
    def inject(self, temp_dir):
        return figure_text.inject(str(temp_dir))

    def test_inserts_text_after_the_caption_and_only_for_rendered_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            self.write_sidecar(temp_dir, self.FLOW, blocks=["Client", "Load balancer", "Client", "200", "1. Send *it*"])
            self.write_sidecar(temp_dir, self.PHOTO, kind="photo", blocks=["Smile"])
            result = self.inject(temp_dir)
            chunk1 = (temp_dir / "chunk0001.md").read_text(encoding="utf-8")
            chunk2 = (temp_dir / "chunk0002.md").read_text(encoding="utf-8")

        self.assertEqual(result["blocks"], 3)
        self.assertEqual(result["updated_chunks"], ["chunk0001.md", "chunk0002.md"])
        self.assertIn(
            "Figure 1.1 How a request flows.\n\n"
            '::: {.figure-text image="images/flow.png"}\n'
            "- Client\n- Load balancer\n- 1\\. Send \\*it\\*\n:::\n\nMore text.",
            chunk1,
        )
        self.assertIn("![example](images/in-code.png)\n```\n", chunk1)
        self.assertNotIn("Smile", chunk2)
        self.assertIn(
            '![Architecture](images/arch.svg)\n\n::: {.figure-text image="images/arch.svg"}\n- Load\n- Web server\n:::\n',
            chunk2,
        )
        self.assertIn('::: {.figure-text image="images/copy%20of%20flow.png"}', chunk2)

    def test_is_idempotent_and_leaves_images_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            self.write_sidecar(temp_dir, self.FLOW)
            images_before = {p.name: p.read_bytes() for p in (temp_dir / "images").iterdir()}
            self.inject(temp_dir)
            after_first = self.snapshot(temp_dir)
            second = self.inject(temp_dir)
            self.assertEqual(second["updated_chunks"], [])
            self.assertEqual(self.snapshot(temp_dir), after_first)
            self.assertEqual({p.name: p.read_bytes() for p in (temp_dir / "images").iterdir()}, images_before)

    def test_removing_the_sidecar_restores_the_chunk(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            sidecar = self.write_sidecar(temp_dir, self.FLOW)
            self.inject(temp_dir)
            sidecar.unlink()
            (temp_dir / "images" / "arch.svg").write_text("<svg></svg>", encoding="utf-8")
            self.inject(temp_dir)
            self.assertEqual((temp_dir / "chunk0001.md").read_text(encoding="utf-8"), self.CHUNK1)
            self.assertEqual((temp_dir / "chunk0002.md").read_text(encoding="utf-8"), self.CHUNK2)

    def test_updates_manifest_and_reports_translations_to_redo(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            self.write_sidecar(temp_dir, self.FLOW)
            (temp_dir / "output_chunk0001.md").write_text("已翻译。\n", encoding="utf-8")
            result = self.inject(temp_dir)
            entries = {e["source_file"]: e["source_hash"] for e in load_manifest(str(temp_dir))["chunks"]}
            self.assertEqual(entries["chunk0001.md"], file_hash(temp_dir / "chunk0001.md"))
            self.assertEqual(entries["chunk0002.md"], file_hash(temp_dir / "chunk0002.md"))
        self.assertEqual(result["stale_outputs"], ["output_chunk0001.md"])
        self.assertEqual(result["manifest_left_unchanged"], [])

    def test_leaves_manifest_entry_of_a_chunk_edited_since_splitting(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            self.write_sidecar(temp_dir, self.FLOW)
            recorded = load_manifest(str(temp_dir))["chunks"][0]["source_hash"]
            (temp_dir / "chunk0001.md").write_text(self.CHUNK1 + "\nEdited by hand.\n", encoding="utf-8")
            result = self.inject(temp_dir)
            self.assertEqual(load_manifest(str(temp_dir))["chunks"][0]["source_hash"], recorded)
        self.assertEqual(result["manifest_left_unchanged"], ["chunk0001.md"])

    def test_invalid_sidecar_is_reported_and_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            self.write_sidecar(temp_dir, self.FLOW, kind="poster")
            result = self.inject(temp_dir)
            chunk1 = (temp_dir / "chunk0001.md").read_text(encoding="utf-8")
        self.assertEqual(chunk1, self.CHUNK1)
        [invalid] = result["invalid_sidecars"]
        self.assertEqual(invalid["file"], f"figure_text/{sha256(self.FLOW)}.json")

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc is not installed")
    def test_translated_blocks_render_below_their_figure(self):
        import merge_and_build

        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            self.write_sidecar(temp_dir, self.FLOW)
            self.inject(temp_dir)
            for name in ("chunk0001.md", "chunk0002.md"):
                text = (temp_dir / name).read_text(encoding="utf-8")
                (temp_dir / f"output_{name}").write_text(text.replace("- Client", "- 客户端"), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(merge_and_build.merge_markdown_files(str(temp_dir)))
                self.assertTrue(merge_and_build.convert_with_pandoc(
                    str(temp_dir / "output.md"), str(temp_dir / "output.html"), "T", "zh-CN"))
            rendered = (temp_dir / "output.html").read_text(encoding="utf-8")

        self.assertIn('<div class="figure-text" data-image="images/flow.png">', rendered)
        self.assertIn("<li>客户端</li>", rendered)
        self.assertNotIn(":::", rendered)
        self.assertEqual(rendered.count("<img"), 7)


class CliTests(WorkspaceMixin, unittest.TestCase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = figure_text.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_plan_prints_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            code, out, _ = self.run_main("plan", str(temp_dir))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["counts"]["queue"], 2)

    def test_validate_fails_on_an_invalid_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            self.write_sidecar(temp_dir, self.PHOTO, kind="photo", blocks=())
            self.write_sidecar(temp_dir, b"unreferenced", kind="chart")
            ok_code, ok_out, _ = self.run_main("validate", str(temp_dir))
            self.write_sidecar(temp_dir, self.FLOW, kind="poster")
            bad_code, bad_out, _ = self.run_main("validate", str(temp_dir))
        self.assertEqual(ok_code, 0)
        self.assertEqual(json.loads(ok_out)["unused"], [f"figure_text/{sha256(b'unreferenced')}.json"])
        self.assertEqual(bad_code, 1)
        self.assertEqual(len(json.loads(bad_out)["invalid"]), 1)

    def test_inject_refuses_without_pandoc(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_workspace(tmp)
            self.write_sidecar(temp_dir, self.FLOW)
            with mock.patch.object(figure_text.shutil, "which", return_value=None):
                code, _, err = self.run_main("inject", str(temp_dir))
            chunk1 = (temp_dir / "chunk0001.md").read_text(encoding="utf-8")
        self.assertEqual(code, 1)
        self.assertIn("pandoc not found", err)
        self.assertEqual(chunk1, self.CHUNK1)


if __name__ == "__main__":
    unittest.main()
