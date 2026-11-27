# CLAUDE.md

Single-user Flask power dialer: leads.xlsx → one lead at a time → Zoom Phone → outcome written back to the sheet.

## Layout
- `app.py`: routes, workbook read/write, validation, access control
- `templates/dialer.html`: page shell; `static/dialer.js` and `static/dialer.css`: the UI (no framework, no build step)
- `make_sample.py`: creates a workbook with 3 fictional leads and generic scripts
- `tests/`: pytest; fixtures build a fresh sample workbook in `tmp_path`

## Run and test
```bash
pip install -r requirements-dev.txt
cp config.example.json config.json && python make_sample.py && python app.py   # http://localhost:5050
ruff check . && pytest
```

## Never
- Read, print, commit or paste rows from a real `leads*.xlsx` or `config.json`: they hold other people's personal data. Use `make_sample.py` output.
- Weaken these guards: server-side DNC blanking of `_dial`, the localhost-only rule when no password is set, the JSON-only write route, `esc()` on rendered lead data, the save lock and atomic replace.
- Use real phone numbers in tests or samples. Use 555-01xx (US/CA) or 07700 900xxx (UK).

## Conventions
- Spreadsheet column names are the schema. Outcome names live in `OUTCOMES` (app.py) and must match `OUTCOMES` in `static/dialer.js`.
- Errors return `{ok: false, error}` with 400, 404, 409, 415 or 429.
- Keep Python 3.9 compatible (no `match`, no `X | Y` type hints).
- Small, conventional commits.
