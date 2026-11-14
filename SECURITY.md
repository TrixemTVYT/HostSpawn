# Security

Found a security problem in HostSpawn (for example a way to read someone's passwords, run commands, or reach
HostSpawn from outside the machine)? **Please don't post it publicly.**

- Report it privately on GitHub: **Security → Report a vulnerability** on this repository, or
- open a private ticket with **Get help** in the HostSpawn Discord and say it's a security problem.

We'll reply as soon as we can, fix it, and credit you in the changelog if you'd like.

How HostSpawn keeps you safe: it listens on 127.0.0.1 only; passwords and tokens live in private files (chmod 600)
and are hidden in every log and console view; sign-in uses scrypt password hashes and locks after wrong passwords;
risky actions ask first and back up first; every release has checksums, and Setup checks every file before
installing. It never changes your firewall or system settings itself.
