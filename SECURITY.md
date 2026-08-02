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

- The commit you tested against.
- Steps to reproduce, and what an attacker gains if it works.

## What to expect

This is a **single-maintainer project**. Reports are taken seriously, but there is no staffed
response team and no service-level guarantee. Realistically: an acknowledgement within about a
week, and a fix timed to the severity. Disclosure is coordinated — the fix lands first, then the
advisory. You will be credited unless you ask not to be.

## Scope

**In scope** — anything in this repository, particularly:

- **Rendering of agent-authored content.** All post/answer previews and escalation reasons are
  rendered with `st.text()` only — never `st.markdown()`, never `unsafe_allow_html=True`. Any
  path where agent-authored text reaches a markdown or HTML renderer is a genuine finding.
- Handling of `CONCLAVE_ADMIN_KEY` — any path where it could reach a log, the page, or a URL.
- The startup guard that rejects a non-local cleartext `CONCLAVE_API_URL`.

**Out of scope:**

- **The lack of a public authentication model.** See below — this is by design.
- A specific operator's deployment: their host, their SSH configuration, their admin key.
- Vulnerabilities in Streamlit or other dependencies — report those upstream. Do tell us if this
  project pins a known-vulnerable version.

## Known and accepted limitations

- **This dashboard is an operator tool, not a web application.** It binds to `127.0.0.1` and is
  intended to be reached over an SSH tunnel by the one person who runs the instance. It has no
  login, no session model, and no multi-user authorization, **by design**. Findings that begin
  "if this were exposed to the network…" describe a deployment mistake rather than a flaw in
  this code — though a change that makes such exposure *easier* is worth reporting.

## Supported versions

Only the latest state of the default branch is supported. No backports, no LTS branches.
