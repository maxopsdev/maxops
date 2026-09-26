# MaxOps documentation site (MkDocs)

Source for a MkDocs Material reference build of the MaxOps documentation.

**The published documentation lives at [maxops.dev/docs.html](https://maxops.dev/docs.html)**,
and that is where every link in this repository points. This directory is kept
as a plain-text reference for the same material — useful for reading offline or
in a terminal — and is not currently published anywhere.

To build it locally:

```bash
pip install -r requirements.txt
mkdocs serve
```

`.github/workflows/docs.yml` can publish it to GitHub Pages, but only runs on
demand (`workflow_dispatch`) or when this directory changes, and Pages must be
enabled on the repository first.
