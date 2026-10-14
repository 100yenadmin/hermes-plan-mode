# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately: [open a security advisory](https://github.com/100yenadmin/hermes-plan-mode/security/advisories/new).
Don't open a public issue for a security problem.

We aim to acknowledge reports within 3 business days and to share a decision or fix plan within 14 days.

## What counts as a vulnerability here

- A tool with side effects runs while Plan Mode should block it through the `pre_tool_call` hook (approval bypass).
- The plugin records or accepts a plan as approved without the user approving it.
- The plugin writes a plan file outside the fixed plans directory, including through path traversal or a symlink.

## Out of scope

- Problems in Hermes Agent core: report those to [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/security).
- Hermes core approval-system bugs belong upstream, even when first noticed while using this plugin.

## Supported versions

Fixes go into the latest release.
