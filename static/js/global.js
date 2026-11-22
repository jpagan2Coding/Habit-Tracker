function toggleMobileMenu() {
    const menu = document.getElementById('mobileMenu');

    if (menu) {
        menu.classList.toggle('hidden');
    }
}

document.querySelectorAll(".flash-message").forEach((message) => {
    const closeButton = message.querySelector(".flash-message-close");

    const dismiss = () => {
        message.classList.add("leaving");
        window.setTimeout(() => message.remove(), 180);
    };

    if (closeButton) {
        closeButton.addEventListener("click", dismiss);
    }

    window.setTimeout(dismiss, 5000);
});
