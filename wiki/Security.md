# Security

Treat every homework photo, PDF and web page as untrusted input: it can contain instructions aimed
at the agent.

## Prompt injection

An injection becomes dangerous when one agent has all three of: private data, untrusted content,
and a way to send data out (web fetch counts, since a URL can carry data). No role has all three.
`tests/test_roles.py` enforces that no role has both private-data tools and outbound tools.

## What each role can touch

| Role | Private data | Untrusted content | Way out | Tools |
|------|--------------|-------------------|---------|-------|
| research | none | web pages | search, fetch | `web_search`, `web_fetch`, `write_artifact` |
| writer | style profile | research notes | none | `read_artifact`, `get_profile`, `write_artifact` |
| solver | task only | homework photo | none | `run_code`, `write_artifact` |
| review | task only | the draft | none | `read_artifact` |
| orchestrator | profile, history | artifact ids and statuses | none | `run_agent`, `get_profile`, `get_subject_memory`, `list_history`, `search_history` |
| curator | profile, memory | feedback text | none | `get_profile`, `get_subject_memory`, `update_memory` |

The source of truth is `apps/agent/src/agent/roles.py`. The API keeps a copy in `packages/shared`
to check calls without importing the agent; a test keeps the two equal.

- **Fixed tools per role.** A tool missing from the list doesn't exist for that agent.
- **Strict research brief.** `{topic, questions[], level, language, length}`, with length caps. No
  free-text field where personal details could leak into search queries.
- **Research results stay out of the orchestrator.** It sees artifact ids and statuses, not the
  summaries written after reading web pages.
- **Notes are untrusted.** The writer's prompt wraps research notes as source material, not
  instructions. The reviewer flags content that doesn't belong (links, odd instructions).
- **Code runs in its own actor**: no network, no token, no user data. The job is the trust boundary:
  one run can affect later runs of the same job, never another job.

Worst case for a successful injection: a bad answer that the reviewer and the student both see. It
can't leak this student's data or anyone else's.

## Tokens and secrets

- Context API tokens are HMAC-signed (`CONTEXT_TOKEN_SECRET`), one per role per round, and stay in
  the harness; they never appear in a prompt. Every query is filtered by the token's job and user.
- The egress gateway adds the OpenRouter and search keys outside the sandbox.
- CI authenticates to the cloud with workload identity federation, no JSON key files. Gitleaks runs on every PR.
- Firebase Auth Emulator mode skips signature checks, so it only starts with a `demo-*` project ID.

## User data

- Users are mostly minors. Host in the EU; zero data retention on the whole OpenRouter account.
- Every row and storage path carries `user_id`, so deleting a user is one query plus one folder.
- A "16+ or parental consent" checkbox and `consent_at` for now. Full GDPR/COPPA compliance before
  any paying users.
