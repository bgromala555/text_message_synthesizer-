/* mobile.js -- Hamburger menu & slide-out mobile navigation */

// ---------------------------------------------------------------------------
// DOM references (resolved at init time)
// ---------------------------------------------------------------------------

let hamburgerBtn = null;
let slidePanel = null;
let overlay = null;

// ---------------------------------------------------------------------------
// Toggle helpers
// ---------------------------------------------------------------------------

function openPanel() {
    if (!slidePanel) return;
    slidePanel.classList.add('open');
    overlay.classList.add('open');
    hamburgerBtn.setAttribute('aria-expanded', 'true');
}

function closePanel() {
    if (!slidePanel) return;
    slidePanel.classList.remove('open');
    overlay.classList.remove('open');
    hamburgerBtn.setAttribute('aria-expanded', 'false');
}

function togglePanel() {
    if (slidePanel && slidePanel.classList.contains('open')) {
        closePanel();
    } else {
        openPanel();
    }
}

// ---------------------------------------------------------------------------
// Public init
// ---------------------------------------------------------------------------

export function initMobileNav() {
    hamburgerBtn = document.getElementById('hamburger-btn');
    if (!hamburgerBtn) return;

    overlay = document.createElement('div');
    overlay.className = 'mobile-nav-overlay';
    document.body.appendChild(overlay);

    slidePanel = document.createElement('nav');
    slidePanel.className = 'mobile-slide-panel';
    slidePanel.setAttribute('aria-label', 'Mobile navigation');

    const tabBar = document.querySelector('.tab-bar');
    if (!tabBar) return;

    const buttons = tabBar.querySelectorAll('.tab-btn');
    for (const btn of buttons) {
        const clone = btn.cloneNode(true);
        clone.addEventListener('click', () => {
            btn.click();
            closePanel();
        });
        slidePanel.appendChild(clone);
    }

    document.body.appendChild(slidePanel);

    hamburgerBtn.addEventListener('click', togglePanel);

    overlay.addEventListener('click', closePanel);

    const observer = new MutationObserver(() => {
        const realBtns = tabBar.querySelectorAll('.tab-btn');
        const cloneBtns = slidePanel.querySelectorAll('.tab-btn');
        for (let i = 0; i < realBtns.length && i < cloneBtns.length; i++) {
            cloneBtns[i].classList.toggle('active', realBtns[i].classList.contains('active'));
        }
    });
    observer.observe(tabBar, { attributes: true, subtree: true, attributeFilter: ['class'] });
}
