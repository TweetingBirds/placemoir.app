// Loaded in <head> without defer so a saved theme applies before first paint.
// With nothing saved, the CSS follows the system appearance.
(function () {
    var KEY = 'placemoir-theme';
    var root = document.documentElement;

    try {
        var saved = localStorage.getItem(KEY);
        if (saved === 'light' || saved === 'dark') root.setAttribute('data-theme', saved);
    } catch (e) { /* storage blocked: follow the system */ }

    function current() {
        var set = root.getAttribute('data-theme');
        if (set) return set;
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }

    document.addEventListener('DOMContentLoaded', function () {
        var button = document.getElementById('themeToggle');
        if (!button) return;

        function label() {
            button.setAttribute('aria-label', current() === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
        }
        label();

        button.addEventListener('click', function () {
            var next = current() === 'dark' ? 'light' : 'dark';
            root.setAttribute('data-theme', next);
            try { localStorage.setItem(KEY, next); } catch (e) { /* not remembered, still applied */ }
            label();
        });
    });
})();
