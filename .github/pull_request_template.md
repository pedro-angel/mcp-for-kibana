## Summary

Describe what this PR changes and why.

## Checklist

- [ ] I added or updated tests (unit, and contract/e2e when a live path changed)
- [ ] `make check` passes locally (hooks + lint + audit + sast + unit + docs)
- [ ] `make dod` passes locally, or I noted which live tiers I could not run
- [ ] Commits are Conventional Commits and carry the provenance trailer
- [ ] I updated docs (`docs/`) and `docs/tools.md` if the tool surface changed
- [ ] I added a `CHANGELOG.md` entry under `[Unreleased]` if user-visible

## Validation

List the commands you ran and their key outcomes.

```bash
# Example
make check
make dod          # or: make test-contract / make streams-ephemeral / ...
```

## Related Issues

Closes #
