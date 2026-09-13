# Developer workstation workflow

After source changes, run `python scripts/dev_build.py build` and confirm success.
The local watcher also builds after stable source changes. Do not terminate a
running user app to replace it. Desktop launcher uses `.dev-build/current.json`
to open the last tested build. Never commit `.dev-build` or user account data.
Public GitHub releases remain a separate workflow.
