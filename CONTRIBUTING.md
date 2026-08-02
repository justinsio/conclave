# Contributing

Thanks for your interest. A few things worth knowing before you spend time on a change.

## What this project is

The Conclave seed agent runtime — a self-hosted, single-maintainer project. It is published
because the code is useful and the build is worth showing, not because there is a team behind
it. Issues and pull requests are read, but response times are best-effort and there is no
support commitment.

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

**Why this matters here:** the seeds hold provider API keys and send untrusted network content
to a language model. The DCO is the record that every line in this repository came from someone
entitled to contribute it.

## Pull requests

- **Every pull request is reviewed by a human, line by line, before merge.** No exceptions and
  no automated merges. A public repository is a supply-chain surface, and this is the control
  that keeps it honest. Expect this to be slow. It is slow on purpose.
- **Keep changes focused.** One concern per PR.
- **New or changed dependencies need a reason in the PR description.** This runtime is
  deliberately lean — adding a dependency is a security decision, not a convenience decision.
- **Tests must pass, and behaviour changes need tests.** See the README for test setup.
- **Prompt-isolation changes get extra scrutiny.** Anything touching how untrusted content is
  wrapped before it reaches a model must explain the threat it addresses and must not widen the
  trusted boundary.

## Security

**Do not report security vulnerabilities through public issues or pull requests.** Use the
private disclosure process in [SECURITY.md](SECURITY.md) so a fix can be prepared before the
problem is public. If you are unsure whether something counts, treat it as if it does.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE), the same license that covers this project.
