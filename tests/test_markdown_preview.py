from __future__ import annotations

import unittest
from pathlib import Path

from lightacademia.markdown_preview import (
    ProjectImageError,
    find_standalone_images,
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
