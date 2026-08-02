# Contributing

Thanks for your interest. A few things worth knowing before you spend time on a change.

## What this project is

Conclave is a self-hosted, single-maintainer project. It is published because the code is
useful and the build is worth showing, not because there is a team behind it. Issues and pull
requests are read, but response times are best-effort and there is no support commitment.

If you need a hosted, one-command, actively-staffed alternative, say so in an issue — pointing
you at a better fit is a perfectly good outcome.

## Developer Certificate of Origin (DCO)

**Every commit must be signed off.** This project uses the
[Developer Certificate of Origin](https://developercertificate.org/) — a short statement that
you wrote the contribution, or otherwise have the right to submit it under this project's
license.

Sign off by adding `-s` to your commit:

```bash
git commit -s -m "fix: correct the retry backoff"
```

That appends a line to your commit message:

```
Signed-off-by: Jane Developer <jane@example.com>
```

The name and email must be real and must match your git config. Pull requests containing
commits without a sign-off will be asked to amend before review.

**Why this matters here:** this repository ships security-sensitive code — a moderation gate,
prompt-injection isolation, and authorization logic. The DCO is the record that every line in
it came from someone entitled to contribute it.

## Pull requests

- **Every pull request is reviewed by a human, line by line, before merge.** No exceptions and
  no automated merges. A public repository is a supply-chain surface, and this is the control
  that keeps it honest. Expect this to be slow. It is slow on purpose.
- **Keep changes focused.** One concern per PR. A large PR mixing a refactor with a behaviour
  change will be sent back to be split, because the review above cannot be done properly on it.
- **New or changed dependencies need a reason in the PR description.** Adding one is a
  security decision, not a convenience decision.
- **Tests must pass, and behaviour changes need tests.** See the README for test setup.
- **Explain the failure your change prevents**, not just what it does. "Fixes X" is more
  reviewable than "refactors Y".

## Security

**Do not report security vulnerabilities through public issues or pull requests.** Use the
private disclosure process in [SECURITY.md](SECURITY.md) so a fix can be prepared before the
problem is public. If you are unsure whether something counts, treat it as if it does.

## Configuration and defaults

This project is deployed by people running it on their own hardware, and several defaults are
deliberately conservative because of that (post expiry off, corpus anonymization off, URL
policy restrictive). **Changing a default is a behaviour change for every existing operator** —
propose it in an issue before writing the code.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE), the same license that covers this project.
