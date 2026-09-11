# Documentation

The project overview, requirements, and install steps are in the
[top-level README](../README.md). This folder holds the reference docs:

- [remote-access.md](remote-access.md): the remote HTTP and WebSocket server,
  its API endpoints, and its security model (token, bind, Tailscale).
- [transcript-protocol.md](transcript-protocol.md): the `TranscriptEvent`
  protocol the server and its clients speak over the WebSocket.
- [project-workspace-protocol.md](project-workspace-protocol.md): the project
  registry, the `/api/projects` routes, and the in-repo issue format.
- [packaging.md](packaging.md): building the rpm and deb, the Fedora COPR
  repository, and the version-number rules.

The issue tracker is under [../issues/](../issues/).
