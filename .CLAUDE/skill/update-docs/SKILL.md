---
name: update-docs
description: When the user wants to update all project related docs, he can use this skill.
---

When the user wants to update the various doc files of the project, he will invoke this skill.
The project's .claude folder will contain Doc-Index.MD (be case in-sensitive). It will index to all the doc files of
this project. Go through the list and make necessary changes to the docs based on the current code, understanding,
changes and updates.

If user specifies 'Full', also make sure that all docs in the project are indexed here. Look in the project repo for MD
files and add them here, but don't include git-ignored ones or claude skills.

If user specifies 'Recent', look at what changes / learnings / updates happened in the current session and update the
docs accordingly. This is the default action.

If user specifies 'init', create the Doc-Index.MD file and index the existing documentation MD files in it. The location
of the documentation will vary from project to project, so find it from the project specific pointers.

if user specifies 'help', display the help message, along with the available actions.

Note: CLAUDE-COMMON.md is a common file from a shared repo. It is not to be edited! If important changes required,
inform the user.