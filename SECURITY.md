# Security Policy

## Supported Versions

The `main` branch is the only supported development line.

## Reporting a Vulnerability

Do not publish exploitable details, API keys, `.env` contents, local databases, or private translation data in a public issue.

Report vulnerabilities through GitHub private vulnerability reporting when available, or through a private channel agreed with the repository owner.

## Secrets

Never commit real Gemini API keys, proxy credentials, Chroma/SQLite project databases, translated private chapters, or generated local reports.

Use `.env.example` as the template for local configuration.
