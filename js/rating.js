// The Detailed rating example: five stars in half steps that the visitor can tap, drag or
// change with the arrow keys. It's a demo, so nothing is saved or sent. The Simple choices
// and the "Would you go back?" pills are plain radios and need no script.
// Without JavaScript the stars stay a static picture of four and a half.
(function () {
    var stars = document.getElementById('demoStars');
    if (!stars) return;

    var cells = Array.prototype.slice.call(stars.querySelectorAll('.star'));
    var readout = document.getElementById('demoStarsValue');
    var MIN = 0.5, MAX = 5, STEP = 0.5;
    var WORDS = ['', 'One', 'Two', 'Three', 'Four', 'Five'];
    var value = parseFloat(stars.getAttribute('data-value')) || 4.5;

    function describe(v) {
        var whole = Math.floor(v);
        var half = v % 1 !== 0;
        if (whole === 0) return 'Half a star';
        var text = WORDS[whole] + (half ? ' and a half' : '');
        return text + (whole === 1 && !half ? ' star' : ' stars');
    }

    function render(v) {
        value = Math.min(MAX, Math.max(MIN, Math.round(v / STEP) * STEP));
        cells.forEach(function (cell, i) {
            var left = value - i;
            cell.setAttribute('data-fill', left >= 1 ? 'full' : left >= 0.5 ? 'half' : 'empty');
        });
        stars.setAttribute('aria-valuenow', String(value));
        stars.setAttribute('aria-valuetext', describe(value));
        stars.setAttribute('data-value', String(value));
        if (readout) readout.textContent = value.toFixed(1);
    }

    // Left half of a star is a half step, right half the whole star, as in the app.
    function valueAt(clientX) {
        var first = cells[0].getBoundingClientRect();
        var last = cells[cells.length - 1].getBoundingClientRect();
        var x = clientX - first.left;
        var width = last.right - first.left;
        var pitch = width / cells.length;
        var index = Math.floor(x / pitch);
        var within = (x - index * pitch) / first.width;
        return index + (within <= 0.5 ? 0.5 : 1);
    }

    stars.removeAttribute('aria-label');
    stars.setAttribute('role', 'slider');
    stars.setAttribute('tabindex', '0');
    stars.setAttribute('aria-label', 'Star rating example');
    stars.setAttribute('aria-valuemin', String(MIN));
    stars.setAttribute('aria-valuemax', String(MAX));
    stars.classList.add('is-live');
    if (readout) readout.hidden = false;
    render(value);

    var dragging = null;
    stars.addEventListener('pointerdown', function (event) {
        if (event.button !== 0) return;
        dragging = event.pointerId;
        // Touch keeps vertical scrolling (touch-action: pan-y), so only capture mouse and pen.
        if (event.pointerType !== 'touch' && stars.setPointerCapture) stars.setPointerCapture(event.pointerId);
        render(valueAt(event.clientX));
    });
    stars.addEventListener('pointermove', function (event) {
        if (dragging === event.pointerId) render(valueAt(event.clientX));
    });
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(function (type) {
        stars.addEventListener(type, function () { dragging = null; });
    });

    stars.addEventListener('keydown', function (event) {
        var next = {
            ArrowRight: value + STEP, ArrowUp: value + STEP,
            ArrowLeft: value - STEP, ArrowDown: value - STEP,
            PageUp: value + 1, PageDown: value - 1,
            Home: MIN, End: MAX
        }[event.key];
        if (next === undefined && /^[1-5]$/.test(event.key)) next = Number(event.key);
        if (next === undefined) return;
        event.preventDefault();
        render(next);
    });
})();
