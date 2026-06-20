# ✨🎻📚 Light Academia

## Purpose
The purpose of this application is to use artificial intelligence agents to
- Help manage a list of research projects
- Maintain markdown notes on each project
- Quickly retrieve and tabulate key results

## Product Direction
Light Academia is intended to evolve into a local-first research birds-eye view tool.

Many PhD students and researchers already maintain ongoing notes, but the workflow is often scattered across several tools:
- CLI tools to start experiments
- CLI tools to collect experiment data
- Jupyter notebooks to visualize and tabulate results
- Manually created presentation slides, such as Marp decks
- Markdown note tools, such as Obsidian

Light Academia does not replace those tools. It provides connective tissue between them. The goal is to use an agent to automate the boring parts of research project upkeep: collecting outputs, generating quick tables and visualizations, updating markdown notes, and maintaining a git-backed project history.

Eventually, Light Academia should help researchers understand the state of a project without digging through terminal history, logs, notebooks, dashboards, slides, and scattered notes.

## Scope
In scope eventually:
- Run ablations using existing user-provided CLI tools
- Pull logs, training job outputs, and exported metrics back into the project record
- Generate quick data visualizations using Pandas
- Generate lightweight tables from result artifacts
- Integrate generated tables and visualizations into markdown notes
- Help maintain a project-level birds-eye view across notes, results, and agent actions

Permanently out of scope:
- Coding the experiments
- Paper writing
- Working directly with actual datasets

## Logo
The logo composed of an artistic renditions of these three emojis superimposed:
✨🎻📚
And embodying the Light Academia aesthetic.

## Development Stack
The application will be developed in Python using Streamlit. By default, Codex will be the agent of choice. A modular architecture will be developed to allow adding support for other agents in the future.

Git will be used for versioning.

This component will be used 
https://github.com/bouzidanas/streamlit-code-editor

## Notebook Folder
- The content is managed in a central notebook folder
- The default notebook folder is `./notebook`
- The notebook folder can be overridden at startup with `--notebook`
- The notebook folder is not user-editable from the application UI
- The notebook can contain zero or more Projects
- A Project is a folder inside the notebook folder

## Tools Folder
- Researcher-specific tools are managed in a separate tools folder
- The default tools folder is `./tools`
- The tools folder can be overridden at startup with `--tools`
- The tools folder is not user-editable from the application UI
- The tools folder can contain a `SKILL.md` file describing available commands, expected arguments, environment requirements, output formats, and safety notes
- The tools folder is intended for commands that interact with external research systems such as clusters, cloud services, experiment trackers, and services like wandb

## Project Structure
- A Note is a markdown file inside a Project
- Each project contains an index Note called `Home.md` describing the project at a high level
- A project can contain additional Notes
- Notes are flat files inside the project root. Notes do not have subfolders.
- A project may contain a `SKILL.md` file describing project-specific instructions for using researcher tools
- Each project contains a `data` directory for derived research artifacts
- The `data` directory is for logs, training job outputs, exported metrics, query results, CSV or JSON summaries, and lightweight tables used by notes
- The `data` directory is not for raw datasets or full dataset management
- Each project contains an `assets` directory for generated images referenced by notes
- Notes reference project images with project-relative Markdown paths such as `![Caption](assets/plot.png)`
- The preview renders supported image links from the selected project's `assets` directory
- Project image paths are restricted to `assets` and may not escape the selected project
- Each project contains a `code` directory for scratch analysis scripts
- The `code` directory is for ad hoc scripts that fetch, parse, summarize, tabulate, or plot project artifacts
- The `code` directory is not for experiment implementation code

## Project Creation
- When a project is created, a local git repository is initialized for it
- If git initialization fails, the user gets an error message
- `Home.md` is created automatically with boilerplate content
- `SKILL.md` is created automatically with terse boilerplate instructions
- `meta.yaml` is created to store machine-readable project metadata
- A folder called `chats` is created
- A folder called `archived` is created
- A folder called `data` is created
- A folder called `assets` is created
- A folder called `code` is created

## Archiving
- When a project is archived
    - The whole project is moved to `notebook/archived/{project}`
    - The project folder structure is preserved
    - The move should be performed with `git mv` when possible
- When a note is archived
    - The note is moved to `{project}/archived/{note}`
    - The move should be performed with `git mv` when possible

