(() => {
    const form = document.getElementById("bulk-delete-form");
    const selectAll = document.getElementById("select-all-habits");
    const deleteButton = document.getElementById("delete-selected-button");
    const selectionStatus = document.getElementById("habit-selection-status");

    if (!form || !selectAll || !deleteButton || !selectionStatus) {
        return;
    }

    const checkboxes = Array.from(
        document.querySelectorAll(".habit-selection-checkbox")
    );

    const selectedCount = () =>
        checkboxes.filter(checkbox => checkbox.checked).length;

    function updateSelection() {
        const count = selectedCount();
        const total = checkboxes.length;

        deleteButton.textContent = `Delete Selected (${count})`;
        deleteButton.disabled = count === 0;

        selectAll.disabled = total === 0;
        selectAll.checked = total > 0 && count === total;
        selectAll.indeterminate = count > 0 && count < total;

        selectionStatus.textContent =
            `${count} ${count === 1 ? "habit" : "habits"} selected.`;
    }

    selectAll.addEventListener("change", () => {
        checkboxes.forEach(checkbox => {
            checkbox.checked = selectAll.checked;
        });

        updateSelection();
    });

    checkboxes.forEach(checkbox => {
        checkbox.addEventListener("change", updateSelection);
    });

    form.addEventListener("submit", event => {
        const count = selectedCount();

        if (count === 0) {
            event.preventDefault();
            updateSelection();
            return;
        }

        const label = count === 1 ? "habit" : "habits";
        const confirmed = window.confirm(
            `Delete ${count} selected ${label}? This cannot be undone.`
        );

        if (!confirmed) {
            event.preventDefault();
        }
    });

    window.addEventListener("pageshow", updateSelection);
    updateSelection();
})();