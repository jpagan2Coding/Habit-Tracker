(function () {
    const bell = document.getElementById('notification-bell');
    const dropdown = document.getElementById('notification-dropdown');
    const countBadge = document.getElementById('notification-count');
    const csrfToken = window.habitNotificationConfig && window.habitNotificationConfig.csrfToken;
    if (!bell || !dropdown || !countBadge || !csrfToken) return;

    bell.addEventListener('click', function () {
        const isHidden = dropdown.classList.toggle('hidden');
        bell.setAttribute('aria-expanded', String(!isHidden));
    });

    document.addEventListener('click', function (event) {
        if (!dropdown.contains(event.target) && !bell.contains(event.target)) {
            dropdown.classList.add('hidden');
            bell.setAttribute('aria-expanded', 'false');
        }
        document.querySelectorAll('.reminder-schedule[open]').forEach(function (details) {
            if (!details.contains(event.target)) details.open = false;
        });
    });

    document.querySelectorAll('.reminder-schedule').forEach(function (details) {
        details.addEventListener('toggle', function () {
            if (!details.open) return;
            document.querySelectorAll('.reminder-schedule').forEach(function (other) {
                if (other !== details) other.open = false;
            });
        });
    });

    async function markRead(item) {
        if (!item || !item.querySelector('[data-mark-notification-read]')) return;
        const response = await fetch(
            `/api/notifications/${item.dataset.notificationId}/read`,
            { method: 'POST', headers: { 'X-CSRF-Token': csrfToken } }
        );
        if (!response.ok) throw new Error('Unable to update notification.');
        const result = await response.json().catch(() => ({}));
        item.classList.remove('bg-indigo-50');
        const content = item.querySelector('.block.text-left');
        if (content) content.classList.remove('font-semibold');
        const button = item.querySelector('[data-mark-notification-read]');
        if (button) button.remove();
        clearHabitReminder(item.dataset.habitId);
        setUnreadCount(Number.isInteger(result.unread_count) ? result.unread_count : 0);
    }

    function clearHabitReminder(habitId) {
        if (!habitId) return;
        document.querySelectorAll(
            `[data-habit-id="${habitId}"].reminder-highlight`
        ).forEach((habit) => {
            habit.classList.remove(
                'reminder-highlight',
                'border-amber-400',
                'bg-amber-50',
                'ring-2',
                'ring-amber-200'
            );
            habit.classList.add('border-slate-200', 'bg-white');
            const badge = habit.querySelector('.reminder-due-badge');
            if (badge) badge.remove();
        });
    }

    function setUnreadCount(unreadCount) {
        countBadge.textContent = unreadCount;
        countBadge.setAttribute('aria-label', `${unreadCount} unread notifications`);
        countBadge.classList.toggle('hidden', unreadCount === 0);
    }

    function updateUnreadCount(change) {
        const currentCount = Number.parseInt(countBadge.textContent, 10) || 0;
        const unreadCount = Math.max(0, currentCount + change);
        countBadge.textContent = unreadCount;
        countBadge.setAttribute('aria-label', `${unreadCount} unread notifications`);
        countBadge.classList.toggle('hidden', unreadCount === 0);
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


    const markAll = document.getElementById('mark-all-notifications');
    if (markAll) {
        markAll.addEventListener('click', async function () {
            try {
                const response = await fetch('/api/notifications/read-all', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': csrfToken }
                });
                if (!response.ok) throw new Error('Unable to update notifications.');
                document.querySelectorAll('[data-mark-notification-read]').forEach(function (button) {
                    const item = button.closest('[data-notification-id]');
                    item.classList.remove('bg-indigo-50');
                    const content = item.querySelector('.block.text-left');
                    if (content) content.classList.remove('font-semibold');
                    button.remove();
                    clearHabitReminder(item.dataset.habitId);
                });
                setUnreadCount(0);
                markAll.remove();
            } catch (error) {
                console.error(error);
            }
        });
    }
})();
