from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lightacademia.markdown_preview import (
    ProjectDataframeError,
    ProjectImageError,
    find_markdown_tables,
    find_standalone_dataframes,
    find_standalone_images,
    format_markdown_table_for_latex,
    format_markdown_table_for_plain_text,
    resolve_project_dataframe,
    resolve_project_image,
    rewrite_project_note_links,
)


PROJECT_ROOT = Path(__file__).parents[1]


class FindStandaloneImagesTest(unittest.TestCase):
    def test_finds_local_standalone_image(self) -> None:
        result = find_standalone_images(
            "# Results\n\n![Validation accuracy](assets/accuracy.png)\n\nAfter.\n"
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].target, "assets/accuracy.png")
        self.assertEqual(result[0].alt, "Validation accuracy")
        self.assertEqual((result[0].start_line, result[0].end_line), (2, 3))

    def test_leaves_external_and_inline_images_to_markdown(self) -> None:
        external = find_standalone_images("![Remote](https://example.com/image.png)\n")
        inline = find_standalone_images("Text beside ![Local](assets/image.png).\n")
        nested = find_standalone_images("- ![Nested](assets/image.png)\n")

        self.assertEqual(external, ())
        self.assertEqual(inline, ())
        self.assertEqual(nested, ())


class ResolveProjectImageTest(unittest.TestCase):
    def test_resolves_existing_asset(self) -> None:
        result = resolve_project_image(PROJECT_ROOT, "assets/logo.png")

        self.assertEqual(result, (PROJECT_ROOT / "assets/logo.png").resolve())

    def test_rejects_paths_outside_assets(self) -> None:
        for target in ("README.md", "data/plot.png", "assets/../../README.md"):
            with self.subTest(target=target):
                with self.assertRaises(ProjectImageError):
                    resolve_project_image(PROJECT_ROOT, target)

    def test_rejects_missing_or_unsupported_assets(self) -> None:
        with self.assertRaisesRegex(ProjectImageError, "not found"):
            resolve_project_image(PROJECT_ROOT, "assets/missing.png")
        with self.assertRaisesRegex(ProjectImageError, "Unsupported"):
            resolve_project_image(PROJECT_ROOT, "assets/theme.css")


class DataframeLinkTest(unittest.TestCase):
    def test_finds_standalone_dataframe_link(self) -> None:
        result = find_standalone_dataframes(
            "# Results\n\n[dataframe](data/metrics.csv)\n\nAfter.\n"
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].target, "data/metrics.csv")
        self.assertEqual(result[0].columns, {})
        self.assertIsNone(result[0].annotation_error)
        self.assertEqual((result[0].start_line, result[0].end_line), (2, 3))

    def test_finds_dataframe_column_annotations(self) -> None:
        result = find_standalone_dataframes(
            "[dataframe](data/metrics.csv)\n\n"
            "```dataframe\n"
            "columns:\n"
            "  foo_bar: Foo Bar\n"
            "  asr_dwer_micro: dWER\n"
            "```\n\n"
            "After.\n"
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].columns, {"foo_bar": "Foo Bar", "asr_dwer_micro": "dWER"})
        self.assertEqual((result[0].start_line, result[0].end_line), (0, 7))

    def test_reports_invalid_dataframe_annotations(self) -> None:
        result = find_standalone_dataframes("[dataframe](data/metrics.csv)\n\n```dataframe\ncolumns: nope\n```\n")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].columns, {})
        self.assertIn("columns", result[0].annotation_error or "")

    def test_ignores_non_standalone_and_non_dataframe_links(self) -> None:
        inline = find_standalone_dataframes("See [dataframe](data/metrics.csv) inline.\n")
        ordinary = find_standalone_dataframes("[metrics](data/metrics.csv)\n")
        external = find_standalone_dataframes("[dataframe](https://example.com/metrics.csv)\n")

        self.assertEqual(inline, ())
        self.assertEqual(ordinary, ())
        self.assertEqual(external, ())

    def test_resolves_project_csv(self) -> None:
        with TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "data").mkdir()
            csv_path = project_root / "data" / "metrics.csv"
            csv_path.write_text("epoch,loss\n1,0.5\n", encoding="utf-8")

            expected = csv_path.resolve()
            result = resolve_project_dataframe(project_root, "data/metrics.csv")

            self.assertEqual(result, expected)

    def test_rejects_missing_unsupported_and_outside_dataframes(self) -> None:
        with self.assertRaisesRegex(ProjectDataframeError, "Unsupported"):
            resolve_project_dataframe(PROJECT_ROOT, "README.md")
        with self.assertRaisesRegex(ProjectDataframeError, "not found"):
            resolve_project_dataframe(PROJECT_ROOT, "missing.csv")
        with self.assertRaisesRegex(ProjectDataframeError, "inside the project"):
            resolve_project_dataframe(PROJECT_ROOT, "../outside.csv")


