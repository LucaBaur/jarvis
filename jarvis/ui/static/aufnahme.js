// Sprachaufnahme im Browser.
//
// Bewusst NICHT mit MediaRecorder: der liefert webm/opus, und auf dem
// Homeserver ist kein ffmpeg installiert, um das zu dekodieren. Stattdessen
// nimmt die Web-Audio-API rohes PCM ab, und daraus wird hier eine WAV-Datei
// gebaut - genau das, was Whisper direkt verarbeiten kann.
//
// Die Abtastrate ist egal, wyoming-faster-whisper rechnet selbst um. Deshalb
// wird 16000 Hz nur gewuenscht und nicht erzwungen; Safari etwa gibt die
// Geraeterate zurueck.

(function () {
  "use strict";

  var WUNSCHRATE = 16000;
  var MAX_SEKUNDEN = 300;

  function wavAus(bloecke, laenge, rate) {
    var puffer = new ArrayBuffer(44 + laenge * 2);
    var sicht = new DataView(puffer);

    function text(pos, s) {
      for (var i = 0; i < s.length; i++) sicht.setUint8(pos + i, s.charCodeAt(i));
    }

    text(0, "RIFF");
    sicht.setUint32(4, 36 + laenge * 2, true);
    text(8, "WAVEfmt ");
    sicht.setUint32(16, 16, true);        // Groesse des fmt-Blocks
    sicht.setUint16(20, 1, true);         // 1 = unkomprimiertes PCM
    sicht.setUint16(22, 1, true);         // Mono
    sicht.setUint32(24, rate, true);
    sicht.setUint32(28, rate * 2, true);  // Bytes pro Sekunde
    sicht.setUint16(32, 2, true);         // Bytes pro Rahmen
    sicht.setUint16(34, 16, true);        // bit pro Abtastwert
    text(36, "data");
    sicht.setUint32(40, laenge * 2, true);

    var pos = 44;
    bloecke.forEach(function (block) {
      for (var i = 0; i < block.length; i++) {
        // Float -1..1 auf 16-Bit-Ganzzahl, mit Begrenzung gegen Uebersteuern.
        var wert = Math.max(-1, Math.min(1, block[i]));
        sicht.setInt16(pos, wert < 0 ? wert * 0x8000 : wert * 0x7fff, true);
        pos += 2;
      }
    });

    return new Blob([puffer], { type: "audio/wav" });
  }

  function Aufnehmer(beiAenderung) {
    var kontext = null, quelle = null, knoten = null, spur = null;
    var bloecke = [], laenge = 0, rate = WUNSCHRATE, laeuft = false;

    this.laeuft = function () { return laeuft; };

    this.start = function () {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        return Promise.reject(new Error(
          "Dieser Browser gibt kein Mikrofon frei. Ueber HTTP geht das nur bei localhost."
        ));
      }
      return navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }
      }).then(function (strom) {
        spur = strom;
        var KontextKlasse = window.AudioContext || window.webkitAudioContext;
        try {
          kontext = new KontextKlasse({ sampleRate: WUNSCHRATE });
        } catch (e) {
          kontext = new KontextKlasse();   // Safari nimmt die Wunschrate nicht
        }
        rate = kontext.sampleRate;

        quelle = kontext.createMediaStreamSource(strom);
        // ScriptProcessorNode gilt als veraltet, laeuft aber ueberall. Ein
        // AudioWorklet braucht eine eigene Datei und bringt hier nichts.
        knoten = kontext.createScriptProcessor(4096, 1, 1);
        knoten.onaudioprocess = function (ereignis) {
          if (!laeuft) return;
          var eingang = ereignis.inputBuffer.getChannelData(0);
          bloecke.push(new Float32Array(eingang));
          laenge += eingang.length;
          if (laenge / rate > MAX_SEKUNDEN) this.stop();
          if (beiAenderung) beiAenderung(laenge / rate);
        }.bind(this);

        quelle.connect(knoten);
        knoten.connect(kontext.destination);
        bloecke = []; laenge = 0; laeuft = true;
        if (beiAenderung) beiAenderung(0);
      }.bind(this));
    };

    this.stop = function () {
      laeuft = false;
      if (knoten) { knoten.disconnect(); knoten.onaudioprocess = null; }
      if (quelle) quelle.disconnect();
      if (spur) spur.getTracks().forEach(function (t) { t.stop(); });
      if (kontext) kontext.close();
      knoten = quelle = spur = kontext = null;
      return laenge ? wavAus(bloecke, laenge, rate) : null;
    };
  }

  window.JarvisAufnahme = { Aufnehmer: Aufnehmer };
})();
