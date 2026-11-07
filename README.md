# Since for Home Assistant

A private "time since" and reminder tracker, shown as a dashboard card.

- Track named events and log each time they happen, with an optional date and note.
- See how long it has been since each one.
- Give an event an interval, either **recommended** (colours only) or **reminder** (also sends a phone notification when it's due, with a "Log it now" button).
- Events turn orange when due soon (the last 15% of the interval) and red when overdue.
- Edit or delete any history entry.

## Privacy

- Events are **not** entities. They live only in `/config/.storage/since.data`, with owner-only file permissions.
- The card talks to the integration over Home Assistant's websocket API. Every request checks the logged-in user, and only the users you pick in the integration's settings get any data. Everyone else, admins included, gets "no access".
- Limits: anyone who can open the config folder (for example with the File editor, Studio Code Server or SSH add-ons, or a full backup) can still read the file. Home Assistant backups include it, so keep your backups encrypted.
- The "Log it now" button on a reminder only works from a phone signed in as an allowed user.
- This repository holds only code. Your events never leave your Home Assistant server.

## Install with HACS

1. If you don't have HACS yet, install it by following [hacs.xyz](https://hacs.xyz/docs/use/download/download/).
2. Open **HACS**, tap the **⋮** menu in the top right, then **Custom repositories**.
3. Enter `https://github.com/Sk3yNet/since-track-ha`, choose type **Integration**, and tap **Add**.
4. Search HACS for **Since**, open it, and tap **Download**.
5. Go to **Settings › System › Restart**.
6. Go to **Settings › Devices & services › Add integration** and search for **Since**. Pick your user. For reminders, also pick your phone's `notify.mobile_app_…` service.
7. On a dashboard, tap **Edit › Add card** and search for **Since**. You can also add a Manual card with `type: custom:since-card`.

Updates show up in HACS. Download them, then restart. Your data is stored separately and isn't touched.

## Card options

All options are optional:

```yaml
type: custom:since-card
title: Since
due_soon_color: "#ff9500"
overdue_color: "#ff3b30"
```

To change who has access, or the reminder settings: **Settings › Devices & services › Since › Configure**.

## Development

The tests run against a real Home Assistant core:

```sh
pip install pytest-homeassistant-custom-component home-assistant-frontend
pytest
```

When releasing, bump the version in `custom_components/since/manifest.json`, `const.py`, and `frontend/since-card.js`.
