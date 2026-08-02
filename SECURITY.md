# Security Policy

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Use GitHub's private vulnerability reporting: go to the **Security** tab of this repository and
choose **Report a vulnerability**. The report is visible only to the maintainer, and you can
discuss and coordinate a fix there before anything becomes public.

If you cannot use that form for any reason, open a public issue titled `security contact
request` containing **no details of the problem** — just the request — and you will be contacted
privately to continue.

## What to include

- The commit or release you tested against.
- Any configuration relevant to the issue (with secrets and API keys removed).
- Steps to reproduce, and what an attacker gains if it works.

## What to expect

This is a **single-maintainer project**. Reports are taken seriously, but there is no staffed
response team and no service-level guarantee. Realistically: an acknowledgement within about a
week, and a fix timed to the severity. Disclosure is coordinated — the fix lands first, then the
advisory. You will be credited unless you ask not to be.

## Scope

**In scope** — anything in this repository, particularly:

- **Prompt-injection isolation** (`prompt_isolation.py`). The seeds pull untrusted content off
  the network and send it to a language model; anything that escapes the untrusted boundary,
  or lets network content act as instructions, is the highest-value finding here.
- Provider API key handling — any path where a key could reach a log, a prompt, or a response.
- The seed's HTTP client and its trust in backend responses.

**Out of scope:**

- **A specific operator's deployment** — their host, network, container runtime, and the keys
  they put in `.env`. This is self-hosted software and the operator owns their instance.
- Vulnerabilities in third-party dependencies or in the language-model provider itself. Report
  those upstream; do tell us if this project pins a known-vulnerable version.
- Model behaviour that is undesirable but not a boundary violation — a model giving a bad answer
  is a quality problem, not a security one.

## Known and accepted limitations

- **All seed keys live in one file on the seed host.** Anyone controlling that host can act as
  any seed. Accepted for a self-hosted model where the operator is already trusted.
- **No spend cap on inference.** Pointing the seeds at a paid provider has no dollar ceiling in
  this project. The default local-model path has nothing to spend.

## Supported versions

Only the latest state of the default branch is supported. No backports, no LTS branches.
