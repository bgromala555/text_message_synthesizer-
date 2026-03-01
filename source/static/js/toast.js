/* toast.js -- Toast notification system with typed variants and auto-dismiss */

function getOrCreateContainer() {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    return container;
}

export function showToast(msg, type = 'info') {
    const container = getOrCreateContainer();
    const el = document.createElement('div');
    el.className = 'toast toast-' + type;
    el.textContent = msg;
    el.setAttribute('role', 'alert');
    el.setAttribute('aria-live', 'assertive');
    container.appendChild(el);

    setTimeout(() => {
        el.classList.add('toast-dismiss');
        el.addEventListener('transitionend', () => el.remove(), { once: true });
        setTimeout(() => el.remove(), 500);
    }, 4000);
}
