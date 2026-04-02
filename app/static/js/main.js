// Crypto Alert System — main JS

document.addEventListener("DOMContentLoaded", () => {

    // ── Auto-dismiss flash messages after 5 s ──────────────
    document.querySelectorAll(".flash").forEach(el => {
        setTimeout(() => {
            el.style.transition = "opacity .4s";
            el.style.opacity = "0";
            setTimeout(() => el.remove(), 400);
        }, 5000);
    });

    // ── Modal: close on overlay click ─────────────────────
    document.querySelectorAll(".modal-overlay").forEach(overlay => {
        overlay.addEventListener("click", e => {
            if (e.target === overlay) overlay.style.display = "none";
        });
    });

    // ── Escape key closes any open modal ──────────────────
    document.addEventListener("keydown", e => {
        if (e.key === "Escape") {
            document.querySelectorAll(".modal-overlay").forEach(m => {
                m.style.display = "none";
            });
        }
    });

    // ── Checkbox: prevent double-submit on send_once ──────
    // The hidden input trick means the last value wins.
    // Chrome submits checkboxes before hidden inputs when checked,
    // so we flip the order client-side just to be safe.
    document.querySelectorAll("input[type=checkbox][name=send_once]").forEach(cb => {
        cb.closest("form")?.addEventListener("submit", () => {
            const hidden = cb.parentElement?.querySelector("input[type=hidden][name=send_once]");
            if (hidden) hidden.value = cb.checked ? "true" : "false";
        });
    });
});

