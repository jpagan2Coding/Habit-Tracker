(() => {
    const preferenceKey = "habitTracker.shortcutsEnabled";
    const feedbackKey = "habitTracker.shortcutFeedback";

    function readStored(key) {
        try {
            return sessionStorage.getItem(key);
        } catch {
            return null;
        }
    }

    function writeStored(key, value) {
        try {
            sessionStorage.setItem(key, value);
        } catch {
            // Shortcuts still work when browser storage is unavailable.
        }
    }

    const navigationFeedback =
        document.getElementById("keyboard-navigation-feedback");
    const savedFeedback = readStored(feedbackKey);

    if (savedFeedback && navigationFeedback) {
        navigationFeedback.textContent = savedFeedback;
        navigationFeedback.hidden = false;

        try {
            sessionStorage.removeItem(feedbackKey);
        } catch {
            // Storage may be unavailable in restricted browser contexts.
        }
    }

    const enabledControl =
        document.getElementById("keyboard-shortcuts-enabled");
    const status = document.getElementById("keyboard-shortcut-status");

    if (!enabledControl || !status) {
        return;
    }

    enabledControl.checked = readStored(preferenceKey) !== "false";

    let prefixTime = null;

    function resetSequence() {
        prefixTime = null;
    }

    function updatePreference() {
        resetSequence();
        writeStored(preferenceKey, String(enabledControl.checked));
        status.textContent = enabledControl.checked
            ? "Keyboard shortcuts enabled."
            : "Keyboard shortcuts disabled.";
    }

    enabledControl.addEventListener("change", updatePreference);

    status.textContent = enabledControl.checked
        ? "Keyboard shortcuts enabled."
        : "Keyboard shortcuts disabled.";

    function activateLink(id, message) {
        const link = document.getElementById(id);

        if (!link) {
            status.textContent = "This shortcut is currently unavailable.";
            return;
        }

        writeStored(feedbackKey, message);
        link.click();
    }

    const actions = {
        n: () => activateLink(
            "shortcut-new-habit",
            "Keyboard shortcut: opened Add New Habit."
        ),
        s: () => activateLink(
            "shortcut-settings",
            "Keyboard shortcut: opened Settings."
        ),
        b: () => {
            const bell = document.getElementById("notification-bell");

            if (!bell || bell.disabled) {
                status.textContent = "Notifications are currently unavailable.";
                return;
            }

            bell.click();

            status.textContent =
                bell.getAttribute("aria-expanded") === "true"
                    ? "Notifications opened."
                    : "Notifications closed.";
        }
    };

    document.addEventListener("keydown", event => {
        const target = event.target;
        const isEditing = target instanceof Element && (
            target.closest("input, textarea, select, [role='textbox']") ||
            target.isContentEditable
        );

        if (
            !enabledControl.checked ||
            event.defaultPrevented ||
            event.repeat ||
            event.isComposing ||
            event.ctrlKey ||
            event.metaKey ||
            event.altKey ||
            event.shiftKey ||
            isEditing
        ) {
            resetSequence();
            return;
        }

        const key = event.key.toLowerCase();
        const now = Date.now();

        if (prefixTime !== null && now - prefixTime <= 1000) {
            resetSequence();

            if (Object.hasOwn(actions, key)) {
                event.preventDefault();
                actions[key]();
                return;
            }
        } else {
            resetSequence();
        }

        if (key === "g") {
            prefixTime = now;
        }
    });

    document.addEventListener("focusin", resetSequence);
    window.addEventListener("blur", resetSequence);
})();