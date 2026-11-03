(function () {
    const bell = document.getElementById('notification-bell');
    const dropdown = document.getElementById('notification-dropdown');
    const csrfToken = window.habitNotificationConfig && window.habitNotificationConfig.csrfToken;
    if (!bell || !dropdown || !csrfToken) return;

    bell.addEventListener('click', function () {
        const isHidden = dropdown.classList.toggle('hidden');
        bell.setAttribute('aria-expanded', String(!isHidden));
    });

    document.addEventListener('click', function (event) {
        if (!dropdown.contains(event.target) && !bell.contains(event.target)) {
            dropdown.classList.add('hidden');
            bell.setAttribute('aria-expanded', 'false');
        }
    });

    async function markRead(item) {
        const response = await fetch(
            `/api/notifications/${item.dataset.notificationId}/read`,
            { method: 'POST', headers: { 'X-CSRF-Token': csrfToken } }
        );
        if (!response.ok) throw new Error('Unable to update notification.');
        item.classList.remove('bg-indigo-50');
        const link = item.querySelector('[data-notification-link]');
        if (link) link.classList.remove('font-semibold');
        const button = item.querySelector('[data-mark-notification-read]');
        if (button) button.remove();
    }

    document.querySelectorAll('[data-mark-notification-read]').forEach(function (button) {
        button.addEventListener('click', async function () {
            try {
                await markRead(button.closest('[data-notification-id]'));
            } catch (error) {
                console.error(error);
            }
        });
    });

    document.querySelectorAll('[data-notification-link]').forEach(function (link) {
        link.addEventListener('click', async function (event) {
            const item = link.closest('[data-notification-id]');
            if (!item.querySelector('[data-mark-notification-read]')) return;
            event.preventDefault();
            try {
                await markRead(item);
            } catch (error) {
                console.error(error);
            }
            window.location.href = link.href;
        });
    });

    const markAll = document.getElementById('mark-all-notifications');
    if (markAll) {
        markAll.addEventListener('click', async function () {
            const response = await fetch('/api/notifications/read-all', {
                method: 'POST',
                headers: { 'X-CSRF-Token': csrfToken }
            });
            if (response.ok) window.location.reload();
        });
    }
})();
