# Boards

Boards display agent-fetched CSV data as interactive tables. Press **Refresh: MNIST Experiments** below to ask the Robot to generate the sample data.

```board
name: MNIST Experiments
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
