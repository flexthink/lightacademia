# Agent and Actions

## Asking the Agent

The agent works within the currently selected project. Give it concrete maintenance and analysis tasks, such as collecting logs through an existing tool, summarizing results, creating a quick plot, or updating the current note.

The agent is intended for research organization and lightweight analysis. Experiment implementation, paper writing, and dataset management remain outside Light Academia's scope.

## Project Instructions

Edit the project's `SKILL.md` to describe experiment naming conventions, relevant researcher tools, expected output locations, and project-specific constraints. The agent reads this file before working.

## Reusable Actions

Actions are note-local prompt macros. Add one with the toolbar or write an action block directly:

````markdown
```action
name: Compare validation accuracy

Retrieve the latest validation metrics.
Create a comparison table and plot.
Add both to this note.
```
````

The preview keeps the description collapsed and shows a Run button above it. Selecting an action adds its instructions to the agent request; it does not execute automatically.

Return to [Getting Started](Home.md).