## Versioning and Saving
- Each project is its own local git repository
- The application itself may also be versioned with git independently
- Notes are saved automatically in an Overleaf-style workflow
- The automatic commit inactivity period is a startup parameter, not a user-facing UI option
- Automatic commits are created
    - After a configurable period of inactivity, defaulting to 5 minutes
    - When navigating away from the current note
    - Before running an agent command
- Commits created after agent actions use commit messages prefixed with `[agent]`
- The `[agent]` prefix makes agent-generated updates distinguishable from human/app checkpoints in git history
- The application does not need to provide a visible commit history or rollback UI for now
- Users can use the git command line directly for history and rollback use cases

## User Interface
- Top
  - The Light Academia logo
  - The words Light Academia
- Left
  - A dropdown of projects (with buttons to add or archive, no delete)
  - A list of notes in the current project
    - With buttons to add a new note or archive
    - If the user selects a note, the editor will show it
- Right
  - The title of the note
  - A Rename button
- Bottom
  - The agent chat window (multi-line)
  - An action selector next to the agent prompt
    - The selector defaults to `Choose an action...`
    - The selector lists actions defined in the current note only

## Note Actions
- A note may define reusable agent actions in fenced Markdown blocks with the language `action`
- Actions are scoped to the current note; actions from other notes are not shown
- Action discovery is performed locally by parsing the note and does not require an LLM call
- An action block has a required `name` field followed by a blank line and an unrestricted instruction body
- The `name` is used as the label in the action selector
- The instruction body is added to the agent prompt when the action is selected and run
- The application also includes the source note path and action name in the agent context and chat log
- Action blocks remain visible as ordinary code blocks in the rendered Markdown preview for now
- The preview displays a `Run: {action name}` button with a bolt icon immediately above each valid action block
- Clicking a preview action button selects that action in the pinned action selector; it does not run the agent immediately
- The action list is refreshed when the current note changes, is saved, or is reloaded after an agent run
- Navigating to another note resets the action selection to `Choose an action...`
- Malformed action blocks are ignored and reported to the user without preventing the note from loading
- Running an action does not automatically remove it or mark it complete

Example:

````markdown
```action
name: Compare validation accuracy

Retrieve the relevant results.
Plot the comparison.
Calculate confidence intervals.
Add the results to this note.
```
````

## Workflow Notes
- A log of agent chats is kept in {project}/chats/{YYYY-mm-dd}.md
- When an agent action is executed, an entry is appended to the chat log for that day.
- While an agent action is running, the pinned agent section displays a real-time activity feed
- The Codex implementation uses `codex exec --json` and renders its JSONL events deterministically without an additional LLM call
- The activity feed includes emitted reasoning, agent messages, commands and command output, file changes, tool calls, plan updates, errors, and usage events
- Agent execution remains non-interactive and does not prompt the user for input
- The chat log should include
    - The user prompt
    - The agent response
    - Tool actions
    - File changes
- Agent context and permissions
    - Agent implementations are accessed through an application-level agent abstraction
    - The initial agent implementation uses the Codex CLI in non-interactive mode
    - The agent may read files in the selected project
    - The agent may update files only in the selected project
    - The agent may write derived artifacts into the selected project's `data` directory
    - The agent may write generated images into the selected project's `assets` directory
    - The agent may write scratch analysis scripts into the selected project's `code` directory
    - If the selected project contains `SKILL.md`, the agent should use it as guidance for project-specific tool usage
    - The agent may read files from a temporary copy of the configured tools folder
    - The agent may run shell commands from a temporary copy of the configured tools folder
    - If the tools folder contains `SKILL.md`, the agent should use it as guidance for available researcher-specific tools
    - The agent may not update the tools folder
    - The agent may not access folders outside the selected project and temporary tools copy
    - The agent may execute sandboxed shell commands, similar to VS Code
    - The agent may not run shell commands on compute clusters except implicitly through commands in the configured tools folder
    - The agent may use outbound network access from shell commands
    - The Codex workspace-write sandbox is configured with `sandbox_workspace_write.network_access=true`
    - The agent may not download or install software from the Internet
    - The agent may not run git actions against the project
    - Git actions are reserved for the application
    - The agent does not need to ask before editing
    - The agent does not need to show a proposed diff before applying changes
    - The application commits current changes before running an agent command
- An agent's write workspace is restricted to the selected project

## Note
This application is minimalistic and "quick-and-dirty" by design. It is intended to be used locally
