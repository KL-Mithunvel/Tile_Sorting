# documents/requirements/

`requirements.md` is the formal functional/non-functional requirements specification for
the tile sorting system. It is derived from and traceable to
`documents/project/project_charter.md` — the charter remains the source of truth for
design rationale and hardware options; this document extracts the actual requirements
into stable, numbered IDs (`FR-xx`, `NFR-xx`) so they can be referenced from tests, TODO
items, and college deliverables without re-deriving them from charter prose each time.

**Keep in sync:** any change to the charter's scope (§3, §4) or safety requirements
(§17) that affects what the system must do should update `requirements.md` in the same
commit — don't let them drift apart.
