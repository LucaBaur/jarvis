// Die gesamte Logik der Oberfläche. Kein Framework, kein Build-Schritt:
// ein Formular abschicken und einen Ausschnitt der Seite nachladen.

(function () {
  "use strict";

  var TAKT_MS = 3000;

  // Jobs mit diesen Status ändern sich noch, dafür lohnt das Nachladen.
  function seiteIstLebendig(html) {
    return /class="status (queued|assigned|running)"/.test(html);
  }

  function nachladen(ziel) {
    var quelle = ziel.getAttribute("data-quelle");
    if (!quelle) return;

    fetch(quelle, { headers: { "X-Partial": "1" } })
      .then(function (antwort) {
        if (!antwort.ok) throw new Error(antwort.status);
        return antwort.text();
      })
      .then(function (html) {
        // Nicht anfassen, während jemand etwas markiert hat.
        var auswahl = window.getSelection();
        if (auswahl && auswahl.toString().length > 0) return;
        if (html !== ziel.innerHTML) ziel.innerHTML = html;
        if (!seiteIstLebendig(html) && ziel.id === "jobdetail") stoppen(ziel);
      })
      .catch(function () {
        // Server kurz weg. Beim nächsten Takt wieder versuchen.
      });
  }

  var takte = [];

  function stoppen(ziel) {
    takte = takte.filter(function (eintrag) {
      if (eintrag.ziel !== ziel) return true;
      clearInterval(eintrag.id);
      return false;
    });
  }

  function starten() {
    document.querySelectorAll("[data-quelle]").forEach(function (ziel) {
      takte.push({ ziel: ziel, id: setInterval(nachladen.bind(null, ziel), TAKT_MS) });
    });
  }

  function formularAbschicken(formular) {
    var daten = new FormData(formular);
    var body = {
      prompt: daten.get("prompt"),
      priority: Number(daten.get("priority") || 50),
      window: daten.get("window") || "any",
      min_vram_mb: Number(daten.get("min_vram_mb") || 0)
    };
    var modell = (daten.get("model") || "").trim();
    if (modell) body.model = modell;

    var fehlerfeld = document.getElementById("job-fehler");
    var knopf = formular.querySelector("button[type=submit]");
    knopf.disabled = true;

    fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    })
      .then(function (antwort) {
        if (antwort.status === 401) throw new Error("Nicht angemeldet. Seite neu laden.");
        if (!antwort.ok) throw new Error("Der Server hat den Auftrag abgelehnt (" + antwort.status + ").");
        return antwort.json();
      })
      .then(function (job) {
        formular.reset();
        fehlerfeld.hidden = true;
        window.location.href = "/jobs/" + job.id;
      })
      .catch(function (fehler) {
        fehlerfeld.textContent = fehler.message;
        fehlerfeld.hidden = false;
      })
      .finally(function () {
        knopf.disabled = false;
      });
  }

  document.addEventListener("submit", function (ereignis) {
    var formular = ereignis.target;

    if (formular.id === "neuer-job") {
      ereignis.preventDefault();
      formularAbschicken(formular);
      return;
    }

    if (formular.hasAttribute("data-abbrechen")) {
      ereignis.preventDefault();
      fetch(formular.action, { method: "POST" }).then(function () {
        window.location.reload();
      });
    }
  });

  // --- Sprachaufnahme ---------------------------------------------------

  function meldung(text, istFehler) {
    var feld = document.getElementById(istFehler ? "job-fehler" : "job-hinweis");
    feld.textContent = text;
    feld.hidden = !text;
    if (istFehler) document.getElementById("job-hinweis").hidden = true;
    else document.getElementById("job-fehler").hidden = true;
  }

  function hochladen(blob) {
    var formular = document.getElementById("neuer-job");
    var daten = new FormData();
    daten.append("audio", blob, "aufnahme.wav");
    daten.append("priority", formular.priority.value || "50");
    daten.append("window", formular.window.value || "any");
    if (formular.model.value.trim()) daten.append("model", formular.model.value.trim());

    meldung("Wird erkannt. Das dauert ungefähr so lange wie die Aufnahme.", false);

    return fetch("/api/jobs/audio", { method: "POST", body: daten })
      .then(function (antwort) {
        return antwort.json().then(function (koerper) {
          if (!antwort.ok) throw new Error(koerper.detail || "Upload fehlgeschlagen.");
          return koerper;
        });
      })
      .then(function (job) { window.location.href = "/jobs/" + job.id; })
      .catch(function (fehler) { meldung(fehler.message, true); });
  }

  function aufnahmeEinrichten() {
    var knopf = document.getElementById("aufnahme");
    if (!knopf || !window.JarvisAufnahme) return;

    var beschriftung = document.getElementById("aufnahme-text");
    var aufnehmer = new window.JarvisAufnahme.Aufnehmer(function (sekunden) {
      beschriftung.textContent = "Stopp (" + Math.floor(sekunden) + " s)";
    });

    knopf.addEventListener("click", function () {
      if (aufnehmer.laeuft()) {
        knopf.classList.remove("aktiv");
        beschriftung.textContent = "Sprechen";
        var blob = aufnehmer.stop();
        if (!blob) { meldung("Es wurde nichts aufgenommen.", true); return; }
        knopf.disabled = true;
        hochladen(blob).finally(function () { knopf.disabled = false; });
        return;
      }

      meldung("", false);
      aufnehmer.start()
        .then(function () { knopf.classList.add("aktiv"); })
        .catch(function (fehler) {
          meldung(
            fehler && fehler.name === "NotAllowedError"
              ? "Kein Zugriff aufs Mikrofon. Im Browser erlauben und erneut versuchen."
              : (fehler.message || "Aufnahme nicht möglich."),
            true
          );
        });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    starten();
    aufnahmeEinrichten();
  });
})();
