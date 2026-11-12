// The "Suggest a place" form on guides/suggest/. It checks the fields in the browser, then
// sends them as form data to workers/place-suggestions/ on this site (same origin), which
// checks them again, confirms the Cloudflare Turnstile check and files the suggestion.
// Nothing is kept in the browser.
(function () {
  var form = document.getElementById('suggestForm');
  if (!form) return;
  var thanks = document.getElementById('suggestThanks');
  var status = document.getElementById('formStatus');
  var submit = form.querySelector('button[type="submit"]');
  var city = form.elements.city;
  var otherCityField = document.getElementById('otherCityField');
  var creditField = document.getElementById('creditNameField');
  var creditLabel = document.getElementById('creditNameLabel');

  function credit() { return form.querySelector('input[name="credit"]:checked').value; }

  function sync() {
    otherCityField.hidden = city.value !== 'other';
    form.elements.otherCity.required = city.value === 'other';
    var choice = credit();
    creditField.hidden = choice === 'none';
    form.elements.creditName.required = choice !== 'none';
    if (choice !== 'none') creditLabel.textContent = creditLabel.getAttribute('data-label-' + choice);
  }
  form.addEventListener('change', sync);
  sync();

  function setError(name, message) {
    var input = form.elements[name];
    var error = document.getElementById(name + '-error');
    if (!input || !error) return false;
    error.textContent = message || '';
    error.hidden = !message;
    if (message) input.setAttribute('aria-invalid', 'true'); else input.removeAttribute('aria-invalid');
    return true;
  }

  // A field's error goes as soon as it's edited.
  ['input', 'change'].forEach(function (type) {
    form.addEventListener(type, function (event) {
      if (event.target.name) setError(event.target.name, '');
      if (event.target.name === 'link') setError('address', '');
    });
  });

  function clearErrors() {
    form.querySelectorAll('.field-error').forEach(function (error) {
      setError(error.id.replace(/-error$/, ''), '');
    });
  }

  // The same rules as the Worker's validate(), so most mistakes never leave the page.
  function check() {
    var errors = {};
    var value = function (name) { return (form.elements[name].value || '').trim(); };
    if (!city.value) errors.city = 'Pick the city, or type it in.';
    if (city.value === 'other' && !value('otherCity')) errors.otherCity = 'Type the city.';
    if (!value('place')) errors.place = 'Add the name of the place.';
    if (!form.elements.kind.value) errors.kind = 'Pick what kind of place it is.';
    if (!value('address') && !value('link')) errors.address = 'Add the address or a link to the place, so we find the right one.';
    if (value('link') && !/^(https?:\/\/)?[^\s\/]+\.[^\s]+$/i.test(value('link'))) errors.link = "That doesn't look like a web link.";
    if (!value('tip')) errors.tip = 'Tell us what to get, see or do there.';
    if (value('why').length < 10) errors.why = 'Tell us a little more about why it belongs.';
    if (!form.elements.lastWent.value) errors.lastWent = 'Pick when you last went.';
    if (credit() !== 'none' && !value('creditName')) errors.creditName = 'Add the name to thank, or choose not to be named.';
    return errors;
  }

  function showErrors(errors) {
    var first = null;
    Object.keys(errors).forEach(function (name) {
      if (setError(name, errors[name]) && !first) first = form.elements[name];
    });
    if (first) first.focus();
    return !!first;
  }

  function resetTurnstile() {
    if (window.turnstile) window.turnstile.reset();
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    clearErrors();
    status.textContent = '';
    if (showErrors(check())) {
      status.textContent = 'Check the highlighted fields.';
      return;
    }
    var data = new URLSearchParams(new FormData(form));
    if (!data.get('cf-turnstile-response')) {
      status.textContent = 'Please wait for the check above to finish, then send again.';
      return;
    }
    submit.disabled = true;
    status.textContent = 'Sending…';
    fetch(form.getAttribute('action'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: data.toString(),
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (response.ok && body.ok) {
          form.hidden = true;
          thanks.hidden = false;
          thanks.focus();
          return;
        }
        status.textContent = body.error || 'Something went wrong. Please try again later.';
        if (body.fields) showErrors(body.fields);
        resetTurnstile();
      });
    }, function () {
      status.textContent = "We couldn't reach the server. Check your connection and try again.";
      resetTurnstile();
    }).then(function () {
      submit.disabled = false;
    });
  });

  document.getElementById('suggestAnother').addEventListener('click', function () {
    form.reset();
    clearErrors();
    sync();
    status.textContent = '';
    resetTurnstile();
    thanks.hidden = true;
    form.hidden = false;
    form.elements.place.focus();
  });
})();
