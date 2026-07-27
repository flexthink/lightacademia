from __future__ import annotations

import unittest

from lightacademia.routing import WorkspaceRoute, parse_workspace_hash, workspace_hash


class WorkspaceRoutingTest(unittest.TestCase):
    def test_round_trips_project_and_note_with_spaces(self) -> None:
        route = workspace_hash("Cluster Maintenance", "Experiment Board.md")

        self.assertEqual(
            route,
            "#/project/Cluster%20Maintenance/note/Experiment%20Board.md",
        )
        self.assertEqual(
            parse_workspace_hash(route),
            WorkspaceRoute("Cluster Maintenance", "Experiment Board.md"),
        )

    def test_encodes_route_delimiters_inside_names(self) -> None:
        route = workspace_hash("A/B", "Results #1.md")

        self.assertEqual(
            parse_workspace_hash(route),
            WorkspaceRoute("A/B", "Results #1.md"),
        )

    def test_rejects_unrelated_or_incomplete_hashes(self) -> None:
        self.assertIsNone(parse_workspace_hash(""))
        self.assertIsNone(parse_workspace_hash("#section"))
        self.assertIsNone(parse_workspace_hash("#/project/"))


if __name__ == "__main__":
    unittest.main()
