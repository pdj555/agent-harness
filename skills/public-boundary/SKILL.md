---
name: public-boundary
description: Use before adding docs, comments, configs, fixtures, or prompts to this public repository.
---

This repository is public. Keep it general-purpose.

Don't add secrets, credentials, private endpoints, personal filesystem paths, customer data, recorded run output, or anything that only makes sense for one private setup. If a change needs one of those to work, write a general version or leave it out.

When reviewing a diff, treat any of these as a merge blocker. `tests/test_boundary.py` and `scripts/scan_secrets.py` catch some cases automatically.
