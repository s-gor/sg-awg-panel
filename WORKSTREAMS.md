# SG-AWG-Panel workstreams

The repository is split into independent streams.

## Stable

- Branch: `main`
- Mirror branch: `stable/alpha8`
- Contains only the last version verified on a real server.
- Current stable baseline: `v0.1.0-alpha8`.

## Integration

- Branch: `develop`
- Receives completed feature branches only after their full test suite passes.

## Server

- Branch: `feature/server-alpha9`
- Contains the complete Server section and its tests.
- It is not deployed to the working EC2 until Linux validation is complete.

## Clients

- Future branch: `feature/clients-alpha10`
- Client lifetime, comments, enable/disable, key recreation, QR, `.conf`, per-client DNS/MTU, traffic and search.

## Platform

- Future branch: `feature/platform`
- Authentication, sessions, logs, backups, updates, rollback and reboot recovery.

## Promotion order

`feature/*` -> `develop` -> real test server -> `main` -> release tag

No direct edits are made in `/opt/sg-awg-panel` on the working server.
