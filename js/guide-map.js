// The map on a guide page (guides/<slug>/, built by tools/guides.py).
//
// Loads Apple's MapKit JS only on guide pages, with the token the Pages deploy writes to
// guides/mapkit-token.json, then pins every place in guide.json, coloured by section to match
// the legend and the section headings. Until all of that has worked the map stays hidden, so
// without JavaScript, without a token (a local preview) or without Apple Maps, the page is
// simply the list.
(function () {
    "use strict";

    var MAPKIT = "https://cdn.apple-mapkit.com/mk/5.x.x/mapkit.core.js";
    // The guide-key-0…3 colours in site.css, light then dark.
    var COLOURS = {
        light: ["#8C3B5E", "#9C6A12", "#3F6B4A", "#2F6F8F"],
        dark: ["#E295B6", "#D9A441", "#8DBB97", "#8EC3D9"]
    };

    function isDark() {
        var theme = document.documentElement.getAttribute("data-theme");
        if (theme) return theme === "dark";
        return window.matchMedia("(prefers-color-scheme: dark)").matches;
    }

    function getJSON(url) {
        return fetch(url, { credentials: "omit" }).then(function (response) {
            if (!response.ok) throw new Error(url + ": " + response.status);
            return response.json();
        });
    }

    function loadMapKit() {
        return new Promise(function (resolve, reject) {
            window.placemoirMapKitLoaded = resolve;
            var script = document.createElement("script");
            script.src = MAPKIT;
            script.crossOrigin = "anonymous";
            script.async = true;
            script.setAttribute("data-callback", "placemoirMapKitLoaded");
            script.setAttribute("data-libraries", "map,annotations");
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }

    function draw(figure, guide) {
        var mapkit = window.mapkit;
        var sections = guide.guide.sections || [];
        var map = new mapkit.Map(figure.querySelector(".guide-map-canvas"), {
            colorScheme: isDark() ? mapkit.Map.ColorSchemes.Dark : mapkit.Map.ColorSchemes.Light,
            pointOfInterestFilter: mapkit.PointOfInterestFilter.excludingAllCategories,
            showsMapTypeControl: false,
            isRotationEnabled: false
        });
        var pins = guide.places.map(function (place) {
            var index = Math.max(0, sections.indexOf(place.section)) % 4;
            var pin = new mapkit.MarkerAnnotation(new mapkit.Coordinate(place.latitude, place.longitude), {
                title: place.name,
                subtitle: place.section,
                glyphText: (place.section || "").charAt(0),
                accessibilityLabel: place.name + ", " + place.section
            });
            pin.sectionIndex = index;
            pin.color = COLOURS[isDark() ? "dark" : "light"][index];
            return pin;
        });
        map.showItems(pins, { padding: new mapkit.Padding(48, 32, 32, 32) });

        // Picking a pin brings its card into view and marks it.
        map.addEventListener("select", function (event) {
            var place = guide.places[pins.indexOf(event.annotation)];
            var card = place && document.getElementById(place.id.split("/").pop());
            if (!card) return;
            document.querySelectorAll(".guide-place.is-picked").forEach(function (other) {
                other.classList.remove("is-picked");
            });
            card.classList.add("is-picked");
        });

        // Follow the theme switch and the system setting.
        function retheme() {
            var dark = isDark();
            map.colorScheme = dark ? mapkit.Map.ColorSchemes.Dark : mapkit.Map.ColorSchemes.Light;
            pins.forEach(function (pin) { pin.color = COLOURS[dark ? "dark" : "light"][pin.sectionIndex]; });
        }
        new MutationObserver(retheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
        window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", retheme);
    }

    function start() {
        var figure = document.getElementById("guide-map");
        if (!figure || !window.fetch || !window.Promise) return;
        Promise.all([getJSON("../mapkit-token.json"), getJSON("guide.json")])
            .then(function (results) {
                var token = results[0].token, guide = results[1];
                return loadMapKit().then(function () {
                    window.mapkit.init({ authorizationCallback: function (done) { done(token); } });
                    figure.hidden = false;
                    draw(figure, guide);
                });
            })
            .catch(function (error) {
                figure.hidden = true;
                if (window.console) console.info("Guide map not shown:", error && error.message ? error.message : error);
            });
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
})();
