# Habit Tracker

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