// Search and type filters on the Field Guides list (guides/). tools/guides.py renders the
// controls hidden and gives each guide card data-kind and data-search (its title, summary,
// region and place names, lowercased and without accents). Without this script every guide
// shows, as before.
(function () {
  var controls = document.getElementById('guideControls');
  var list = document.getElementById('guideList');
  if (!controls || !list) return;
  var search = document.getElementById('guideSearch');
  var count = document.getElementById('guideCount');
  var noMatch = document.getElementById('guideNoMatch');
  var buttons = Array.prototype.slice.call(controls.querySelectorAll('.filter-chip'));
  var cards = Array.prototype.slice.call(list.querySelectorAll('.prompt-card[data-search]'));
  var groups = Array.prototype.slice.call(list.querySelectorAll('.guide-group'));
  var kind = '';

  // The same folding as search_text() in tools/guides.py: lowercase, no accents.
  function fold(text) {
    return text.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  }

  var lastMessage = null;
  function say(shown) {
    var message = shown ? shown + (shown === 1 ? ' guide' : ' guides') : 'none';
    if (message === lastMessage) return;
    lastMessage = message;
    if (shown) {
      count.textContent = message;
    } else {
      count.textContent = '';
      count.appendChild(noMatch.content.cloneNode(true));
    }
  }

  function apply() {
    var words = fold(search.value).split(/\s+/).filter(Boolean);
    var shown = 0;
    cards.forEach(function (card) {
      var text = card.getAttribute('data-search');
      var match = (!kind || card.getAttribute('data-kind') === kind) &&
        words.every(function (word) { return text.indexOf(word) !== -1; });
      card.hidden = !match;
      if (match) shown++;
    });
    groups.forEach(function (group) {
      group.hidden = !group.querySelector('.prompt-card:not([hidden])');
    });
    say(shown);
  }

  buttons.forEach(function (button) {
    button.addEventListener('click', function () {
      kind = button.getAttribute('data-kind');
      buttons.forEach(function (other) {
        other.setAttribute('aria-pressed', other === button ? 'true' : 'false');
      });
      apply();
    });
  });
  search.addEventListener('input', apply);
  controls.hidden = false;
  apply();
})();
