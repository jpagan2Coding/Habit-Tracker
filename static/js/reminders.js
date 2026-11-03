(function () {
    const checkbox = document.getElementById('reminder-enabled');
    const timezone = document.getElementById('reminder-timezone');
    const status = document.getElementById('notification-status');

    if (!checkbox || !timezone || !status) return;
    timezone.value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

    checkbox.addEventListener('change', async function () {
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
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') {
            status.textContent = 'Notification permission was not granted.';
            return;
        }
        try {
            const registration = await navigator.serviceWorker.register('/static/push-service-worker.js');
            const keyResponse = await fetch('/api/push-public-key');
            if (!keyResponse.ok) throw new Error('Push notifications are not configured.');
            const keyData = await keyResponse.json();
            const subscription = await registration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(keyData.public_key)
            });
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
    });

    function urlBase64ToUint8Array(value) {
        const padding = '='.repeat((4 - value.length % 4) % 4);
        const raw = atob((value + padding).replace(/-/g, '+').replace(/_/g, '/'));
        return Uint8Array.from([...raw].map((character) => character.charCodeAt(0)));
    }
})();
