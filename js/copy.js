// Copy buttons on the AI import page. Each button names the element whose text it copies.
// Without JavaScript or the Clipboard API the buttons stay hidden and the prompts can be
// selected and copied by hand.
(function () {
  if (!navigator.clipboard) return;
  document.querySelectorAll('[data-copy]').forEach(function (button) {
    var source = document.getElementById(button.getAttribute('data-copy'));
    if (!source) return;
    var label = button.textContent;
    var timer;
    button.hidden = false;
    button.addEventListener('click', function () {
      navigator.clipboard.writeText(source.textContent.trim()).then(function () {
        button.textContent = 'Copied';
        clearTimeout(timer);
        timer = setTimeout(function () { button.textContent = label; }, 2000);
      }, function () {
        button.textContent = 'Select the text to copy';
      });
    });
  });
})();
