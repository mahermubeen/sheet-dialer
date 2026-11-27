# Security

## Reporting

Found a vulnerability? Email mahermubeenahmad@gmail.com. Please don't open a public issue.

## Risks I considered

| Risk | How it's handled |
|---|---|
| Secrets and personal config | No API keys are used. `config.json` is gitignored; see `config.example.json`. The access password is never sent to the browser. |
| Lead personal data | Lead files (`*.xlsx`, `*.csv`) are gitignored. The repo ships only fictional sample data from `make_sample.py`, using reserved test number ranges. |
| Injection | No database. Spreadsheet cells are written by column name and never evaluated. |
| XSS | All lead data is HTML-escaped before rendering; scripts are rendered as plain text. |
| CSRF | The only write route (`POST /api/lead/<row>`) accepts only `application/json`, which a cross-site form can't send without a CORS preflight. |
| Input validation | Row numbers must point at an existing lead (404). Outcome, dates, duration and note length are validated (400). |
| Auth and access | With no password, only `localhost` is served and any other host gets 403. In tunnel mode: HTTP Basic auth, a password of 12+ characters, constant-time comparison. |
| Brute force | After 20 failed logins in 10 minutes, all logins get 429 until the window passes. |
| Data integrity | Saves are serialised with a lock and written atomically (temp file, then `os.replace`). |
| Calling rules | *Do not call* and *Wrong number* leads are never returned as dialable. Calls outside 8am–9pm lead-local time need explicit confirmation, and auto-dial skips them. |
| Third-party messages | Zoom Smart Embed `postMessage` events are accepted only from `https://applications.zoom.us`. |
| AI / LLM | No model is called by the app. |
| Dependencies | Pinned in `requirements.txt`. CI runs lint, tests and a gitleaks secret scan. |

## Known gaps

- Tunnel mode uses one shared password. There are no per-user accounts and no audit log.
- The login throttle is global (behind a tunnel every client is `127.0.0.1`), so repeated bad logins also lock out the real user for up to 10 minutes.
- DNC registry lookups (US National DNC, Canada DNCL, UK TPS) are not automated. The `DNC Checked` column is filled in by hand.
- Calling hours can't be checked for US/CA leads without a `Timezone` value, because those countries span several zones.
- Dependency vulnerability scanning (e.g. `pip-audit`) is not in CI yet.
