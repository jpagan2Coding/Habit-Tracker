# Accessibility: Text Size

Users can change the text size on the **Settings** page (`/settings`), which is linked in the top navigation bar.

## Options

| Option | Size |
|---|---|
| Small | 87.5% |
| Default | 100% |
| Large | 112.5% |
| Extra large | 125% |

## How it works

- The choice is saved in a browser cookie (`text_size`) for one year, so it's remembered between visits. It isn't tied to an account.
- Every page adds a `text-size-<option>` class to the `<html>` tag, and `static/css/global.css` sets the base font size for each one.
- Text in the app is sized in `rem`, so changing the base size scales text on every page, including the home and login pages.
- "Reset to default" clears the cookie and goes back to 100%.
- If the cookie has an unknown value, the default size is used.

## Notes

- Some spacing is set in pixels, so "Extra large" can make some areas tighter. New pages should be checked at that size.
- The browser's own zoom (Cmd/Ctrl +) still works alongside this setting.