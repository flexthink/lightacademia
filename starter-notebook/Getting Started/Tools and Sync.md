# Tools and Sync

## Researcher Tools

The Tools workspace contains researcher-specific scripts for interacting with clusters and services. It is global rather than project-specific.

- Create folders and text or code files from the Tools sidebar.
- Select files to edit them with extension-aware syntax highlighting.
- Download non-text files.
- Use History to inspect earlier revisions.
- Import or export the current toolkit as a ZIP archive.
- Deleting a file or folder requires confirmation and records the deletion in Git.

Add a `SKILL.md` to the tools root explaining available commands and their expected arguments. The agent may read and run tools, but it cannot edit the configured tools directory.

## Project Sync

Open Settings and configure one Git remote URL for the selected project. The Sync button stages note changes, commits when needed, pulls, and pushes. Merge conflicts are left for you to resolve and are not treated as an unexpected application failure.

Return to [Getting Started](Home.md).
