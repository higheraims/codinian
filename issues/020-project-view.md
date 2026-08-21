---
id: ISSUE-020
title: Project view
status: done
type: feature
area: tools
created: 2026-08-17
updated: 2026-08-20
related: [ISSUE-019]
---

## Summary

Build the ability to browse projects and add by folder, like VS Code.

## Notes & worklog

- Displays all files and sessions related to the project. 
- Exposes options
    - git-enable the folder
    - set and update git-ignore
    - create commits and tags
    - create and rename files
    - open file in (relevant program such as code editor)
    - interact with issues system (see ISSUE-019)

## Resolution

Built in M4. A project is a folder registered in
`~/.local/share/codinian/projects.json`; `project.html` renders it as four tabs
(Files, Issues, Git, Sessions) and the GTK sidebar grows a Projects section
whose rows open that same page in a WebKitGTK pane, exactly as a session row
opens the transcript.

Everything this issue listed is there: browse and add by folder, files and
sessions in one place, `git init`, a `.gitignore` editor, commits and tags,
create and rename files, and opening a file in an external program.

Boundaries worth stating, because they were chosen rather than missed:

- **Nothing in the git surface can lose work.** No reset, checkout, clean,
  amend, force-tag, push, pull or fetch. Adding one needs its own issue and its
  own confirmation flow.
- **Removing a project only deregisters it.** There is no delete-file or
  delete-folder operation anywhere in the API, and both the GTK dialog and the
  browser say so.
- **Open externally is refused for a remote client** (`403 not_local`). It runs
  `xdg-open` on the machine hosting the server, which is not something a phone
  on the tailnet should be able to ask for. The check is best-effort: `tailscale
  serve` proxies from loopback, so the same ambiguity documented for identity
  headers in ISSUE-022 applies here.