class MarkdownTableTest(unittest.TestCase):
    def test_finds_markdown_table(self) -> None:
        source = "Before\n\n| Name | Score |\n| --- | ---: |\n| Alpha | 10 |\n| Beta longer | 2 |\n\nAfter\n"

        tables = find_markdown_tables(source)

        self.assertEqual(len(tables), 1)
        self.assertEqual((tables[0].start_line, tables[0].end_line), (2, 6))
        self.assertIn("| Beta longer | 2 |", tables[0].source)
        self.assertIn(r"\begin{tabular}", tables[0].latex_text)

    def test_ignores_tables_inside_code_fences(self) -> None:
        source = "```markdown\n| Name | Score |\n| --- | --- |\n```\n"

        self.assertEqual(find_markdown_tables(source), ())

    def test_formats_table_for_plain_text_copying(self) -> None:
        formatted = format_markdown_table_for_plain_text(
            "| Name | Score |\n"
            "| --- | ---: |\n"
            "| Alpha | 10 |\n"
            "| Beta longer | 2 |\n"
        )

        self.assertEqual(
            formatted,
            "| Name        |   Score |\n"
            "|-------------|---------|\n"
            "| Alpha       |      10 |\n"
            "| Beta longer |       2 |",
        )

    def test_formats_table_for_latex_copying(self) -> None:
        formatted = format_markdown_table_for_latex(
            "| Name | Score |\n"
            "| --- | ---: |\n"
            "| Alpha | 10 |\n"
            "| Beta longer | 2 |\n"
        )

        self.assertIn(r"\begin{tabular}{lr}", formatted)
        self.assertIn(r"\toprule", formatted)
        self.assertIn(r"Name", formatted)
        self.assertIn(r"Beta longer &       2 \\", formatted)
        self.assertIn(r"\end{tabular}", formatted)


class RewriteProjectNoteLinksTest(unittest.TestCase):
    def test_rewrites_root_note_links_to_query_params(self) -> None:
        markdown, errors = rewrite_project_note_links("See [Spec](PRD.md).\n", PROJECT_ROOT)

        self.assertEqual(errors, ())
        self.assertIn("[Spec](?project=lightacademia&note=PRD.md)", markdown)

    def test_does_not_rewrite_code_or_images(self) -> None:
        source = "Inline `[Spec](PRD.md)` and ![Spec](PRD.md), then [Spec](PRD.md).\n"

        markdown, errors = rewrite_project_note_links(source, PROJECT_ROOT)

        self.assertEqual(errors, ())
        self.assertIn("`[Spec](PRD.md)`", markdown)
        self.assertIn("![Spec](PRD.md)", markdown)
        self.assertIn("[Spec](?project=lightacademia&note=PRD.md)", markdown)

    def test_reports_missing_note_links(self) -> None:
        markdown, errors = rewrite_project_note_links("See [Missing](Missing.md).\n", PROJECT_ROOT)

        self.assertEqual(markdown, "See [Missing](Missing.md).\n")
        self.assertEqual(errors, ("Note not found: `Missing.md`.",))


if __name__ == "__main__":
    unittest.main()
