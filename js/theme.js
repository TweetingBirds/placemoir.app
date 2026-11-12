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

    // A saved theme has to reach each dark <source> before its <img> picks one, or an eager
    // image (the hero) is chosen by the system appearance and swapped at DOMContentLoaded.
    // This script runs before the body is parsed, so watch the parser add sources and pin each
    // as it lands. The browser's preload scanner can still fetch the system's choice early;
    // what's shown is right from the first paint.
    var pinning = null;
    if (root.hasAttribute('data-theme') && window.MutationObserver) {
        pinning = new MutationObserver(function (records) {
            var dark = current() === 'dark';
            for (var r = 0; r < records.length; r++) {
                var added = records[r].addedNodes;
                for (var n = 0; n < added.length; n++) {
                    var node = added[n];
                    if (node.nodeName === 'SOURCE' && node.hasAttribute('data-theme-dark')) {
                        node.media = dark ? 'all' : 'not all';
                    }
                }
            }
        });
        pinning.observe(root, { childList: true, subtree: true });
    }

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
        if (pinning) pinning.disconnect();
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
