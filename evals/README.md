# Accuracy test set

24 made-up documents with known answers, for measuring how well Inkfold reads documents:

- 8 invoices in US, UK, German, Pakistani and Australian styles, including European number formats,
  day-first dates, a bill to a department with no named person, a discount line and a phone photo.
- 6 business cards, including a tilted, slightly blurry photo, a name in capitals, two phone numbers
  and a number with no country code.
- 6 lead forms: typed, hand-filled and a web form printout.
- 4 emails and notes, including a forwarded email where the contact is the original sender.

Every person, company and number is fictional, and every email and website is under `example.com`.

`make_cases.py` builds `cases/` and `expected.json` (it needs Chrome). `../Backend/scripts/eval_accuracy.py`
reads every case with Gemini through Inkfold's own code, saves the replies in `runs/` and writes the
scores to `results.md`.

Where a document allows more than one right answer, `expected.json` lists them all. A website that
isn't printed may be left blank or taken from the email's domain, for example.
