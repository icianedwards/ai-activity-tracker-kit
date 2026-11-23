---
name: activity-review
description: Open the local ICE Context Kit activity review and upload page. Use when the user wants to review, edit, approve, upload, exclude, or delete tracked activities.
---

# Activity Review

Start the ICE Context Kit review server from this plugin installation:

```bash
python3 <kit>/scripts/ts.py review-ui
```

`<kit>` is the plugin root two levels above this skill directory. Run the command in a persistent terminal session because the local page works only while that process remains alive. The command opens a fresh token-protected browser page automatically.

Tell the user the review page is open and that ending the terminal process stops the local server. Do not approve, upload, exclude, mark private, or delete activities on the user's behalf.
