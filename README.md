# Habit Tracker

[![CI](https://github.com/jpagan2Coding/Habit-Tracker/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jpagan2Coding/Habit-Tracker/actions/workflows/ci.yml)

A web application that helps users create, manage, and track daily habits and progress toward consistent routines.

**Course:** SSW555 — Agile Methods for Software Development  
**Institution:** Stevens Institute of Technology  
**Team:** Habit Builders  

## Team Members

| Name | GitHub |
| --- | --- |
| Miti Patel | [@MitiPatel205](https://github.com/MitiPatel205) |
| Mahima Jain | [@github-mahima](https://github.com/github-mahima) |
| Thys Vanderschoot | [@RegalTurtle](https://github.com/RegalTurtle) |
| Elizabeth Kirstein | [@ElizabethKirstein](https://github.com/ElizabethKirstein) |
| Ismael Fuentes | [@ToniFlamboni](https://github.com/ToniFlamboni) |
| Jaycen Pagan | [@jpagan2Coding](https://github.com/jpagan2Coding) |

## Running the App

Requires Python 3.9+ and [uv](https://github.com/astral-sh/uv).

```bash
uv sync
uv run python app.py
```

Then open [http://localhost:5000](http://localhost:5000).

## Push Reminder Setup

Reminders use Web Push and require HTTPS (except localhost), a browser that
supports Push API notifications, and a minute-based worker or cron job. Keep
these values in the server environment; never expose the private key:

```text
VAPID_PUBLIC_KEY=<public key>
VAPID_PRIVATE_KEY=<private key or path to a PEM file>
VAPID_SUBJECT=mailto:admin@example.com
```

Run the reminder worker once per minute:

```bash
uv run python reminders.py
```

The web app does not run this worker automatically. If it is not running,
reminders will not be added to the bell or sent as push notifications. On
Windows, configure Task Scheduler to run the command every minute from the
repository directory while the app is deployed.

The worker sends reminders only on each habit's scheduled days, uses the
saved browser time zone, and stores delivery keys in MongoDB to prevent
duplicates. Every due reminder is also stored in the dashboard Notification
Center, even when browser push permission is denied or unsupported.

## Running Tests

```bash
uv run pytest -v
```

## Development Workflow

- Create a feature branch for each user story.
- Add or update automated tests for feature changes.
- Run `uv run pytest -v` before pushing changes.
- Open a Pull Request to `main`.
- Wait for review and successful CI checks before merging.
