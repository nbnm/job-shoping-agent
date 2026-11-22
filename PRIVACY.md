# Keeping a JOP repository public and its data private

JOP separates code from private runtime files. Start the public demo with a fresh data directory; never use a personal database to record public screenshots.

## Files and sources

- `data/`, `gmail/`, mail exports, SQLite and DuckDB databases, logs, backups, environment files, and `MEMORY.md` are ignored.
- Use a private directory outside Git for the original MBOX and mail indexes. Imported receipts contain sender and recipient addresses, order numbers, timestamps, and purchase details.
- App databases contain preferences and current-ownership markings as well as receipts. Uploaded images can reveal people or places.
- Demo inventory is fictional. Bundled product and mascot images are AI-generated.
- The browser may request public catalog images from receipt-derived, allowlisted hosts. Email images and arbitrary email HTML are not rendered.

## Before publication

Run `npm run check:privacy` for the current source and `python3 scripts/check_public.py --history` for reachable local Git history. The checks look for private file types, personal mailbox addresses, local absolute paths, private keys, and common access-token shapes. Findings print rule names and paths rather than matched private values.

This focused scanner can miss secrets and personal details. Review diffs, filenames, media, Git author/committer identities, branches, tags, GitHub attachments, and any already-published releases. Git ignores do not protect a file that was previously committed.

`npm run release:public` builds an allowlisted ZIP of source and documentation without `.git`, runtime data, or local profiles. It preserves local files and does not push, publish, rewrite commits, or change repository visibility. For a new public repository, unpack that ZIP and start a fresh history with the identity you intend to publish.

If an existing repository has private information in history, making a new commit that removes it is insufficient. Plan a coordinated history cleanup or publish the clean source to a new repository. Rotate any exposed credentials; rewriting history does not revoke them.

## Local operation

The app binds to loopback and has no authentication. It is not a hosted multi-user service. Host validation helps resist DNS rebinding, and cross-origin mutations are rejected. These controls do not make the server safe to publish on the internet.

Python processes mail locally. DuckDB automatic extension installation is disabled during imports. JOP never needs Gmail OAuth, an app password, payment credentials, or an AI-service key.

## Backup and reset

Stop the server before copying `jop.db`, or use SQLite's backup facility. Keep originals and backups on encrypted storage.

**Reset sample inventory** removes manual/sample inventory and restores the fictional demo. It does not erase the imported mail databases or purchase-history tables. Use a separate data directory to return to an isolated demo.
