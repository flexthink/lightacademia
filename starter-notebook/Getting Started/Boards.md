# Boards

Boards display fetched CSV data as interactive tables. This example uses fast fetch: the first refresh asks the Robot to create a reusable fetch script, while later refreshes run that script directly. Editing the fetch instructions or columns asks the Robot to update the script.

```board
name: MNIST Experiments
fetch: fast
filters:
- Name
- Architecture: dropdown
- Finished: dropdown
columns:
- Name
- Architecture
- Finished
- Accuracy

Generate a fake table of 12 illustrative MNIST experiments.

- Give every experiment a distinct, readable name.
- Use only MLP, CNN, or ViT for Architecture.
- Use only Yes or No for Finished.
- Give finished experiments varied, plausible accuracy rates.
- Leave Accuracy empty for unfinished experiments.
- These are demonstration results, not measurements from real experiments.
```

Use the Name field for text search. Architecture and Finished provide dropdowns populated from the generated CSV.

Return to [Getting Started](Home.md).
