# DBS PayLah! Merchant Finder

A searchable static index of merchants and stalls listed in the DBS PayLah! Saturdays PDF. Search by Singapore postal code, merchant name, or address.

## Publish with GitHub Pages

The repository includes `.github/workflows/deploy-pages.yml`. It builds `public/` and deploys it whenever `main` is pushed.

1. Create a GitHub repository and push this project to its `main` branch.
2. Open **Settings → Pages** in the GitHub repository.
3. Under **Build and deployment**, select **GitHub Actions**.
4. Push `main`, or run **Deploy GitHub Pages** from the Actions tab.

No `.env` file, access token, or repository secret is required. The workflow uses GitHub's built-in Pages permissions.

## Refresh the PDF index manually

Create a Python environment and install the parser dependency:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Download and parse the configured DBS PDF. This updates both `dbs-paylah-merchants.json` and `index_meta.json`:

```bash
python3 parse_merchants.py --refresh
python3 build_static.py
```

The downloaded file is hashed before extraction. If its SHA-256 matches the current index, the expensive PDF parsing step is skipped.

If DBS moves the PDF, supply the replacement HTTPS URL:

```bash
python3 parse_merchants.py --refresh --url "https://example.com/new-merchants.pdf"
python3 build_static.py
```

To index a PDF already on your computer, use `--local-pdf`. The source file is preserved; a cached copy is made only when its hash has changed:

```bash
python3 parse_merchants.py --local-pdf "/path/to/new-merchants.pdf"
python3 build_static.py
```

Review the record count and test representative searches:

```bash
python3 parse_merchants.py --postal 730888
python3 parse_merchants.py --name "food court"
python3 parse_merchants.py --address "woodlands"
```

Commit the refreshed data and push it. GitHub Actions will republish the page:

```bash
git add dbs-paylah-merchants.json index_meta.json
git commit -m "Refresh merchant index"
git push origin main
```

Every push to `main` triggers the **Deploy GitHub Pages** workflow. To redeploy without changing data, open the workflow in GitHub Actions and select **Run workflow** (`workflow_dispatch`).

Do not commit `dbs-paylah-merchants.pdf`; it is intentionally ignored and can be downloaded again.

## Local static site

```bash
python3 build_static.py
python3 -m http.server 8000 --directory public
```

Then open <http://localhost:8000>.
