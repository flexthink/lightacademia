from __future__ import annotations

import unittest

from lightacademia.actions import build_action_prompt, parse_note_actions


class ParseNoteActionsTest(unittest.TestCase):
    def test_parses_multiple_action_fences(self) -> None:
        result = parse_note_actions(
            """# Results

```action
name: Compare validation accuracy

Retrieve this.
Plot this.
```

```python
print("not an action")
```

```action
name: Add placeholder

Add ten lines of Lorem Ipsum.
```
"""
        )

        self.assertEqual(
            [action.name for action in result.actions],
            ["Compare validation accuracy", "Add placeholder"],
        )
        self.assertEqual(result.actions[0].instructions, "Retrieve this.\nPlot this.")
        self.assertEqual(result.actions[0].line, 3)
        self.assertEqual(result.actions[0].end_line, 8)
        self.assertEqual(result.actions[1].line, 14)
        self.assertEqual(result.actions[1].end_line, 18)
        self.assertEqual(result.errors, ())

    def test_reports_malformed_blocks_without_losing_valid_actions(self) -> None:
        result = parse_note_actions(
            """```action
Compare results

Plot them.
```

```action
name: Valid action

Update the note.
```
"""
        )

        self.assertEqual([action.name for action in result.actions], ["Valid action"])
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].line, 1)

    def test_requires_blank_separator_and_instructions(self) -> None:
        no_separator = parse_note_actions("```action\nname: Broken\nDo this\n```\n")
        no_body = parse_note_actions("```action\nname: Broken\n\n```\n")

        self.assertIn("blank line", no_separator.errors[0].message)
        self.assertIn("cannot be empty", no_body.errors[0].message)

    def test_preserves_markdown_in_instruction_body(self) -> None:
        result = parse_note_actions(
            """````action
name: Analyze metrics

1. Read `metrics.csv`.
2. Run:

```python
print("analyze")
```
````
"""
        )

        self.assertIn("1. Read `metrics.csv`.", result.actions[0].instructions)
        self.assertIn('```python\nprint("analyze")\n```', result.actions[0].instructions)

    def test_builds_prompt_with_optional_user_request(self) -> None:
        action = parse_note_actions(
            "```action\nname: Plot results\n\nRead metrics and add a plot.\n```\n"
        ).actions[0]

        prompt = build_action_prompt(action, "Results.md", "Use the latest run only.")

        self.assertIn("Source note: Results.md", prompt)
        self.assertIn("Action name: Plot results", prompt)
        self.assertIn("Read metrics and add a plot.", prompt)
        self.assertIn("Additional user request:\nUse the latest run only.", prompt)


if __name__ == "__main__":
    unittest.main()
