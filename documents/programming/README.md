# documents/programming/

Code-adjacent supporting documents — things that explain or plan the software without
being the system-level design source of truth (that's `documents/project/`) and without
being source code (that's the repo-root packages, e.g. `acoustic/`).

| Folder | Contents |
|---|---|
| `flowcharts/` | Process/algorithm flowcharts, state diagrams |
| `datasets/` | Dataset documentation — what was collected, labeling scheme, sample counts (not the raw data itself; per `.gitignore`, `data/` and `*.wav` stay untracked) |

Currently empty scaffolding as of this restructure (2026-07-10). Test reports and
pseudocode notes can also live directly under `documents/programming/` if they don't
warrant their own subfolder yet.

Distinction from `documents/project/`: `project/` docs describe *what the system is and
why* (charter, subsystem architecture); `programming/` docs describe *how a specific
algorithm or dataset works* at an implementation level.
