/* Loaded in <head> so a saved theme is applied before the first paint. */
"use strict";
{
    const meta = document.querySelector('meta[name="color-scheme"]');
    const system = matchMedia("(prefers-color-scheme: dark)");
    let theme = null;
    try {
        const saved = localStorage.getItem("demangle-theme");
        if (saved === "light" || saved === "dark") theme = saved;
    } catch { /* Theme persistence is optional when browser storage is blocked. */ }
    const dark = () => theme === "dark" || (!theme && system.matches);

    function apply() {
        meta.content = theme ?? "light dark";
        const button = document.getElementById("btn-theme-toggle");
        if (button) button.textContent = dark() ? "☀️ Light" : "🌙 Dark";
    }

    apply();
    system.addEventListener("change", apply);
    document.addEventListener("DOMContentLoaded", () => {
        apply();
        document.getElementById("btn-theme-toggle").addEventListener("click", () => {
            theme = dark() ? "light" : "dark";
            apply();
            try { localStorage.setItem("demangle-theme", theme); }
            catch { /* Switching still works without persistence. */ }
        });
    });
}
