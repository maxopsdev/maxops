This directory contains the bundled pricing database artifact that ships with the repository.

Files:

- `maxops_pricing.db.xz`: compressed SQLite database
- `maxops_pricing.db.xz.sha256`: checksum for the compressed artifact
- `maxops_pricing.metadata.json`: row counts and region coverage summary

Users unpack the database explicitly with:

```bash
./.venv/bin/python3.14 staging_pricing/unpack_pricing_db.py
```
