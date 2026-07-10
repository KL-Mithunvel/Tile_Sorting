# documents/

Non-code project artifacts for the Tile Sorting system: CAD, drafted sheets, electrical
documentation, code-adjacent supporting docs, the system-level design charter, and the
formal requirements spec. Source code stays in the repo root (`acoustic/`, future
subsystem packages); this tree is for everything that documents the system rather than
implements it.

College-mandated coursework documents (synopsis, reports, review presentations, etc.)
live under `deliverables/` — kept distinct in naming/purpose from the engineering
subfolders below even though it's nested in the same `documents/` tree.

| Folder | Contents |
|---|---|
| `mech/` | CAD source files, drafted mechanical sheets (PDF), bills of materials |
| `electrical/` | Wiring/connection schematics, component datasheets, panel layout docs |
| `programming/` | Flowcharts, pseudocode, dataset/model notes, test reports — code-adjacent docs that aren't the system-level charter |
| `project/` | System-level design source of truth: `project_charter.md` and per-subsystem architecture docs |
| `requirements/` | Formal functional/non-functional requirements spec, traceable back to `documents/project/project_charter.md` |
| `deliverables/` | College-mandated coursework (VIT Chennai BMEE497J/BMHA497J) — see `deliverables/README.md` for the assessment schedule |

Each subfolder has its own `README.md` with naming conventions and more detail.

## Conventions

- Every file added here should have a clear, descriptive name — no `final_v2_new.pdf`.
  Prefer `<subsystem>_<doc-type>_<version>.<ext>`, e.g. `acoustic_station_wiring_v1.pdf`.
- When a document supersedes an older one, keep both and note the change (don't silently
  overwrite) unless the old one is a genuine draft with no historical value.
- Large native CAD files (SolidWorks, Fusion 360, etc.) are tracked directly in git for
  now. If the `mech/cad/` folder grows large enough to bloat clone times, revisit with
  Git LFS rather than continuing to commit raw binaries.
