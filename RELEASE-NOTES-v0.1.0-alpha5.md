# SG-AWG-Panel v0.1.0 Alpha 5

Alpha 5 focuses on readability and reliable long-term operation on a small VPS.

## Interface

- Larger fonts, controls, table rows and spacing.
- Dark gray background instead of near-black.
- Calmer muted teal accent without strong glow.
- Better contrast for secondary text and values.

## Reliability

- Diagnostics now checks systemd autostart, `awg0.conf`, IPv4 forwarding and NAT.
- Service uptime is displayed for the panel and AWG server.
- A downloadable diagnostic report redacts private keys, PSK and Access tokens.
- Automatic daily backups are enabled through a persistent systemd timer.
- The Python package is installed into the virtual environment, so `awgpanel` works from any directory.

## Security

- Login protection: five failed attempts within 15 minutes per IP.
- Optional HTTPS installer for Nginx and Let’s Encrypt.
- HTTPS mode binds the panel to `127.0.0.1:8080`, enables proxy headers and Secure cookies.

## Upgrade behavior

The updater does not run `apt`, does not reinstall AmneziaWG and does not restart the working AWG tunnel. The database, keys, clients, `web.env` and `awg0.conf` are preserved.

## Verification

- 28 automated tests passed.
- Python syntax passed.
- Bash syntax passed.
- Markdown links passed.
- Editable Python package installation passed.
- ZIP integrity and executable permissions passed.

Alpha 5 has not yet been validated on the real EC2. Alpha 4 networking remains unchanged.
