(() => {
    const menus = document.querySelectorAll("[data-habit-action-menu]");

    function closeMenu(menu) {
        if (!menu) return;
        const toggle = menu.querySelector("[data-habit-action-toggle]");
        const dropdown = menu.querySelector("[data-habit-action-dropdown]");
        if (!dropdown) return;
        toggle.setAttribute("aria-expanded", "false");
        dropdown.hidden = true;
    }

    menus.forEach((menu) => {
        const toggle = menu.querySelector("[data-habit-action-toggle]");
        const dropdown = menu.querySelector("[data-habit-action-dropdown]");

        toggle.addEventListener("click", (event) => {
            event.stopPropagation();
            const willOpen = toggle.getAttribute("aria-expanded") !== "true";

            menus.forEach((otherMenu) => {
                if (otherMenu !== menu) closeMenu(otherMenu);
            });

            if (!willOpen) {
                closeMenu(menu);
                return;
            }

            toggle.setAttribute("aria-expanded", String(willOpen));
            dropdown.hidden = false;

            if (willOpen) {
                const firstItem = dropdown.querySelector("button, a");
                if (firstItem) firstItem.focus();
            }
        });
    });

    document.addEventListener("click", (event) => {
        menus.forEach((menu) => {
            if (!menu.contains(event.target)) closeMenu(menu);
        });
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            menus.forEach(closeMenu);
        }
    });

})();
