# Repository guidance

## Purpose

This repository parses the DBS PayLah! Saturdays merchant PDF and serves a searchable merchant finder. Preserve both operating modes:

- `server.py` serves the Python API-backed application from `index.html`.
- The production static site is generated in `public/` and deployed through the Vite/Sites wrapper.

## Source-of-truth rules

- Treat root `index.html` as the source for the front end.
- Treat root `dbs-paylah-merchants.json` as the generated merchant dataset.
- Run `python3 build_static.py` after changing either source so `public/index.html` and `public/dbs-paylah-merchants.json` stay synchronized.
- Do not hand-edit the copies under `public/` unless diagnosing the static build.
- Keep `.openai/hosting.json` and its opaque `project_id` unchanged unless explicitly migrating the Sites project.
- Never commit credentials, repository tokens, or runtime secrets.

## Validation

Run the checks relevant to your change:

```bash
python3 -m py_compile parse_merchants.py server.py build_static.py
python3 build_static.py
npm run build
```

For parser changes, also exercise representative postal-code, merchant-name, and address searches against the cached JSON.

## Editing expectations

- Preserve English/Chinese localization and light/dark themes.
- Keep the static site usable without the Python backend; re-indexing is intentionally unavailable there.
- Avoid refreshing the upstream PDF unless the task explicitly requires current source data, because doing so changes generated artifacts.
