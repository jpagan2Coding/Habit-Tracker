(function () {
    const checkbox = document.getElementById('reminder-enabled');
    const timezone = document.getElementById('reminder-timezone');
    const status = document.getElementById('notification-status');
    const frequency = document.getElementById('habit-frequency');
    const time = document.getElementById('reminder-time');
    const preview = document.getElementById('reminder-preview');
    const weeklyFields = document.getElementById('weekly-reminder-fields');
    const monthlyFields = document.getElementById('monthly-reminder-fields');

    if (!checkbox || !timezone || !status) return;
    if (!timezone.value) {
        timezone.value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    }

    function updateScheduleFields() {
        const selectedFrequency = frequency ? frequency.value : '';
        if (weeklyFields) weeklyFields.hidden = selectedFrequency !== 'weekly';
        if (monthlyFields) monthlyFields.hidden = selectedFrequency !== 'monthly';
        updatePreview();
    }

    function updatePreview() {
        if (!preview || !frequency || !time) return;
        const selectedFrequency = frequency.value;
        const formattedTime = time.value
            ? new Date(`1970-01-01T${time.value}:00`).toLocaleTimeString([], {
                hour: 'numeric', minute: '2-digit'
            })
            : 'the selected time';
        if (selectedFrequency === 'daily') {
            preview.textContent = `Remind me every day at ${formattedTime}.`;
        } else if (selectedFrequency === 'weekly') {
            const days = [...document.querySelectorAll('.reminder-weekday:checked')]
                .map((input) => input.value.charAt(0).toUpperCase() + input.value.slice(1));
            preview.textContent = days.length
                ? `Remind me on ${days.join(' and ')} at ${formattedTime}.`
                : 'Choose one or more weekdays for your reminder.';
        } else if (selectedFrequency === 'monthly') {
            const monthDay = document.getElementById('reminder-month-day');
            const label = monthDay && monthDay.value
                ? (monthDay.value === 'last' ? 'the last day of the month' : `the ${monthDay.value}${ordinal(monthDay.value)} of each month`)
                : 'the selected date';
            preview.textContent = `Remind me on ${label} at ${formattedTime}.`;
        } else {
            preview.textContent = '';
        }
    }

    function ordinal(value) {
        const number = Number(value);
        if (number % 100 >= 11 && number % 100 <= 13) return 'th';
        return ({ 1: 'st', 2: 'nd', 3: 'rd' })[number % 10] || 'th';
    }

    if (frequency) frequency.addEventListener('change', updateScheduleFields);
    if (time) time.addEventListener('input', updatePreview);
    document.querySelectorAll('.reminder-weekday').forEach((input) => {
        input.addEventListener('change', updatePreview);
    });
    const monthDay = document.getElementById('reminder-month-day');
    if (monthDay) monthDay.addEventListener('change', updatePreview);
    updateScheduleFields();

    async function enableReminders() {
        if (!checkbox.checked) {
            status.textContent = 'Reminders are disabled.';
            return;
        }
        if (!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)) {
            status.textContent = 'This browser does not support background reminders.';
            return;
        }
        if (Notification.permission === 'denied') {
            status.textContent = 'Notifications are blocked. Allow them in your browser settings.';
            return;
        }
        try {
            const permission = await Notification.requestPermission();
            if (permission !== 'granted') {
                status.textContent = 'Notification permission was not granted.';
                return;
            }
            const registration = await navigator.serviceWorker.register('/static/push-service-worker.js');
            const keyResponse = await fetch('/api/push-public-key');
            if (!keyResponse.ok) throw new Error('Push notifications are not configured.');
            const keyData = await keyResponse.json();
            let subscription = await registration.pushManager.getSubscription();
            if (!subscription) {
                subscription = await registration.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: urlBase64ToUint8Array(keyData.public_key)
                });
            }
            const response = await fetch('/api/push-subscriptions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ subscription: subscription.toJSON() })
            });
            if (!response.ok) throw new Error('Could not save notification settings.');
            status.textContent = 'Reminders enabled. Save the habit to finish.';
        } catch (error) {
            checkbox.checked = false;
            status.textContent = error.message || 'Reminder setup failed.';
        }
    }

    checkbox.addEventListener('change', enableReminders);
    if (checkbox.checked) enableReminders();

    function urlBase64ToUint8Array(value) {
        const padding = '='.repeat((4 - value.length % 4) % 4);
        const raw = atob((value + padding).replace(/-/g, '+').replace(/_/g, '/'));
        return Uint8Array.from([...raw].map((character) => character.charCodeAt(0)));
    }
})();
