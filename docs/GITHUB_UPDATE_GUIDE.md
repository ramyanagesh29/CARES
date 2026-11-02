# Reviewing and committing the corrected project (nothing is pushed for you)

The ZIP you uploaded had no `.git` folder, so no Git history was read or changed. Your
repository (`https://github.com/ramyanagesh29/CARES.git`, last commit `e795906`) is untouched.

## 1. Keep your original safe
```bash
cd path/to/your/local/CARES-main      # your existing repo (has .git)
git status                            # make sure you know what is uncommitted
git switch -c review-audit-fixes      # work on a new branch, not main
```

## 2. Copy in the corrected files
Unzip the corrected ZIP to a **separate folder**, then copy only the files listed in
`CHANGES.md` into your repo (keep your `.git` folder, your `.env` and your `data/` folder).
Do not copy `data/*.sqlite3*`.

## 3. Review exactly what changed
```bash
git status
git diff                              # read every change
git diff --stat
```

## 4. Check no secrets or databases will be committed
```bash
git ls-files | grep -E "(^|/)\.env$|sqlite3|\.wal|\.shm"     # should print nothing
git diff | grep -iE "api[_-]?key|token|password" | grep -v "your_\|replace_with\|test-key\|device-token-for-tests"
```
Also search old history once: `git log -p -S"TEXTBEE_API_KEY" --all | grep -E "^\+TEXTBEE_API_KEY=.{20,}"`.
If a real key was ever committed, treat it as exposed: create a new key in TextBee and revoke the old one.

## 5. Run the tests, then commit
```bash
python -m pytest -v
git add .env.example SMS_SETUP.md README.md CHANGES.md docs backend guardian frontend tests textbee_test_sms.py
git status                            # confirm only intended files are staged
git commit -m "Fix guardian notification metadata; add opt-in real-SMS demo, GPS freshness checks and GPS-only ingest"
```

## 6. Push only when you are ready (not done for you)
```bash
git push -u origin review-audit-fixes   # pushes the NEW branch only; never use --force
```
Then open a Pull Request on GitHub, review it with your team, and merge when satisfied.
