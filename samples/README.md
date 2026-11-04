# Sample documents

Three fictional documents for trying Inkfold: an invoice, a business card and a lead form.
All people, companies, addresses and numbers are made up, and every domain ends in `.example`.

The sources are in `src/`. To render them again:

```bash
google-chrome --headless=new --no-pdf-header-footer --print-to-pdf=invoice.pdf src/invoice.html
google-chrome --headless=new --no-pdf-header-footer --print-to-pdf=lead-form.pdf src/lead-form.html
```

`business-card.png` is a 1.5x screenshot of `src/business-card.html` at 1000 × 620.

If you render them again, the demo account's prefilled copies in `Backend/app/demo_data.py` keep
the old file hashes, so uploading the new file counts as a different document.
