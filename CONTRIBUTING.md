# Contributing

Open an issue or pull request in this repository for documentation, resource references, or performance-format changes. Evaluation implementation changes belong in the linked BabyVision-v2 repository.

For a resource update, record its immutable revision, verification date, verification scope, visibility, and migration status. Preserve the previous source until a replacement has been checked. Keep dataset licensing separate from the repository license.

For performance artifacts, include provenance, expected task scope, coverage, and the unmodified native collector output. Explain exclusions and preserve missing values. Synthetic examples and scorer self-tests must be labelled separately from measured model results.

Run `python scripts/validate_repository.py` with the validation requirements installed. Review changed text and artifacts for credentials, personal information, local paths, and private infrastructure details before submitting. Large data, raw logs, and model credentials do not belong in Git.
