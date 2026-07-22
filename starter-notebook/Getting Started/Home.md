# Getting Started

Light Academia is a local-first research notebook for keeping notes, experiment outputs, quick analyses, and agent-assisted updates together.

## Projects and Notes

Use the project selector in the sidebar to move between research projects. Each project contains Markdown notes and its own Git history.

- Create projects and notes with the buttons in the sidebar.
- Select a note to open it.
- Use the View/Edit button to show or hide the Markdown source.
- Changes save automatically. Light Academia creates checkpoints after inactivity and when you navigate away.
- Open History to inspect an earlier revision without changing the current note.

Project and note names may contain spaces.

## Agent Chat

Expand **Agent chat** at the bottom of the page, enter a request, and run the configured agent. The agent can update files only inside the selected project and can read and run commands from the researcher tools folder.

Light Academia saves current edits before an agent run, shows live progress, reloads changed notes afterward, and records agent changes in Git with an `[agent]` prefix.

See [Agent and Actions](Agent%20and%20Actions.md) for reusable actions and project guidance.

## Research Outputs

Projects separate generated material by purpose:

- `data/` contains logs, metrics, CSV files, and other experiment outputs used for analysis.
- `assets/` contains plots and images embedded in notes.
- `code/` contains small scripts used to collect, tabulate, or visualize results.
- `SKILL.md` contains project-specific instructions for the agent.

## Tools

Open **Tools** from the sidebar to browse and edit the researcher-specific toolkit. Tools have their own Git history and are shared across projects.

See [Tools and Sync](Tools%20and%20Sync.md) for tool management, history, import/export, and project synchronization.

## Markdown Extras

Embed a project image with ordinary Markdown:

```markdown
![Validation plot](assets/validation-plot.png)
```

Display a CSV as an interactive, sortable table:

````markdown
[dataframe](data/results.csv)

```dataframe
columns:
  validation_accuracy: Validation Accuracy
```
````

Use the toolbar to insert images and links at the current editor position. Rendered tables and images include copy controls for moving results into chat, slides, or papers.
