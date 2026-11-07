// Loaded in <head> without defer so a saved theme applies before first paint.
// With nothing saved, the CSS and each <picture>'s media query follow the system appearance.
(function () {
    var KEY = 'placemoir-theme';
    var root = document.documentElement;
    var system = window.matchMedia('(prefers-color-scheme: dark)');

    try {
        var saved = localStorage.getItem(KEY);
        if (saved === 'light' || saved === 'dark') root.setAttribute('data-theme', saved);
    } catch (e) { /* storage blocked: follow the system */ }

    function current() {
        return root.getAttribute('data-theme') || (system.matches ? 'dark' : 'light');
    }

    // Screens are <picture>s whose dark <source> carries data-theme-dark. Once the visitor
    // picks a theme, pin that source on or off so only the matching image is fetched.
    function syncPictures() {
        if (!root.hasAttribute('data-theme')) return;
        var dark = current() === 'dark';
        var sources = document.querySelectorAll('source[data-theme-dark]');
        for (var i = 0; i < sources.length; i++) sources[i].media = dark ? 'all' : 'not all';
    }

    function apply(theme) {
        root.setAttribute('data-theme', theme);
        try { localStorage.setItem(KEY, theme); } catch (e) { /* not remembered, still applied */ }
        syncPictures();
        label();
    }

    function label() {
        var button = document.getElementById('themeToggle');
        // Translated pages carry their own labels in data-label-light and data-label-dark.
        if (button) {
            var next = current() === 'dark' ? 'light' : 'dark';
            button.setAttribute('aria-label', button.getAttribute('data-label-' + next) || 'Switch to ' + next + ' mode');
        }
        var links = document.querySelectorAll('[data-set-theme]');
        for (var i = 0; i < links.length; i++) {
            links[i].setAttribute('aria-pressed', String(links[i].getAttribute('data-set-theme') === current()));
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        syncPictures();
        label();

        var button = document.getElementById('themeToggle');
        if (button) button.addEventListener('click', function () {
            apply(current() === 'dark' ? 'light' : 'dark');
        });

        var links = document.querySelectorAll('[data-set-theme]');
        for (var i = 0; i < links.length; i++) {
            links[i].addEventListener('click', function () { apply(this.getAttribute('data-set-theme')); });
        }

        if (system.addEventListener) system.addEventListener('change', label);
    });
})();
