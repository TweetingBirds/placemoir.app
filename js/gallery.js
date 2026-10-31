// Device tabs for the screenshot gallery. Without JavaScript every device's row shows,
// one after another; with it, the tab bar appears and only the chosen row is visible.
// Hidden rows' images are lazy, so they aren't fetched until their tab is opened.
(function () {
    var tablist = document.querySelector('.device-tabs');
    if (!tablist) return;

    var tabs = Array.prototype.slice.call(tablist.querySelectorAll('[role="tab"]'));
    var panels = tabs.map(function (tab) { return document.getElementById(tab.getAttribute('aria-controls')); });

    function select(index, focus) {
        tabs.forEach(function (tab, i) {
            var on = i === index;
            tab.setAttribute('aria-selected', String(on));
            tab.tabIndex = on ? 0 : -1;
            panels[i].hidden = !on;
            if (on && focus) tab.focus();
        });
        panels[index].scrollLeft = 0;
    }

    tabs.forEach(function (tab, i) {
        tab.addEventListener('click', function () { select(i, false); });
        tab.addEventListener('keydown', function (event) {
            var next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[event.key];
            if (next === undefined) return;
            event.preventDefault();
            select((next + tabs.length) % tabs.length, true);
        });
    });

    document.documentElement.classList.add('has-device-tabs');
    tablist.hidden = false;
    select(0, false);
})();
