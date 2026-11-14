# HostSpawn app (Flutter)

The native HostSpawn program: draws its own screens (no browser, no WebKit) and talks to the HostSpawn agent
(the Python engine in `backend/manager/`) through `/api/v1`.

Status: **phase 2 in progress.** It builds and runs on Linux, and its tests pass.
- Done: sign-in, first-run setup, pairing codes, the dashboard, the server panel (actions and logs), devices,
  **creating servers** (the wizard and the live setup screen with guided steps).
- Not yet: server settings, schedules, wipes and backups tabs; Settings, Dependencies and Events; Windows and
  macOS builds.

## Build (Linux)
One-time setup:
```bash
sudo apt-get install -y clang cmake ninja-build pkg-config libgtk-3-dev liblzma-dev libsecret-1-dev libjsoncpp-dev
git clone --depth 1 -b stable https://github.com/flutter/flutter.git ~/development/flutter
echo 'export PATH="$HOME/development/flutter/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
flutter doctor
```
Then, in this folder:
```bash
flutter create --platforms=linux,windows,macos --project-name hostspawn .   # adds the platform folders once
flutter pub get
flutter test
flutter run -d linux       # or: flutter build linux --release
```

## Try it against a sandbox (fake servers, nothing real touched)
```bash
python3 -I ../../backend/tests/app_sandbox.py /tmp/hostspawn-sandbox     # agent on port 8199; Ctrl-C stops it
HOSTSPAWN_AGENT=http://127.0.0.1:8199 build/linux/x64/release/bundle/hostspawn
flutter test integration_test/app_flow_test.dart -d linux       # first run to sign-out (needs a fresh sandbox)
flutter test integration_test/create_server_test.dart -d linux  # creates a fake Rust server through the wizard
```
Run the two files one at a time: Flutter sometimes can't start the app a second time when both run together.
Servers created in the sandbox use a fake SteamCMD (`backend/tests/fakes/fake_steamcmd.sh`), so nothing is downloaded.

## Files
| File | What |
|---|---|
| `lib/api.dart` | The agent's API: info, setup/login/pair (device token), overview, server, logs, actions, devices |
| `lib/store.dart` | The agent address, and the sign-in key kept in the system keychain (never a plain file) |
| `lib/theme.dart` | Colours and look (same as the built-in page), entrance animation, logo |
| `lib/main.dart` | Start-up: find the agent → setup / sign in / dashboard |
| `lib/screens/` | `sign_in`, `home`, `server`, `devices` |
| `test/` | The API client against a fake agent; the sign-in screens |
| `integration_test/app_flow_test.dart` | The real app against the sandbox agent, start to sign-out |

## Rules
- The app never stores a password. It sends it once to sign in and keeps only the device key, in the keychain.
- The agent decides what each role may do. The app only hides buttons a role can't use.
- Use the agent's address on this machine (`http://127.0.0.1:<port>`), or a Tailscale address for another
  machine (Tailscale encrypts the traffic). Don't expose the agent to the internet.
