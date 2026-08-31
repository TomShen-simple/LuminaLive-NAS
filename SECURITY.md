# Security Policy

## Reporting

Please do not open a public issue for a vulnerability that could expose a NAS, local network, signed media URL, or user-supplied playlist credential. Use GitHub's private security advisory feature for this repository.

## Deployment guidance

- Keep the service on a trusted LAN or behind a VPN.
- Do not publish port 18780 directly to the Internet.
- Private upstream addresses are blocked by default.
- Full signed upstream URLs are not returned by the status endpoint.
- Only mount the dedicated `/config` and `/data` directories.

