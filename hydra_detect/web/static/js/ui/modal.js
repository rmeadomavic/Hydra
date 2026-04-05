'use strict';

window.HydraModules = window.HydraModules || {};

window.HydraModules.createModalController = function createModalController() {
    let activeModal = null;

    function getModalDialog(modal) {
        return modal ? (modal.querySelector('[role="dialog"]') || modal) : null;
    }

    function getFocusableElements(container) {
        if (!container) return [];
        return Array.from(container.querySelectorAll(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )).filter(el => el.offsetParent !== null);
    }

    function openModal(modal, triggerElement) {
        if (!modal) return;
        if (activeModal && activeModal !== modal) closeModal(activeModal);

        modal.__triggerElement = triggerElement || document.activeElement;
        modal.classList.add('active');
        activeModal = modal;

        const dialog = getModalDialog(modal);
        const focusables = getFocusableElements(dialog);
        if (focusables.length > 0) focusables[0].focus();
        else if (dialog) dialog.focus();
    }

    function closeModal(modal) {
        if (!modal) return;
        modal.classList.remove('active');
        if (activeModal === modal) activeModal = null;

        const trigger = modal.__triggerElement;
        if (trigger && document.contains(trigger) && typeof trigger.focus === 'function') {
            trigger.focus();
        }
        modal.__triggerElement = null;
    }

    function closeActiveModal() {
        if (activeModal) return closeModal(activeModal);
        const modal = document.querySelector('.modal-overlay.active');
        if (modal) closeModal(modal);
    }

    function onKeyDown(e) {
        const modal = activeModal || document.querySelector('.modal-overlay.active');
        if (!modal) return;

        if (e.key === 'Escape') {
            e.preventDefault();
            closeModal(modal);
            return;
        }

        if (e.key !== 'Tab') return;
        const dialog = getModalDialog(modal);
        const focusables = getFocusableElements(dialog);
        if (focusables.length === 0) {
            e.preventDefault();
            if (dialog) dialog.focus();
            return;
        }

        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const current = document.activeElement;
        if (e.shiftKey && current === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && current === last) {
            e.preventDefault();
            first.focus();
        }
    }

    function initEscapeAndTrap() {
        document.addEventListener('keydown', onKeyDown);
    }

    return {
        openModal,
        closeModal,
        closeActiveModal,
        initEscapeAndTrap,
        getFocusableElements,
        _onKeyDown: onKeyDown,
    };
};
