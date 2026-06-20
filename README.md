# Light Academia

Local-first research notes with an agent-assisted project workspace.

## Vibe coding disclaimer

This project was developed with substantial AI assistance and prioritizes functionality over software engineering rigor. The code may contain
errors, insecure assumptions, or unexpected behavior. Review and test it
carefully before using it with important data or in a production environment.


## Security and safety disclaimer
This tool is expected to be run locally in environments with no access to highly sensitive data. While it uses Web technologies for maximum portability, it should never be exposed over the Internet.

It is the user's responsibility to ensure that their agent set-up is adequately sandboxed. The developers of the tool are not responsible for any unwanted or undesirable behaviour of the tool or of the AI agent.


## Run

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Use a custom notebook folder:

```bash
python -m streamlit run app.py -- --notebook ./my-notebook
```

Use a custom automatic commit inactivity period:

```bash
python -m streamlit run app.py -- --autocommit-seconds 600
```

Use a custom researcher tools folder:

```bash
python -m streamlit run app.py -- --tools ./my-tools
```

The tools folder can contain `SKILL.md` describing available researcher-specific commands, such as cluster, cloud, or wandb helpers.

Each project can also contain its own `SKILL.md` for project-specific guidance on how to use those tools.

## Note actions

The active note can define reusable agent actions. Actions are parsed locally with
`markdown-it-py` and appear in the selector beside the agent prompt.

````markdown
```action
name: Compare validation accuracy

Retrieve the relevant results.
Plot the comparison.
Add the results to this note.
```
````

The action body can be run by itself or combined with an additional prompt.
In the preview, a bolt button above each action block selects that action in the
pinned agent controls without running it immediately.

## Project images

Generated images belong in the selected project's `assets/` directory and can be
embedded in notes with project-relative Markdown:

```markdown
![Validation accuracy](assets/validation-accuracy.png)
```

The preview resolves supported local image links only within that project's
`assets/` directory.

## Agent

The app uses an agent abstraction in `lightacademia/agents.py`.

The agent instructions are stored in `AGENT_PROMPT.md`. Review this file before
using the app and adjust its rules for your environment and risk tolerance. Keep
the `{{project_name}}`, `{{project_dir}}`, `{{tools_dir}}`, `{{tools_root}}`,
`{{current_note}}`, and `{{user_prompt}}` placeholders where their runtime values
should be inserted.

The default implementation uses the Codex CLI:

- Runs `codex exec`
- Uses `codex exec --json` to stream detailed progress into the pinned agent section
- Displays Codex events directly without a separate summarization model call
- Uses the selected project as the working root
- Adds a temporary copy of the configured tools folder as an auxiliary workspace
- Uses `workspace-write` sandboxing
- Enables outbound network access for commands inside the Codex workspace-write sandbox
- Tells Codex to write derived outputs under `data/` and scratch scripts under `code/`
- Tells Codex to read project-level `SKILL.md` when present
- Tells Codex to read and run tools from the configured tools folder
- Prevents Codex from editing the configured tools folder by exposing only a temporary copy
- Tells Codex not to access folders outside the selected project and tools folder
- Tells Codex not to download or install software from the Internet
- Tells Codex to interact with compute clusters only through configured tools
- Tells Codex not to run git commands
- Leaves git checkpoints and commits to the application
- Uses `[agent]`-prefixed commit messages for commits created after agent actions
