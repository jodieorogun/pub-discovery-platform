# Evaluation data

A holdout is the evaluation equivalent of a sealed exam paper: it is kept unseen while the parser
and ranker are being changed, then opened once to estimate how well those changes generalise. As
soon as its failures guide another code or data change, it is no longer held out and belongs in the
archive. Holdouts are test data only; the application never reads them when serving recommendations.

- `regression/`: active development suite; inspect failures and rerun freely.
- `smoke/`: small borough-specific pipeline checks, not generalisation evidence.
- `archive/development/`: superseded development suites retained for reproducibility.
- `archive/holdouts/`: consumed holdouts v1-v8 plus their checksum manifests.
- `current_holdout/`: sealed v9 generalisation suite. Its schema and checksum may be tested, but do
  not run it during the implementation cycle that created it. Once its results influence a change,
  move it to `archive/holdouts/` and create a new sealed suite.

Archived holdouts are not used by the application and must not be reported as untouched. Their
compact historical results are preserved in `data/audits/`.
