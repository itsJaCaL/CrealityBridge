# Security Policy

## Supported Versions

CrealityBridge has no numbered releases. Only the latest code on `main` gets security fixes.
To update: `cd ~/CrealityBridge && git pull`, then `sudo systemctl restart creality_bridge.service`.

| Version         | Supported          |
| --------------- | ------------------ |
| latest `main`   | :white_check_mark: |
| older checkouts | :x:                |

## Reporting a Vulnerability

Please **don't open a public issue** for security problems.
Use GitHub's private reporting instead: the **Security** tab → **Report a vulnerability**.

Examples of what to report:
- Creality tokens or keys leaking (logs, files with loose permissions, network traffic)
- Ways for someone else to control your printer or start or stop a print
- The video relay's control port (8555) being reachable without its secret

What to expect:
- A reply within 7 days.
- If it's confirmed, a fix on `main` as soon as I can, with credit to you in the commit unless you'd rather stay anonymous.
- If it isn't a security issue, I'll explain why, and we can move it to a normal issue.

Never include your own tokens, `config.json` or passwords in a report.
