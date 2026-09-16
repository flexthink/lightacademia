# Gardener

Gardener schedules note-local actions so routine research maintenance can run with less repeated Robot work.

## Add an Action

Create an ordinary `action` block in a note, then use the plant button beside its Run button in Preview. Choose how often it should run. You can also open Gardener from the main toolbar to inspect and manage scheduled actions.

````markdown
```action
name: Check recent experiments

Use the available tools to inspect experiments from the last day.
Update this note with a short status summary.
```
````

When an action is added, the Robot reviews it and creates a cached script in the project's `code/` directory. The script handles the deterministic portion of the work and prints one machine-readable JSON result.

## Cached Runs and Robot Follow-up

Later runs use that cached script directly. Its result includes a `robot` field:

- `robot: false` means the cached script completed the action without invoking the Robot.
- `robot: true` means the Robot receives the script's structured result and finishes any remaining work.

Changing the action name or instructions invalidates the cached script. Gardener asks the Robot to refresh the script before it runs again.

The Robot chat shows live cached-script output, Robot follow-up progress when needed, and a dismissible completion result after each run.

## Manage the Schedule

Open Gardener with the plant icon in the main toolbar. For every scheduled action you can:

- change its interval in minutes;
- run it immediately;
- run all scheduled actions; or
- remove it from Gardener.

Gardener checks due schedules while Light Academia is open. It is not a background service after the application has been closed.

Return to [Getting Started](Home.md) or read about [Agent and Actions](Agent%20and%20Actions.md).
